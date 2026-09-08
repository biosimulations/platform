"""HTTP contract tests for owned run summaries."""

from collections.abc import Callable, Iterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.dependencies import get_http_client
from biosim_server.summaries.mapping import map_run_summary
from tests.summaries.test_mapping import payload


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_http_client, None)


def summary_client(handler: Callable[[httpx.Request], httpx.Response]) -> tuple[AsyncClient, AsyncClient]:
    upstream = AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test")
    app.dependency_overrides[get_http_client] = lambda: upstream
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://platform.test"), upstream


@pytest.mark.anyio
async def test_owned_json_credentials_query_and_headers() -> None:
    seen: list[httpx.Request] = []
    raw = payload("run")
    raw["unknownFutureField"] = "discard"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=raw, headers={
            "ETag": '"legacy"', "Vary": "Accept-Encoding, Origin", "Location": "/other",
            "Set-Cookie": "session=secret", "Cache-Control": "public, max-age=600",
        })

    caller, upstream = summary_client(handler)
    async with caller, upstream:
        response = await caller.get(
            "/runs/example/summary?includeData=true&a=one&a=two",
            headers={"Authorization": "Bearer secret", "Cookie": "session=caller"},
        )
    assert response.status_code == 200
    assert response.json() == map_run_summary(raw).model_dump(by_alias=True)
    assert len(seen) == 1
    assert seen[0].url.raw_path == b"/runs/example/summary"
    assert "authorization" not in seen[0].headers
    assert "cookie" not in seen[0].headers
    assert response.headers["content-type"] == "application/json"
    for header in ["etag", "vary", "location", "set-cookie", "cache-control"]:
        assert header not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize(("status", "expected"), [(404, 404), (429, 429), (500, 502), (302, 502), (204, 502)])
async def test_upstream_errors_are_sanitized(status: int, expected: int) -> None:
    caller, upstream = summary_client(lambda request: httpx.Response(status, text="secret upstream body"))
    async with caller, upstream:
        response = await caller.get("/runs/example/summary")
    assert response.status_code == expected
    assert "secret" not in response.text
    if status == 404:
        assert response.json() == {"detail": "Not Found"}


@pytest.mark.anyio
async def test_mapping_failure_is_502() -> None:
    raw = payload("run")
    del raw["id"]
    caller, upstream = summary_client(lambda request: httpx.Response(200, json=raw))
    async with caller, upstream:
        response = await caller.get("/runs/example/summary")
    assert response.status_code == 502
    assert response.json() == {"detail": "The upstream service returned an unexpected run summary."}


@pytest.mark.anyio
async def test_timeout_is_504() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret", request=request)

    caller, upstream = summary_client(handler)
    async with caller, upstream:
        response = await caller.get("/runs/example/summary")
    assert response.status_code == 504
    assert "secret" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("caller_segment", "expected_segment"),
    [
        ("a%25b", b"a%25b"),
        ("a.b", b"a.b"),
        ("5f2a.", b"5f2a."),
    ],
)
async def test_run_id_stays_one_encoded_path_segment(
    caller_segment: str, expected_segment: bytes
) -> None:
    """A caller-supplied id is re-quoted into exactly one segment, not double-encoded.

    The dotted cases pin the boundary of the dot-only rejection below: only a
    whole "." / ".." segment is refused, never an id that merely contains one.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload())

    caller, upstream = summary_client(handler)
    async with caller, upstream:
        response = await caller.get(f"/runs/{caller_segment}/summary")

    assert response.status_code == 200
    assert len(seen) == 1
    assert seen[0].url.raw_path == b"/runs/" + expected_segment + b"/summary"


@pytest.mark.anyio
@pytest.mark.parametrize("caller_segment", ["%2E", "%2E%2E", ".%2E"])
async def test_dot_only_run_id_is_rejected_before_any_upstream_call(
    caller_segment: str,
) -> None:
    """A decoded "." or ".." id is refused before fetching upstream.

    The run route inherits this from ``upstream_url``; pinning it here as well
    keeps the contract from regressing if only one router is ever touched. See
    the project-side twin for why the encoded forms are the ones that matter.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload())

    caller, upstream = summary_client(handler)
    async with caller, upstream:
        response = await caller.get(f"/runs/{caller_segment}/summary")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert seen == []


def test_summary_openapi_contracts() -> None:
    schema = app.openapi()
    schemas = schema["components"]["schemas"]
    assert "/simulations/run" in schema["paths"]
    for path, model, operation in [
        ("/projects/{project_id}/summary", "ProjectSummary", "get-project-summary"),
        ("/runs/{run_id}/summary", "RunSummary", "get-run-summary"),
    ]:
        route = schema["paths"][path]["get"]
        assert route["operationId"] == operation
        assert not route.get("security")
        assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model}"
        }
    expected = {
        "ProjectSummary": {"id", "created", "updated", "simulationRun"},
        "RunSummary": {"id", "name", "run", "metadata"},
        "RunExecution": {"simulator", "projectSize", "resultsSize"},
        "RunMetadataSummary": {"abstract", "description", "thumbnails", "creators", "keywords", "citations", "encodes"},
        "LabeledIdentifier": {"uri", "label"},
        "SimulatorSummary": {"name", "version"},
    }
    for model, fields in expected.items():
        assert set(schemas[model]["properties"]) == fields
    assert not any(name.startswith("_Upstream") for name in schemas)
    assert schemas["ProjectSummary"]["properties"]["simulationRun"] == {"$ref": "#/components/schemas/RunSummary"}
