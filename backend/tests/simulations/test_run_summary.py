"""HTTP contract tests for the run-summary passthrough proxy."""

import gzip
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.dependencies import get_http_client

Handler = Callable[[httpx.Request], httpx.Response]

# A genuine biosimulations.org /runs/{id}/summary capture. Held as raw bytes so
# the fidelity test pins passthrough against real upstream output, including its
# exact whitespace and key order, rather than against a re-serialized stub.
FIXTURE = Path(__file__).parents[1] / "fixtures" / "local_data" / "run_summary_response.json"
RAW_BODY = FIXTURE.read_bytes()
RUN_SUMMARY: dict[str, Any] = json.loads(RAW_BODY)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_http_client, None)


def proxy_client(handler: Handler) -> tuple[AsyncClient, httpx.AsyncClient]:
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://upstream.test"
    )
    app.dependency_overrides[get_http_client] = lambda: upstream
    return (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://platform.test"),
        upstream,
    )


@pytest.mark.anyio
async def test_run_summary_fidelity_query_headers_credentials_and_contract() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=gzip.compress(RAW_BODY),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "ETag": '"run-v1"',
                "Set-Cookie": "session=secret",
                "X-Powered-By": "legacy",
                "Connection": "keep-alive",
                "Keep-Alive": "timeout=5",
                "Content-Encoding": "gzip",
                "Content-Length": "99999",
            },
        )

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get(
            "/runs/61fea483f499ccf25faafc4d/summary"
            "?includeData=true&output=a&output=b&includeData=false",
            headers={"Authorization": "Bearer secret", "Cookie": "caller=session"},
        )

    assert response.status_code == 200
    assert response.content == RAW_BODY
    assert len(seen) == 1
    assert seen[0].url.raw_path == (
        b"/runs/61fea483f499ccf25faafc4d/summary"
        b"?includeData=true&output=a&output=b&includeData=false"
    )
    assert "authorization" not in seen[0].headers
    assert "cookie" not in seen[0].headers
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    # httpx decoded the gzip body, so the strong upstream tag is weakened.
    assert response.headers["etag"] == 'W/"run-v1"'
    for blocked in (
        "set-cookie",
        "x-powered-by",
        "connection",
        "keep-alive",
        "content-encoding",
    ):
        assert blocked not in response.headers
    assert response.headers["content-length"] == str(len(RAW_BODY))

    body = response.json()
    assert body == RUN_SUMMARY
    assert body["id"] == "61fea483f499ccf25faafc4d"
    assert body["run"]["status"] == "SUCCEEDED"
    assert body["run"]["simulator"]["name"] == "BoolNet"
    assert body["metadata"][0]["creators"][0]["label"] == "D. J. Irons"
    assert body["metadata"][0]["thumbnails"][0] == "Figure2.jpg"
    assert body["tasks"][0]["id"] == "task_wt"
    assert body["outputs"][0]["uri"] == "simulation.sedml/report_wt"


@pytest.mark.anyio
@pytest.mark.parametrize("downstream_status", [204, 302, 400, 404, 429])
async def test_run_summary_mirrors_2xx_3xx_and_4xx(downstream_status: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        body = b"" if downstream_status == 204 else b"downstream-body"
        return httpx.Response(
            downstream_status,
            content=body,
            headers={"Location": "/runs/other/summary"},
        )

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/runs/run-1/summary")

    assert response.status_code == downstream_status
    assert response.content == (b"" if downstream_status == 204 else b"downstream-body")
    assert response.headers["location"] == "/runs/other/summary"


@pytest.mark.anyio
async def test_invalid_run_upstream_500_becomes_sanitized_502() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="legacy host internal.example:27017")

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/runs/not-a-real-id/summary")

    assert response.status_code == 502
    assert "internal.example" not in response.text
    assert "27017" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(httpx.ReadTimeout("slow"), 504), (httpx.ConnectError("internal:443"), 502)],
)
async def test_run_summary_transport_failures(
    error: httpx.RequestError, expected_status: int
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        error.request = request
        raise error

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/runs/run-1/summary")

    assert response.status_code == expected_status
    assert "internal:443" not in response.text


@pytest.mark.anyio
async def test_already_weak_etag_is_not_prefixed_twice() -> None:
    """The run route shares ``_safe_response_headers``; pin the no-W/W/ rule here too."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(RAW_BODY),
            headers={"Content-Encoding": "gzip", "ETag": 'W/"run-v1"'},
        )

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/runs/61fea483f499ccf25faafc4d/summary")

    assert response.status_code == 200
    assert response.headers["etag"] == 'W/"run-v1"'
    assert response.content == RAW_BODY


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
        return httpx.Response(200, content=b"ok")

    caller, upstream = proxy_client(handler)
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
    """A decoded "." or ".." id is refused rather than proxied.

    The run route inherits this from ``upstream_url``; pinning it here as well
    keeps the contract from regressing if only one router is ever touched. See
    the project-side twin for why the encoded forms are the ones that matter.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get(f"/runs/{caller_segment}/summary")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert seen == []


def test_summary_routes_and_simulation_submission_are_registered() -> None:
    schema = app.openapi()
    assert "/projects/{project_id}/summary" in schema["paths"]
    assert "/runs/{run_id}/summary" in schema["paths"]
    assert "/simulations/run" in schema["paths"]
    schemas = schema.get("components", {}).get("schemas", {})
    assert "RunSummary" not in schemas
    assert "ProjectSummaryResponse" not in schemas

    parameters = schema["paths"]["/runs/{run_id}/summary"]["get"]["parameters"]
    assert [parameter["name"] for parameter in parameters] == ["run_id"]


@pytest.mark.anyio
async def test_unknown_upstream_fields_are_returned_unprojected() -> None:
    """Fields absent from any local model still reach the caller byte-for-byte.

    The real capture above has no such field, so this uses a synthetic body to
    keep the guarantee that the route neither validates nor re-serializes JSON.
    """
    synthetic = b'{"id": "example", "unknownFutureField": {"future": true}}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=synthetic, headers={"Content-Type": "application/json"}
        )

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/runs/example/summary")

    assert response.status_code == 200
    assert response.content == synthetic
    assert response.json()["unknownFutureField"] == {"future": True}
