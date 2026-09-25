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

``httpx.AsyncByteStream`` stands in for a chunked/chunk-y upstream body; no
network, no real upstream.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import HTTPException

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
