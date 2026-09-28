"""Hermetic legacy proxy contracts, including ASGI stream lifetime and framing."""

import asyncio
import gzip
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import Request
from starlette.types import Message

from biosim_server.api.main import app
from biosim_server.biosim_runs.legacy_api import LEGACY_PATCH_MAX_BYTES, proxy_run
from biosim_server.common.upstream import upstream_url
from biosim_server.dependencies import get_http_client
from biosim_server.log_config import JsonFormatter

pytestmark = pytest.mark.asyncio
ROUTES = [
    ("GET", "/runs/example"),
    ("PATCH", "/runs/example"),
    ("DELETE", "/runs/example"),
    ("GET", "/runs/example/download"),
    ("GET", "/runs/example/validate"),
    ("GET", "/runs/summary"),
]


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False
        self.reads = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            assert not self.closed
            self.reads += 1
            yield chunk

    async def aclose(self) -> None:
        await asyncio.sleep(0)  # exercise cleanup under cancellation
        self.closed = True


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    previous = app.dependency_overrides.copy()
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def clients(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://upstream.test"
    )
    app.dependency_overrides[get_http_client] = lambda: upstream
    caller = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://platform.test"
    )
    return caller, upstream


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize(
    "status", [200, 202, 204, 301, 304, 400, 401, 403, 404, 409, 422, 429, 500]
)
async def test_status_opaque_body_headers_and_single_attempt(
    method: str, path: str, status: int
) -> None:
    seen: list[httpx.Request] = []
    body = b"" if status in (204, 304) else b"{invalid json\xff\x00"
    stream = Chunks([body[:3], body[3:]])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            status,
            stream=stream,
            headers={
                "Content-Type": "application/x-legacy",
                "Content-Length": str(len(body)),
                "Retry-After": "19",
                "ETag": '"unchanged"',
                "Location": "https://other.test/archive",
                "Cache-Control": "private",
                "Last-Modified": "Mon, 28 Sep 2026 01:00:00 GMT",
                "Expires": "Tue, 29 Sep 2026 01:00:00 GMT",
                "Vary": "Accept",
                "Set-Cookie": "secret=cookie",
                "Connection": "keep-alive, X-Hop",
                "X-Hop": "secret",
                "X-Arbitrary": "secret",
                "Keep-Alive": "timeout=5",
            },
        )

    caller, upstream = clients(handler)
    upstream.follow_redirects = True  # the proxy must explicitly disable this
    async with caller, upstream:
        response = await caller.request(method, path)
    assert len(seen) == 1
    assert seen[0].method == method and seen[0].url.path == path
    assert "authorization" not in seen[0].headers
    assert response.status_code == status
    assert response.content == body
    assert response.headers["content-type"] == "application/x-legacy"
    for key in (
        "retry-after",
        "etag",
        "location",
        "cache-control",
        "last-modified",
        "expires",
        "vary",
    ):
        assert key in response.headers
    for key in (
        "set-cookie",
        "connection",
        "x-hop",
        "x-arbitrary",
        "keep-alive",
        "transfer-encoding",
    ):
        assert key not in response.headers
    assert stream.closed


@pytest.mark.parametrize("method,path", ROUTES)
async def test_exact_query_body_and_allowlisted_caller_headers(
    method: str, path: str
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=Chunks([b'{"future":[1]}']))

    caller, upstream = clients(handler)
    upstream.cookies.set("pooled", "must-not-leak")
    upstream.headers["Authorization"] = "Bearer pooled-secret"
    upstream.headers["X-Default"] = "must-not-leak"
    upstream.auth = ("pooled-user", "pooled-password")
    query = b"filter=a&filter=b&space=a+b&space=a%20b&escaped=%2f%25&bare&empty="
    body = b" \x00\xff{not json}\n" if method == "PATCH" else b""
    caller_headers = {
        "Authorization": "Bearer caller-secret",
        "Cookie": "caller=secret",
        "Accept": "application/x-legacy",
        "Content-Type": "application/custom; charset=latin-1",
        "Range": "bytes=1-3",
        "If-None-Match": '"old"',
        "If-Modified-Since": "Mon, 28 Sep 2026 00:00:00 GMT",
        "X-Untrusted": "bad",
        "Proxy-Authorization": "bad",
        "Connection": "keep-alive, Accept",
        "Keep-Alive": "timeout=999",
        "TE": "trailers",
        "Trailer": "X-Trailer",
        "Upgrade": "bad",
    }
    async with caller, upstream:
        response = await caller.request(
            method, path + "?" + query.decode(), content=body, headers=caller_headers
        )
    assert response.content == b'{"future":[1]}'
    assert len(seen) == 1
    outbound = seen[0]
    assert outbound.url.query == query
    assert outbound.content == body
    assert outbound.headers["authorization"] == "Bearer caller-secret"
    assert outbound.headers["host"] == "upstream.test"
    assert outbound.headers["accept-encoding"] == "identity"
    for name in (
        "cookie",
        "x-untrusted",
        "proxy-authorization",
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "accept",
        "x-default",
    ):
        assert name not in outbound.headers
    assert ("range" in outbound.headers) == path.endswith("/download")
    assert ("if-none-match" in outbound.headers) == (method == "GET")
    assert ("content-type" in outbound.headers) == (method == "PATCH")
    if method == "PATCH":
        assert outbound.headers["content-type"] == caller_headers["Content-Type"]
        assert outbound.headers["content-length"] == str(len(body))


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize(
    "error,expected", [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502)]
)
async def test_sanitized_transport_failure_no_retry(
    method: str, path: str, error: type[httpx.RequestError], expected: int
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise error("secret URL, credentials and body", request=request)

    caller, upstream = clients(handler)
    async with caller, upstream:
        response = await caller.request(method, path)
    assert response.status_code == expected
    assert "secret" not in response.text
    assert len(seen) == 1


@pytest.mark.parametrize(
    "segment", ["%2E", "%2E%2E", ".%2E", "a%2Fb", "..%2Fruns%2Fsecret"]
)
async def test_invalid_path_never_reaches_upstream(segment: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    caller, upstream = clients(handler)
    async with caller, upstream:
        response = await caller.get(f"/runs/{segment}")
    assert response.status_code == 404
    assert not seen


@pytest.mark.parametrize(
    "segment", ["a%20b", "a%25b", "a%23b", "a%3Fb", "a.b", "end.", "%252E", "a%252Fb"]
)
async def test_path_quoting(segment: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    caller, upstream = clients(handler)
    async with caller, upstream:
        response = await caller.get(f"/runs/{segment}")
    assert response.status_code == 200
    assert seen[0].url.raw_path == b"/runs/" + segment.encode()


async def test_literal_dot_helper_rejects() -> None:
    from fastapi import HTTPException

    for segment in (".", ".."):
        with pytest.raises(HTTPException) as exc:
            upstream_url("runs", segment)
        assert exc.value.status_code == 404


@pytest.mark.parametrize("declared", [True, False])
async def test_oversize_patch_is_rejected_before_mutation(declared: bool) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"x" * LEGACY_PATCH_MAX_BYTES
        yield b"!"

    caller, upstream = clients(handler)
    async with caller, upstream:
        response = await caller.patch(
            "/runs/example",
            content=chunks(),
            headers=(
                {"Content-Length": str(LEGACY_PATCH_MAX_BYTES + 1)} if declared else {}
            ),
        )
    assert response.status_code == 413
    assert not seen


async def test_patch_at_limit_is_forwarded_exactly() -> None:
    body = b"x" * LEGACY_PATCH_MAX_BYTES

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.content == body
        assert "content-type" not in request.headers
        return httpx.Response(204)

    caller, upstream = clients(handler)
    async with caller, upstream:
        assert (await caller.patch("/runs/example", content=body)).status_code == 204


async def test_download_range_binary_and_compression() -> None:
    binary = b"\x00\xff\xfe\x80archive"
    compressed = gzip.compress(binary)
    stream = Chunks([compressed[:7], compressed[7:]])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=0-9"
        assert request.headers["accept"] == "application/zip"
        return httpx.Response(
            206,
            stream=stream,
            headers={
                "Content-Type": "application/zip",
                "Content-Encoding": "gzip",
                "Content-Disposition": 'attachment; filename="run.omex"',
                "Content-Length": str(len(compressed)),
                "Content-Range": "bytes 0-9/100",
                "Accept-Ranges": "bytes",
                "ETag": '"strong"',
            },
        )

    caller, upstream = clients(handler)
    async with caller, upstream:
        async with caller.stream(
            "GET",
            "/runs/example/download",
            headers={"Range": "bytes=0-9", "Accept": "application/zip"},
        ) as response:
            raw = b"".join([chunk async for chunk in response.aiter_raw()])
    assert raw == compressed
    assert response.headers["content-length"] == str(len(compressed))
    assert response.headers["content-disposition"] == 'attachment; filename="run.omex"'
    assert response.headers["content-range"] == "bytes 0-9/100"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"] == '"strong"'
    assert stream.reads == 2 and stream.closed


async def test_stream_is_lazy_and_closes_on_cancellation() -> None:
    entered = asyncio.Event()

    class BlockingStream(Chunks):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"\xfffirst"
            entered.set()
            await asyncio.Event().wait()

    stream = BlockingStream([])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        ),
        base_url="https://upstream.test",
    ) as upstream:
        response = await proxy_run(
            upstream,
            Request(
                {"type": "http", "method": "GET", "headers": [], "query_string": b""}
            ),
            "download",
            "example",
            "download",
        )
        assert not stream.closed and not entered.is_set()
        sent: list[Message] = []

        async def receive() -> dict[str, Any]:
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            sent.append(message)

        task = asyncio.create_task(
            response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert sent[1]["body"] == b"\xfffirst"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed


@pytest.mark.parametrize("failure_at_start", [True, False])
async def test_downstream_send_failure_closes_stream(failure_at_start: bool) -> None:
    stream = Chunks([b"one", b"two"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        ),
        base_url="https://upstream.test",
    ) as upstream:
        response = await proxy_run(
            upstream,
            Request(
                {"type": "http", "method": "GET", "headers": [], "query_string": b""}
            ),
            "download",
            "example",
            "download",
        )

        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if failure_at_start or message["type"] == "http.response.body":
                raise RuntimeError("downstream gone")

        with pytest.raises(RuntimeError):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        assert stream.closed


async def test_static_route_and_safe_structured_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caller, upstream = clients(
        lambda request: httpx.Response(429, stream=Chunks([b"secret-payload"]))
    )
    with caplog.at_level(logging.INFO):
        async with caller, upstream:
            await caller.get(
                "/runs/summary?secret-query=value",
                headers={"Authorization": "Bearer secret-token"},
            )
    records = [
        r for r in caplog.records if r.name == "biosim_server.biosim_runs.legacy_api"
    ]
    assert len(records) == 1
    log = json.loads(JsonFormatter().format(records[0]))
    assert log["legacy_operation"] == "summary"  # proves static route wins
    assert log["legacy_status"] == 429
    assert log["legacy_bytes"] == len(b"secret-payload")
    assert log["legacy_outcome"] == "client_error"
    assert log["legacy_duration_ms"] >= 0
    assert "secret" not in json.dumps(log)
    assert not any("https://upstream.test" in r.getMessage() for r in caplog.records)


async def test_asgi_disconnect_closes_upstream_in_cancel_scope() -> None:
    delivered = asyncio.Event()
    stream = Chunks([b"first", b"second"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        ),
        base_url="https://upstream.test",
    ) as upstream:
        response = await proxy_run(
            upstream,
            Request(
                {"type": "http", "method": "GET", "headers": [], "query_string": b""}
            ),
            "download",
            "example",
            "download",
        )

        async def receive() -> Message:
            await delivered.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body":
                delivered.set()
                await asyncio.Event().wait()

        await asyncio.wait_for(
            response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send),
            2,
        )
        assert stream.closed


async def test_upstream_midstream_failure_aborts_and_closes() -> None:
    class BrokenStream(Chunks):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"first"
            raise httpx.ReadError("upstream failed")

    stream = BrokenStream([])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        ),
        base_url="https://upstream.test",
    ) as upstream:
        response = await proxy_run(
            upstream,
            Request(
                {"type": "http", "method": "GET", "headers": [], "query_string": b""}
            ),
            "download",
            "example",
            "download",
        )
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            sent.append(message)

        with pytest.raises(httpx.ReadError):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        assert stream.closed
        assert len(sent) == 2
        assert sent[0]["status"] == 200
        assert sent[1]["body"] == b"first" and sent[1]["more_body"]


async def test_anonymous_call_never_inherits_pooled_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(401)

    caller, upstream = clients(handler)
    upstream.auth = ("service", "secret")
    upstream.headers["authorization"] = "Bearer service"
    upstream.cookies.set("session", "secret")
    async with caller, upstream:
        assert (await caller.delete("/runs/example")).status_code == 401


async def test_connection_nominated_response_header_is_stripped() -> None:
    caller, upstream = clients(
        lambda request: httpx.Response(
            200,
            stream=Chunks([b"body"]),
            headers={
                "ETag": '"hop"',
                "Connection": "ETag",
                "Content-Type": "text/plain",
            },
        )
    )
    async with caller, upstream:
        response = await caller.get("/runs/example")
    assert response.content == b"body"
    assert "etag" not in response.headers


async def test_openapi_opaque_patch_and_upstream_authorization_contract() -> None:
    spec = app.openapi()
    for method, path in ROUTES:
        operation = spec["paths"][path.replace("example", "{run_id}")][method.lower()]
        assert not operation.get("security")  # no local Auth0 gate
        assert "Caller Authorization" in operation["description"]
        assert "default" in operation["responses"]
        assert "content" not in operation["responses"]["200"]  # no invented JSON schema
    patch = spec["paths"]["/runs/{run_id}"]["patch"]
    assert "413" in patch["responses"]
    assert patch["requestBody"]["content"]["*/*"]["schema"] == {
        "type": "string",
        "format": "binary",
    }


async def test_configured_base_prefix_is_preserved_without_default_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    caller, upstream = clients(handler)
    upstream.base_url = "https://upstream.test/legacy/v1/"
    upstream.params = httpx.QueryParams({"service-secret": "must-not-leak"})
    async with caller, upstream:
        await caller.get("/runs/example?x=1&x=2")
    assert seen[0].url.raw_path == b"/legacy/v1/runs/example?x=1&x=2"
