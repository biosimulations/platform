"""Multipart request bodies are bounded while they are received (PR #120, B1).

FastAPI parses a ``File(...)`` route's form -- spooling every file part to disk --
before any dependency or handler runs, so the handler's content check alone cannot
stop an oversized body from being received. These drive the real application
through ASGI with a streamed body and count how much of it was actually consumed.
"""

from collections.abc import AsyncIterator, Iterator
from tempfile import SpooledTemporaryFile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from starlette import formparsers
from starlette.types import Message, Receive, Scope, Send

from biosim_server.api.main import app
from biosim_server.biosim_omex import omex_storage
from biosim_server.common.upload_limit import MultipartBodyLimitMiddleware, multipart_body_limit

pytestmark = pytest.mark.asyncio

CHUNK = 64 * 1024
CHUNKS = 64
BOUNDARY = "B"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
PREAMBLE = (
    f"--{BOUNDARY}\r\n"
    'Content-Disposition: form-data; name="uploaded_file"; filename="a.omex"\r\n'
    "Content-Type: application/zip\r\n\r\n"
).encode()
EPILOGUE = f"\r\n--{BOUNDARY}--\r\n".encode()
ROUTES = ["/verify/omex", "/compatibility/check"]


class _StreamedUpload:
    """An oversized multipart body that records how many file chunks were pulled."""

    def __init__(self, chunks: int = CHUNKS) -> None:
        self.chunks = chunks
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield PREAMBLE
        for _ in range(self.chunks):
            self.consumed += 1
            yield b"x" * CHUNK
        yield EPILOGUE


@pytest.fixture(autouse=True)
def small_cap(monkeypatch: pytest.MonkeyPatch) -> int:
    """A 1 KiB content cap, so the receipt bound is cap + framing allowance."""
    monkeypatch.setattr(omex_storage, "MAX_OMEX_BYTES", 1024)
    return multipart_body_limit()


@pytest.fixture
def handler_spies() -> Iterator[dict[str, MagicMock]]:
    """Spies on the first thing each route's handler body does."""
    temporal = MagicMock(return_value=None)
    read_upload = AsyncMock(side_effect=HTTPException(status_code=418, detail="handler reached"))
    with patch("biosim_server.api.main.get_temporal_client", temporal), \
         patch("biosim_server.compatibility.router.read_upload_capped", read_upload):
        yield {"/verify/omex": temporal, "/compatibility/check": read_upload}


@pytest.fixture
def temp_files(monkeypatch: pytest.MonkeyPatch) -> list[SpooledTemporaryFile[bytes]]:
    """Record every temp file Starlette's multipart parser spools a part into."""
    created: list[SpooledTemporaryFile[bytes]] = []

    class _Recording(SpooledTemporaryFile[bytes]):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", _Recording)
    return created


async def _post(path: str, body: _StreamedUpload, headers: dict[str, str] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            path, content=body, headers={"content-type": CONTENT_TYPE, **(headers or {})}
        )


def _assert_too_large(response: httpx.Response) -> None:
    assert response.status_code == 413, response.text
    assert "MiB limit" in response.json()["detail"]


@pytest.mark.parametrize("path", ROUTES)
async def test_oversized_body_without_content_length_is_rejected_before_the_remainder_is_read(
    path: str, handler_spies: dict[str, MagicMock]
) -> None:
    body = _StreamedUpload()
    response = await _post(path, body)

    _assert_too_large(response)
    # cap + 64 KiB allowance is crossed by the second 64 KiB chunk.
    assert body.consumed <= 2
    handler_spies[path].assert_not_called()


@pytest.mark.parametrize("path", ROUTES)
async def test_understated_content_length_does_not_bypass_the_bound(
    path: str, handler_spies: dict[str, MagicMock]
) -> None:
    body = _StreamedUpload()
    response = await _post(path, body, headers={"content-length": "100"})

    _assert_too_large(response)
    assert body.consumed <= 2
    handler_spies[path].assert_not_called()


@pytest.mark.parametrize("path", ROUTES)
async def test_declared_oversized_content_length_is_rejected_without_reading_the_body(
    path: str, small_cap: int, handler_spies: dict[str, MagicMock]
) -> None:
    body = _StreamedUpload()
    response = await _post(path, body, headers={"content-length": str(small_cap + 1)})

    _assert_too_large(response)
    assert body.consumed == 0
    handler_spies[path].assert_not_called()


@pytest.mark.parametrize("path", ROUTES)
async def test_spooled_temp_files_are_closed_on_rejection(
    path: str,
    handler_spies: dict[str, MagicMock],
    temp_files: list[SpooledTemporaryFile[bytes]],
) -> None:
    response = await _post(path, _StreamedUpload())

    _assert_too_large(response)
    assert temp_files, "the file part was never spooled -- the test is not exercising cleanup"
    assert all(f.closed for f in temp_files)


@pytest.mark.parametrize("path", ROUTES)
async def test_client_disconnect_mid_body_closes_temp_files(
    path: str,
    handler_spies: dict[str, MagicMock],
    temp_files: list[SpooledTemporaryFile[bytes]],
) -> None:
    messages: list[Message] = [
        {"type": "http.request", "body": PREAMBLE, "more_body": True},
        {"type": "http.request", "body": b"x" * CHUNK, "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
        "query_string": b"", "server": ("test", 80), "client": ("203.0.113.9", 1234),
        "headers": [(b"content-type", CONTENT_TYPE.encode())],
    }
    await app(scope, receive, send)

    assert temp_files and all(f.closed for f in temp_files)
    assert sent == [], "nothing is sent to a client that has gone away"
    handler_spies[path].assert_not_called()


async def test_body_within_the_bound_reaches_the_handler(handler_spies: dict[str, MagicMock]) -> None:
    small = _StreamedUpload(chunks=0)
    response = await _post("/compatibility/check", small)
    assert response.status_code == 418
    handler_spies["/compatibility/check"].assert_awaited_once()

    response = await _post("/verify/omex", _StreamedUpload(chunks=0))
    assert response.status_code == 503
    assert response.json() == {"detail": "Temporal service not available"}
    handler_spies["/verify/omex"].assert_called()


async def test_413_carries_cors_headers(handler_spies: dict[str, MagicMock]) -> None:
    origin = "http://127.0.0.1:4200"
    response = await _post("/compatibility/check", _StreamedUpload(), headers={"origin": origin})

    _assert_too_large(response)
    assert response.headers["access-control-allow-origin"] == origin


async def test_query_only_verify_runs_is_untouched() -> None:
    temporal = MagicMock()

    async def _start(*_args: object, id: str = "", **_kwargs: object) -> MagicMock:
        return MagicMock(id=id, run_id="run-1")

    temporal.start_workflow = AsyncMock(side_effect=_start)
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=AsyncMock()), \
         patch("biosim_server.api.main._load_hdf5_metadata_for_preflight", new=AsyncMock(return_value={})):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/verify/runs")
    assert response.status_code == 200, response.text


# --- the middleware in isolation ------------------------------------------------


async def _drive(app_under_test: MultipartBodyLimitMiddleware, content_type: str, size: int) -> list[Message]:
    pending: list[Message] = [{"type": "http.request", "body": b"y" * size, "more_body": False}]

    async def receive() -> Message:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": "POST", "path": "/x", "headers": [(b"content-type", content_type.encode())]}
    await app_under_test(scope, receive, send)
    return sent


async def _read_whole_body(scope: Scope, receive: Receive, send: Send) -> None:
    """A downstream app that reads the raw body itself instead of parsing a form."""
    total = 0
    more = True
    while more:
        message = await receive()
        total += len(message.get("body", b""))
        more = message.get("more_body", False)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(total).encode()})


async def test_non_multipart_bodies_are_not_bounded(small_cap: int) -> None:
    sent = await _drive(MultipartBodyLimitMiddleware(_read_whole_body), "application/json", small_cap * 4)
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == str(small_cap * 4).encode()


async def test_a_non_form_reader_of_an_oversized_multipart_body_still_gets_413(small_cap: int) -> None:
    sent = await _drive(MultipartBodyLimitMiddleware(_read_whole_body), CONTENT_TYPE, small_cap + 1)
    assert sent[0]["status"] == 413
    assert b"MiB limit" in sent[1]["body"]
