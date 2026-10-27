"""HTTP contract tests for owned project summaries."""

from collections.abc import Callable, Iterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.dependencies import get_http_client
from biosim_server.summaries.mapping import map_project_summary
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
    raw = payload("project")
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
            "/projects/example/summary?includeData=true&a=one&a=two",
            headers={"Authorization": "Bearer secret", "Cookie": "session=caller"},
        )
    assert response.status_code == 200
    assert response.json() == map_project_summary(raw).model_dump(by_alias=True)
    assert len(seen) == 1
    assert seen[0].url.raw_path == b"/projects/example/summary"
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
        response = await caller.get("/projects/example/summary")
    assert response.status_code == expected
    assert "secret" not in response.text
    if status == 404:
        assert response.json() == {"detail": "Not Found"}


@pytest.mark.anyio
async def test_mapping_failure_is_502() -> None:
    raw = payload("project")
    del raw["id"]
    caller, upstream = summary_client(lambda request: httpx.Response(200, json=raw))
    async with caller, upstream:
        response = await caller.get("/projects/example/summary")
    assert response.status_code == 502
    assert response.json() == {"detail": "The upstream service returned an unexpected project summary."}


@pytest.mark.anyio
async def test_timeout_is_504() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret", request=request)

    caller, upstream = summary_client(handler)
    async with caller, upstream:
        response = await caller.get("/projects/example/summary")
    assert response.status_code == 504
    assert "secret" not in response.text


@pytest.mark.anyio
async def test_project_summary_route_does_not_shadow_stats() -> None:
    from unittest.mock import AsyncMock, patch

    projects_db = AsyncMock()
    projects_db.query_project_stats.return_value = []
    with patch(
        "biosim_server.projects.router.get_project_database_service",
        return_value=projects_db,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://platform.test"
        ) as caller:
            response = await caller.get("/projects/stats")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("caller_segment", "expected_segment"),
    [
        ("a%25b", b"a%25b"),
        ("a%20b", b"a%20b"),
        ("a%23b", b"a%23b"),
        # Periods are only special as a whole segment. These pin the rejection
        # boundary below, so a future guard cannot widen into every dotted id.
        ("a.b", b"a.b"),
        ("5f2a.", b"5f2a."),
        ("...", b"..."),
    ],
)
async def test_project_id_stays_one_encoded_path_segment(
    caller_segment: str, expected_segment: bytes
) -> None:
    """A caller-supplied id is re-quoted into exactly one segment, not double-encoded."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload("project"))

    caller, upstream = summary_client(handler)
    async with caller, upstream:
        response = await caller.get(f"/projects/{caller_segment}/summary")

    assert response.status_code == 200
    assert len(seen) == 1
    assert seen[0].url.raw_path == b"/projects/" + expected_segment + b"/summary"


@pytest.mark.anyio
async def test_encoded_slash_in_id_is_rejected_before_any_upstream_call() -> None:
    """An encoded slash never reaches the upstream client.

    This is primarily a Starlette/ASGI routing guarantee: the raw path is
    unquoted before matching, so ``%2F`` splits into extra path segments and no
    route matches. It is deliberately not enforced inside the JSON helper -- do
    not relocate this guarantee there.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload("project"))

    caller, upstream = summary_client(handler)
    async with caller, upstream:
        response = await caller.get("/projects/..%2Fruns%2Fsecret%3Fx=1/summary")

    assert response.status_code == 404
    assert seen == []


@pytest.mark.anyio
@pytest.mark.parametrize("caller_segment", ["%2E", "%2E%2E", ".%2E"])
async def test_dot_only_project_id_is_rejected_before_any_upstream_call(
    caller_segment: str,
) -> None:
    """A decoded "." or ".." id is refused before fetching upstream.

    ``quote`` leaves "." unescaped, so such an id would build a real dot
    segment that httpx resolves against its base_url -- ``/projects/../summary``
    became an upstream ``/summary``, escaping the id segment entirely.

    The encoded forms are what the regression needs: a literal ``..`` is
    normalized away by the caller's own HTTP client before Starlette ever sees
    it, while ``%2E%2E`` survives routing and reaches the vulnerable path.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload("project"))

    caller, upstream = summary_client(handler)
    async with caller, upstream:
        response = await caller.get(f"/projects/{caller_segment}/summary")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert seen == []


@pytest.mark.anyio
async def test_platform_cors_owns_origin_variance() -> None:
    caller, upstream = summary_client(lambda request: httpx.Response(
        200, json=payload("project"), headers={"Vary": "Accept-Encoding, Origin"}
    ))
    async with caller, upstream:
        response = await caller.get(
            "/projects/example/summary", headers={"Origin": "http://localhost:4200"}
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:4200"
    assert response.headers["vary"] == "Origin"
