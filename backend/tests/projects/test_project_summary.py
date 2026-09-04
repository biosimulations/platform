"""HTTP contract tests for the project-summary passthrough proxy."""

import gzip

from collections.abc import Callable, Iterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.dependencies import get_http_client

Handler = Callable[[httpx.Request], httpx.Response]
RAW_BODY = b'{ "id": "project-1", "unknownFutureField": {"keep": true} }\n'


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
async def test_project_summary_fidelity_query_headers_and_credentials() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=gzip.compress(RAW_BODY),
            headers={
                "Content-Type": "application/problem+json; charset=utf-8",
                "ETag": '"project-v1"',
                "Vary": "Accept-Encoding",
                "Set-Cookie": "session=secret",
                "X-Powered-By": "legacy",
                "Keep-Alive": "timeout=5",
                "Content-Encoding": "gzip",
                "Content-Length": "999",
            },
        )

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get(
            "/projects/project-1/summary?a=first&a=second&z=last",
            headers={"Authorization": "Bearer secret", "Cookie": "session=caller"},
        )

    assert response.status_code == 200
    assert response.content == RAW_BODY
    assert response.json()["unknownFutureField"] == {"keep": True}
    assert len(seen) == 1
    assert seen[0].url.raw_path == b"/projects/project-1/summary?a=first&a=second&z=last"
    assert "authorization" not in seen[0].headers
    assert "cookie" not in seen[0].headers
    assert response.headers["content-type"] == "application/problem+json; charset=utf-8"
    assert response.headers["etag"] == '"project-v1"'
    assert response.headers["vary"] == "Accept-Encoding"
    for blocked in (
        "set-cookie",
        "x-powered-by",
        "keep-alive",
        "transfer-encoding",
        "content-encoding",
    ):
        assert blocked not in response.headers
    assert response.headers["content-length"] == str(len(RAW_BODY))


@pytest.mark.anyio
async def test_connection_named_response_header_is_removed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"ok", headers={"Connection": "ETag", "ETag": '"blocked"'}
        )

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/projects/project-1/summary")

    assert "connection" not in response.headers
    assert "etag" not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize("downstream_status", [201, 302, 400, 404, 429])
async def test_project_summary_mirrors_2xx_3xx_and_4xx(downstream_status: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            downstream_status,
            content=b"downstream-body",
            headers={"Location": "/elsewhere"},
        )

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/projects/project-1/summary")

    assert response.status_code == downstream_status
    assert response.content == b"downstream-body"
    assert response.headers["location"] == "/elsewhere"


@pytest.mark.anyio
async def test_project_summary_5xx_becomes_sanitized_502() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="database at internal.example:27017 failed")

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/projects/project-1/summary")

    assert response.status_code == 502
    assert "internal.example" not in response.text
    assert "27017" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(httpx.ReadTimeout("slow"), 504), (httpx.ConnectError("internal:443"), 502)],
)
async def test_project_summary_transport_failures(
    error: httpx.RequestError, expected_status: int
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        error.request = request
        raise error

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/projects/project-1/summary")

    assert response.status_code == expected_status
    assert "internal:443" not in response.text


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
        return httpx.Response(200, content=b"ok")

    caller, upstream = proxy_client(handler)
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
    route matches. It is deliberately not enforced inside ``proxy_get`` -- do
    not relocate this guarantee there.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/projects/..%2Fruns%2Fsecret%3Fx=1/summary")

    assert response.status_code == 404
    assert seen == []


@pytest.mark.anyio
@pytest.mark.parametrize("caller_segment", ["%2E", "%2E%2E", ".%2E"])
async def test_dot_only_project_id_is_rejected_before_any_upstream_call(
    caller_segment: str,
) -> None:
    """A decoded "." or ".." id is refused rather than proxied.

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
        return httpx.Response(200, content=b"ok")

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get(f"/projects/{caller_segment}/summary")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert seen == []


@pytest.mark.anyio
async def test_upstream_vary_origin_is_not_forwarded_into_a_duplicate() -> None:
    """CORSMiddleware owns Origin variance, so the upstream token is dropped.

    Starlette's ``Headers.add_vary_header`` concatenates without deduping, so
    forwarding the upstream's ``Vary: Origin`` would emit ``Origin, Origin``
    on any CORS request. Other upstream Vary field names are still forwarded.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"ok", headers={"Vary": "Accept-Encoding, Origin"}
        )

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        cors = await caller.get(
            "/projects/project-1/summary", headers={"Origin": "http://localhost:3000"}
        )
        plain = await caller.get("/projects/project-1/summary")

    assert cors.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert cors.headers["vary"] == "Accept-Encoding, Origin"
    assert plain.headers["vary"] == "Accept-Encoding"


@pytest.mark.anyio
async def test_vary_of_only_origin_is_dropped_rather_than_emptied() -> None:
    """Narrowing that removes every token must omit the header, not send "" ."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok", headers={"Vary": "Origin"})

    caller, upstream = proxy_client(handler)
    async with caller, upstream:
        response = await caller.get("/projects/project-1/summary")

    assert "vary" not in response.headers
