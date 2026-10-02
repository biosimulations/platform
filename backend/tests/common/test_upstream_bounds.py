"""The upstream response-size bound (SHARED-MAJ-001).

The page assemblers buffer whatever biosimulations.org returns, so an unbounded
body is unbounded worker memory plus synchronous parse work on the request path.
These pin the cap's *semantics*, which is where an implementation quietly goes
wrong:

  * it is a bound on **decoded** bytes -- a small gzip body that expands past the
    cap is rejected, so compression is not a way around it;
  * ``Content-Length`` is never trusted -- a lying or absent header changes
    nothing, because the decision is made from the bytes actually read;
  * the stream is abandoned and closed the moment the cap is crossed, so the
    rejection does not itself buffer the oversized body;
  * the caller-visible failure is a sanitized 502, never a truncated payload.

The same helper must also build each request afresh -- no cookie jar, default
credentials or query defaults from the pooled client it shares with the legacy
runs proxy -- and close every response however the fetch ends.

``httpx.AsyncByteStream`` stands in for a chunked/chunk-y upstream body; no
network, no real upstream.
"""

import asyncio
from collections.abc import AsyncIterator, Buffer, Iterable
from typing import Any, Self, SupportsIndex

import httpx
import pytest
from fastapi import HTTPException

from biosim_server.common import upstream as upstream_module
from biosim_server.common.upstream import fetch_upstream_json_value
from biosim_server.config import get_settings

RESOURCE = "run files"
_OVERSIZE = "The upstream service returned a run files that is too large to load."


class _Body(httpx.AsyncByteStream):
    """A scripted response body that records how much of it was consumed."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _client(body: _Body, *, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=body, headers=headers or {}))
    return httpx.AsyncClient(transport=transport, base_url="https://upstream.test")


@pytest.fixture
def cap(monkeypatch: pytest.MonkeyPatch) -> int:
    """A small, explicit cap so the boundary is testable without big payloads."""
    limit = 64
    monkeypatch.setattr(get_settings(), "upstream_max_response_bytes", limit)
    return limit


@pytest.mark.asyncio
async def test_body_within_the_cap_is_accepted(cap: int) -> None:
    body = _Body([b'{"files": ', b"[]}"])
    async with _client(body) as client:
        assert await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE) == {"files": []}
    assert body.yielded == 2


@pytest.mark.asyncio
async def test_body_at_the_cap_is_accepted(cap: int) -> None:
    payload = b"[]" + b" " * (cap - 2)
    async with _client(_Body([payload])) as client:
        assert await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE) == []


@pytest.mark.asyncio
async def test_body_over_the_cap_is_rejected_and_the_stream_is_abandoned(cap: int) -> None:
    """Chunked body past the cap: sanitized 502, remaining chunks never read, closed."""
    chunks = [b"[" + b" " * (cap // 2), b" " * (cap // 2), b"]" * 10, b"never-read"]
    body = _Body(chunks)
    async with _client(body) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE)

    assert error.value.status_code == 502
    assert error.value.detail == _OVERSIZE
    assert body.closed, "an oversized response must not be left open"
    assert body.yielded < len(chunks), "the whole body was read despite the cap"
    assert "secret" not in error.value.detail


@pytest.mark.asyncio
async def test_declared_content_length_is_not_trusted(cap: int) -> None:
    """A huge (or absent) Content-Length must not decide anything by itself."""
    lying = _Body([b"[]"])
    async with _client(lying, headers={"content-length": str(10 * cap)}) as client:
        assert await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE) == []

    absent = _Body([b"[" + b"1," * (cap // 2) + b"1]"])
    async with _client(absent) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE)
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_a_compressed_body_is_capped_by_decoded_size(cap: int) -> None:
    """A small gzip body that expands past the cap is rejected, not buffered."""
    import gzip

    decoded = b'{"logs": "' + b"x" * (cap * 20) + b'"}'
    compressed = gzip.compress(decoded)
    assert len(compressed) < cap, "the fixture must be small on the wire"

    async with _client(_Body([compressed]), headers={"content-encoding": "gzip"}) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/logs/x", resource="run logs")
    assert error.value.status_code == 502
    assert error.value.detail == "The upstream service returned a run logs that is too large to load."


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
    monkeypatch.setattr(upstream_module, "bytearray", _PeakBuffer, raising=False)
    return _PeakBuffer


@pytest.mark.asyncio
async def test_accumulator_never_exceeds_the_cap(cap: int, peak_buffer: type[_PeakBuffer]) -> None:
    """The cap is checked before a chunk is added, so the buffer never holds more than it."""
    body = _Body([b"[" + b" " * (cap - 2), b"  ", b"]"])
    async with _client(body) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE)

    assert error.value.status_code == 502
    assert error.value.detail == _OVERSIZE
    assert body.closed
    assert 0 < peak_buffer.peak <= cap


@pytest.mark.asyncio
async def test_multi_chunk_body_at_the_cap_is_accepted(cap: int, peak_buffer: type[_PeakBuffer]) -> None:
    async with _client(_Body([b"[", b" " * (cap - 2), b"]"])) as client:
        assert await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE) == []
    assert peak_buffer.peak == cap


@pytest.mark.asyncio
async def test_the_cap_is_read_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "upstream_max_response_bytes", 12)
    async with _client(_Body([b'{"a": 1}'])) as client:
        assert await fetch_upstream_json_value(client, "/x", resource=RESOURCE) == {"a": 1}
    async with _client(_Body([b'{"a": 1234567890}'])) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/x", resource=RESOURCE)
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_a_non_positive_cap_is_rejected_at_settings_construction() -> None:
    from pydantic import ValidationError

    from biosim_server.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, UPSTREAM_MAX_RESPONSE_BYTES="0")  # type: ignore[call-arg,arg-type]


# ---------------------------------------------------------------------------
# Pooled-client isolation: a public fetch carries no client state
# ---------------------------------------------------------------------------
# The page/summary fetches share one pooled client with the legacy runs proxy,
# and httpx's send() stores every upstream Set-Cookie in that client's jar. A
# fetch built through the client's request builder would replay that jar -- and
# the client's default headers, auth, URL credentials and query defaults -- on
# an anonymous public request.

_POOLED_STATE: dict[str, dict[str, Any]] = {
    "cookie jar": {"cookies": {"session": "user-A"}},
    "default authorization header": {"headers": {"Authorization": "Bearer pooled-secret"}},
    "client auth": {"auth": ("pooled-user", "pooled-password")},
    "base URL credentials": {"base_url": "https://pooled-user:pooled-password@upstream.test"},
    "default query parameters": {"params": {"service-secret": "must-not-leak"}},
    "default custom header": {"headers": {"X-Service-Key": "must-not-leak"}},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", _POOLED_STATE.values(), ids=_POOLED_STATE)
async def test_a_public_fetch_inherits_no_pooled_client_state(state: dict[str, Any]) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    options: dict[str, Any] = {"base_url": "https://upstream.test", **state}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), **options) as client:
        assert await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE) == []

    (request,) = seen
    assert request.url.raw_path == b"/files/x"
    assert request.url.userinfo == b""
    for name in ("cookie", "authorization", "x-service-key"):
        assert name not in request.headers


@pytest.mark.asyncio
async def test_a_public_fetch_sends_only_its_own_headers_to_the_configured_base() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"a": 1})

    timeout = httpx.Timeout(7.0, connect=3.0)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://upstream.test/api/v1/", timeout=timeout,
    ) as client:
        assert await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE) == {"a": 1}

    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == "https://upstream.test/api/v1/files/x"
    assert set(request.headers) == {"host", "accept", "accept-encoding", "user-agent"}
    assert request.headers["accept"] == "application/json"
    assert request.extensions["timeout"] == timeout.as_dict()


@pytest.mark.asyncio
async def test_a_cookie_set_on_one_public_fetch_is_never_replayed_on_the_next() -> None:
    """Even a client used only for public fetches must not carry state between them."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[], headers={"Set-Cookie": "session=first-caller; Path=/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as client:
        await fetch_upstream_json_value(client, "/files/a", resource=RESOURCE)
        await fetch_upstream_json_value(client, "/files/b", resource=RESOURCE)

    assert len(seen) == 2
    assert all("cookie" not in request.headers for request in seen)


@pytest.mark.asyncio
async def test_redirects_are_not_followed_even_when_the_client_would(cap: int) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.test/secret"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://upstream.test", follow_redirects=True,
    ) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE)
    assert error.value.status_code == 502
    assert [request.url.host for request in seen] == ["upstream.test"]


# ---------------------------------------------------------------------------
# Every upstream response is closed, whichever way the fetch ends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [204, 301, 400, 404, 429, 500])
async def test_an_error_status_closes_the_response_without_reading_it(cap: int, status_code: int) -> None:
    body = _Body([b"secret error body"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code, stream=body)),
        base_url="https://upstream.test",
    ) as client:
        with pytest.raises(HTTPException):
            await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE)
    assert body.closed
    assert body.yielded == 0


@pytest.mark.asyncio
async def test_an_invalid_body_closes_the_response(cap: int) -> None:
    body = _Body([b"<html>not json"])
    async with _client(body) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE)
    assert error.value.status_code == 502
    assert body.closed


@pytest.mark.asyncio
async def test_a_timeout_mid_body_is_a_504_and_closes_the_response(cap: int) -> None:
    class _Stalled(_Body):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.yielded += 1
            yield b"["
            raise httpx.ReadTimeout("secret upstream detail")

    body = _Stalled([])
    async with _client(body) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/files/x", resource=RESOURCE)
    assert error.value.status_code == 504
    assert "secret" not in error.value.detail
    assert body.closed


@pytest.mark.asyncio
async def test_cancellation_mid_body_propagates_and_closes_the_response(cap: int) -> None:
    reading = asyncio.Event()

    class _Hanging(_Body):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"["
            reading.set()
            await asyncio.Event().wait()

    body = _Hanging([])
    async with _client(body) as client:
        fetch = asyncio.create_task(fetch_upstream_json_value(client, "/files/x", resource=RESOURCE))
        await asyncio.wait_for(reading.wait(), timeout=2)
        assert not body.closed
        fetch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fetch
    assert body.closed
