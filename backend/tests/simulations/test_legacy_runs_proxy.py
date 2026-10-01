"""Hermetic legacy proxy contracts, including ASGI stream lifetime and framing."""

import asyncio
import gzip
import json
import logging
from collections.abc import AsyncIterator, Buffer, Callable, Iterable, Iterator
from typing import Any, Self, SupportsIndex

import httpx
import pytest
from fastapi import Request
from starlette.types import Message

from biosim_server.api.main import app
from biosim_server.biosim_runs import legacy_api as legacy_api_module
from biosim_server.biosim_runs.legacy_api import (
    LEGACY_PATCH_MAX_BYTES,
    _OVERSIZE_DETAIL,
    proxy_run,
)
from biosim_server.common.upstream import upstream_url
from biosim_server.config import get_settings
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
    # Documents the upstream UpdateSimulationRun contract; the proxy still forwards raw bytes.
    assert list(patch["requestBody"]["content"]) == ["application/json"]
    schema = patch["requestBody"]["content"]["application/json"]["schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"status", "fileUrl", "projectSize", "resultsSize"}
    assert patch["requestBody"]["required"] is False


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


# ---------------------------------------------------------------------------
# Section 16: Capping Buffered Legacy Proxy Responses
# ---------------------------------------------------------------------------

@pytest.fixture
def cap(monkeypatch: pytest.MonkeyPatch) -> int:
    """A small, explicit cap so the boundary is testable without megabyte payloads."""
    limit = 64
    monkeypatch.setattr(get_settings(), "upstream_max_response_bytes", limit)
    return limit


async def test_oversize_buffered_response_is_rejected_and_the_stream_is_abandoned(
    cap: int,
) -> None:
    """Chunked body crossing the cap: 502, correct detail, stream closed, remainder not read."""
    # Four chunks; the cap is crossed partway through — not all should be read.
    half = cap // 2
    chunks = [
        b"A" * half,
        b"B" * half,
        b"C" * half,  # this crosses the cap
        b"D" * half,  # this must never be read
    ]
    stream = Chunks(chunks)

    caller, upstream = clients(
        lambda request: httpx.Response(200, stream=stream, headers={"Content-Type": "application/json"})
    )
    async with caller, upstream:
        response = await caller.get("/runs/example")

    assert response.status_code == 502
    assert response.json()["detail"] == _OVERSIZE_DETAIL
    assert stream.closed, "upstream stream must be closed after breach"
    assert stream.reads < len(chunks), "the remainder of the body must not have been read"


async def test_buffered_body_at_the_cap_is_relayed_exactly(cap: int) -> None:
    """A body of exactly cap bytes comes back whole with the original status."""
    body = b"x" * cap
    stream = Chunks([body])

    caller, upstream = clients(
        lambda request: httpx.Response(
            200, stream=stream, headers={"Content-Type": "application/json"}
        )
    )
    async with caller, upstream:
        response = await caller.get("/runs/example")

    assert response.status_code == 200
    assert response.content == body


class _PeakBuffer(bytearray):
    """Stand-in accumulator that records the largest size it ever reached."""

    peak = 0

    def __iadd__(self, value: Buffer, /) -> Self:  # type: ignore[override]  # as typeshed's bytearray
        result = super().__iadd__(value)
        type(self).peak = max(type(self).peak, len(self))
        return result

    def extend(self, iterable_of_ints: Iterable[SupportsIndex], /) -> None:
        super().extend(iterable_of_ints)
        type(self).peak = max(type(self).peak, len(self))


@pytest.fixture
def peak_buffer(monkeypatch: pytest.MonkeyPatch) -> type[_PeakBuffer]:
    """Swap the reader's module-global ``bytearray`` so its accumulator is observable."""
    _PeakBuffer.peak = 0
    monkeypatch.setattr(legacy_api_module, "bytearray", _PeakBuffer, raising=False)
    return _PeakBuffer


async def test_buffered_accumulator_never_exceeds_the_cap(
    cap: int, peak_buffer: type[_PeakBuffer]
) -> None:
    """The cap is checked before a chunk is added, so the buffer never holds more than it."""
    stream = Chunks([b"A" * (cap - 1), b"B" * 2, b"C"])

    caller, upstream = clients(
        lambda request: httpx.Response(200, stream=stream, headers={"Content-Type": "application/json"})
    )
    async with caller, upstream:
        response = await caller.get("/runs/example")

    assert response.status_code == 502
    assert response.json()["detail"] == _OVERSIZE_DETAIL
    assert stream.closed
    assert stream.reads < 3
    assert 0 < peak_buffer.peak <= cap


async def test_multi_chunk_body_filling_the_cap_exactly_is_relayed(
    cap: int, peak_buffer: type[_PeakBuffer]
) -> None:
    """A body that reaches exactly the cap across chunks is relayed whole."""
    chunks = [b"x" * (cap - 1), b"y"]
    stream = Chunks(chunks)

    caller, upstream = clients(
        lambda request: httpx.Response(200, stream=stream, headers={"Content-Type": "application/json"})
    )
    async with caller, upstream:
        response = await caller.get("/runs/example")

    assert response.status_code == 200
    assert response.content == b"".join(chunks)
    assert peak_buffer.peak == cap


@pytest.mark.parametrize(
    "declared_cl,large_body",
    [
        ("lying-large", False),   # huge Content-Length over a small body → 200
        ("absent", True),         # no Content-Length, large body → 502
    ],
)
async def test_declared_content_length_is_not_trusted(
    cap: int, declared_cl: str, large_body: bool
) -> None:
    """Content-Length must never decide the outcome; only bytes actually read matter."""
    if not large_body:
        # Overstated Content-Length but tiny body → must relay successfully.
        body = b"[]"
        stream = Chunks([body])
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Content-Length": str(10 * cap),
        }
        caller, upstream = clients(
            lambda request: httpx.Response(200, stream=stream, headers=headers)
        )
        async with caller, upstream:
            response = await caller.get("/runs/example")
        assert response.status_code == 200
        assert response.content == body
    else:
        # No Content-Length, large body → must reject with 502.
        large = b"Z" * (cap + 1)
        stream = Chunks([large])
        caller, upstream = clients(
            lambda request: httpx.Response(
                200, stream=stream, headers={"Content-Type": "application/json"}
            )
        )
        async with caller, upstream:
            response = await caller.get("/runs/example")
        assert response.status_code == 502


async def test_oversize_upstream_error_is_not_relayed(cap: int) -> None:
    """An oversized upstream 500 body becomes 502, not a truncated 500 (D3)."""
    large = b"E" * (cap + 1)
    stream = Chunks([large])

    caller, upstream = clients(
        lambda request: httpx.Response(
            500, stream=stream, headers={"Content-Type": "application/json"}
        )
    )
    async with caller, upstream:
        response = await caller.get("/runs/example")

    assert response.status_code == 502
    assert response.json()["detail"] == _OVERSIZE_DETAIL


async def test_oversize_is_recorded_as_too_large_without_the_body(
    cap: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Breach log has too_large outcome, correct fields, and SECRET never leaks (D7)."""
    SECRET = "SECRET_BODY_MARKER_XYZ"
    # Build an oversized body that contains the secret marker.
    filler = b"F" * (cap + 1)
    secret_bytes = SECRET.encode()
    chunks = [filler, secret_bytes]
    stream = Chunks(chunks)

    caller, upstream = clients(
        lambda request: httpx.Response(
            200, stream=stream, headers={"Content-Type": "application/json"}
        )
    )
    with caplog.at_level(logging.INFO):
        async with caller, upstream:
            response = await caller.get("/runs/example")

    assert response.status_code == 502

    records = [
        r for r in caplog.records if r.name == "biosim_server.biosim_runs.legacy_api"
    ]
    assert len(records) == 1
    log = json.loads(JsonFormatter().format(records[0]))

    assert log["legacy_outcome"] == "too_large"
    assert log["legacy_operation"] == "get"
    assert log["legacy_status"] == 200
    assert log["legacy_bytes"] > 0

    # The secret must not appear in the formatted log record or the 502 body.
    assert SECRET not in json.dumps(log)
    assert SECRET not in response.text


async def test_buffered_compressed_response_stays_compressed_under_the_cap(
    cap: int,
) -> None:
    """A gzip body under the cap is relayed as raw compressed bytes with Content-Encoding intact (D2)."""
    import gzip as _gzip

    raw = b'{"status": "ok"}'
    compressed = _gzip.compress(raw)
    assert len(compressed) < cap, "fixture must be under the cap on the wire"

    stream = Chunks([compressed])

    caller, upstream = clients(
        lambda request: httpx.Response(
            200,
            stream=stream,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
        )
    )
    async with caller, upstream:
        # Use aiter_raw() to avoid the caller's transparent decompression so we
        # can assert on the wire bytes, mirroring test_download_range_binary_and_compression.
        async with caller.stream("GET", "/runs/example") as response:
            wire = b"".join([chunk async for chunk in response.aiter_raw()])

    assert response.status_code == 200
    assert wire == compressed
    assert response.headers.get("content-encoding") == "gzip"


async def test_downloads_are_not_capped(cap: int) -> None:
    """A download body larger than the cap streams to completion unchanged (16.3)."""
    large = b"G" * (cap * 4)
    stream = Chunks([large])

    caller, upstream = clients(
        lambda request: httpx.Response(
            200,
            stream=stream,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(large)),
            },
        )
    )
    async with caller, upstream:
        response = await caller.get("/runs/example/download")

    assert response.status_code == 200
    assert response.content == large
    assert stream.closed


async def test_empty_and_not_modified_responses_are_unaffected(cap: int) -> None:
    """204 and 304 return empty bodies and correct headers even under a tiny cap."""
    for status, extra_headers in [
        (204, {}),
        (304, {"content-length": "0"}),
    ]:
        def _make_handler(
            s: int, h: dict[str, str]
        ) -> Callable[[httpx.Request], httpx.Response]:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(s, stream=Chunks([]), headers=h)

            return handler

        caller, upstream = clients(_make_handler(status, extra_headers))
        async with caller, upstream:
            response = await caller.get("/runs/example")

        assert response.status_code == status
        assert response.content == b""
        # 204 must have content-length removed.
        if status == 204:
            assert "content-length" not in response.headers


async def test_the_cap_is_read_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing the setting changes the boundary, proving the proxy reads it rather than a constant."""
    # Set a very small cap: 4 bytes.
    small_cap = 4
    monkeypatch.setattr(get_settings(), "upstream_max_response_bytes", small_cap)

    # A body at the cap passes.
    ok_body = b"x" * small_cap
    caller, upstream = clients(
        lambda request: httpx.Response(
            200, stream=Chunks([ok_body]), headers={"Content-Type": "application/json"}
        )
    )
    async with caller, upstream:
        response = await caller.get("/runs/example")
    assert response.status_code == 200
    assert response.content == ok_body

    # A body one byte over fails.
    large_body = b"x" * (small_cap + 1)
    caller2, upstream2 = clients(
        lambda request: httpx.Response(
            200,
            stream=Chunks([large_body]),
            headers={"Content-Type": "application/json"},
        )
    )
    async with caller2, upstream2:
        response2 = await caller2.get("/runs/example")
    assert response2.status_code == 502
    assert response2.json()["detail"] == _OVERSIZE_DETAIL
