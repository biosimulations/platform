"""Bound multipart request bodies while they are received (PR #120, B1).

FastAPI parses a ``File(...)`` route's form -- spooling every file part to disk --
before any dependency or handler runs, so a content check in the handler
(``omex_storage.read_upload_capped``) cannot stop an oversized body from being
received. This middleware counts body bytes as the ASGI server delivers them and
aborts the parse as soon as the bound is crossed; the handler's content check
stays as the exact, defense-in-depth limit.

It applies to every ``multipart/form-data`` request, so a future upload route is
covered without registration. Non-multipart bodies pass through untouched.
"""

from typing import Literal

from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from biosim_server.biosim_omex import omex_storage

# Allowance for multipart framing (boundaries, part headers) around the one file
# part the upload routes accept. Generous on purpose: the file-content cap in
# omex_storage.read_upload_capped remains the exact limit.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


class _BodyLimitExceeded(MultiPartException):
    """Raised from ``receive``. A MultiPartException *on purpose*: Starlette's
    multipart parser closes every spooled temp file only for this exception type."""


def multipart_body_limit() -> int:
    # Read at call time so tests (and a future setting) move one knob.
    return omex_storage.MAX_OMEX_BYTES + MULTIPART_OVERHEAD_BYTES


def _too_large() -> JSONResponse:
    limit_mib = omex_storage.MAX_OMEX_BYTES // (1024 * 1024)
    return JSONResponse(
        status_code=413,
        content={"detail": f"Uploaded OMEX archive exceeds the {limit_mib} MiB limit"},
    )


def _is_multipart(headers: Headers) -> bool:
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return media_type == "multipart/form-data"


def _declared_length(headers: Headers) -> int | None:
    value = headers.get("content-length")
    return int(value) if value is not None and value.isdigit() else None


class MultipartBodyLimitMiddleware:
    """Reject a ``multipart/form-data`` body with 413 once it passes the bound.

    A declared ``Content-Length`` over the bound is refused before any body is
    read. Otherwise the bytes actually delivered are counted, so a missing or
    understated ``Content-Length`` cannot bypass it. Crossing the bound -- or the
    client disconnecting mid-body -- raises a ``MultiPartException`` inside the
    form parse, which makes Starlette close every temp file it spooled; the 400
    Starlette then renders is replaced with the 413 here.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not _is_multipart(headers):
            await self.app(scope, receive, send)
            return

        limit = multipart_body_limit()
        declared = _declared_length(headers)
        if declared is not None and declared > limit:
            await _too_large()(scope, receive, send)
            return

        received = 0
        body_done = False
        state: Literal["ok", "too_large", "aborted"] = "ok"
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, body_done, state
            message = await receive()
            if body_done:
                return message
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if not message.get("more_body", False):
                    body_done = True
                if received > limit:
                    state = "too_large"
                    raise _BodyLimitExceeded("Request body exceeds the upload limit.")
            elif message["type"] == "http.disconnect":
                # Routed through the same exception so the parser's temp files are
                # closed deterministically rather than left for garbage collection.
                state = "aborted"
                raise _BodyLimitExceeded("Client disconnected during upload.")
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if state == "too_large":
                if message["type"] == "http.response.start" and not response_started:
                    response_started = True
                    await _too_large()(scope, receive, send)
                return  # swallow the parser's 400 in favour of the 413 above
            if state == "aborted":
                return  # the client has gone away
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyLimitExceeded:
            # Only reachable when something other than the form parser read the
            # body (the parser converts this exception to an HTTPException).
            if state == "too_large" and not response_started:
                await _too_large()(scope, receive, send)
                return
            if state == "aborted":
                return
            raise
