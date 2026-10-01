"""Opaque legacy run proxying, separate from platform-owned JSON contracts."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Literal

import anyio
import httpx
from fastapi import HTTPException, Request, Response
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from biosim_server.common.upstream import upstream_url
from biosim_server.config import get_settings

logger = logging.getLogger(__name__)
Operation = Literal["get", "update", "delete", "download", "validate", "summary"]
# Match the existing gke/rke ingress 20m ceiling. This is an inbound PATCH
# policy, independent of the platform-owned JSON response cap. Check actual
# chunks before sending anything upstream, even without Content-Length.
LEGACY_PATCH_MAX_BYTES = 20 * 1024 * 1024
_REQUEST_HEADERS = frozenset({"authorization", "accept"})
_READ_HEADERS = frozenset({"if-none-match", "if-modified-since"})
_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-disposition",
        "content-length",
        "content-range",
        "accept-ranges",
        "etag",
        "last-modified",
        "cache-control",
        "expires",
        "vary",
        "location",
        "retry-after",
        "content-encoding",
    }
)
# Raw iteration preserves compressed bytes; Content-Encoding must accompany
# them, keeping lengths, ranges and validators valid even if identity is ignored.
_proxy_active: ContextVar[bool] = ContextVar("legacy_proxy_active", default=False)


class _SuppressProxyURL(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx's INFO request line includes the full URL and query. Suppress
        # only our outbound calls, without changing unrelated client logging.
        return not _proxy_active.get()


logging.getLogger("httpx").addFilter(_SuppressProxyURL())


def _headers(headers: httpx.Headers, allowed: frozenset[str]) -> dict[str, str]:
    # Connection can nominate otherwise end-to-end headers as hop-by-hop.
    nominated = {
        part.strip().lower() for part in headers.get("connection", "").split(",")
    }
    return {
        name: value for name, value in headers.items() if name in allowed - nominated
    }


async def _patch_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > LEGACY_PATCH_MAX_BYTES:
            raise HTTPException(413, "Legacy run PATCH body exceeds the 20 MiB limit.")
        body.extend(chunk)
    return bytes(body)


class _Transfer:
    def __init__(self, operation: Operation) -> None:
        self.operation = operation
        self.started = time.monotonic()
        self.status: int | None = None
        self.size = 0
        self.outcome = "ok"
        self.closed = False

    def log(self) -> None:
        logger.info(
            "Legacy runs proxy",
            extra={
                "legacy_operation": self.operation,
                "legacy_outcome": self.outcome,
                "legacy_status": self.status,
                "legacy_duration_ms": int((time.monotonic() - self.started) * 1000),
                "legacy_bytes": self.size,
            },
        )

    async def close(self, response: httpx.Response) -> None:
        if not self.closed:
            self.closed = True
            try:
                # Starlette disconnect cancellation uses an AnyIO cancel scope.
                # Shield socket cleanup from that scope before re-raising.
                with anyio.CancelScope(shield=True):
                    await response.aclose()
            finally:
                self.log()


# Consumer-facing detail for a buffered response that exceeds the configured
# cap. Sanitized: it names nothing about the limit, the size, the body, or the
# upstream. Distinct wording from common.upstream._OVERSIZE_DETAIL because the
# two are different failures - a platform-owned contract could not be loaded,
# versus an opaque response the proxy is refusing to relay truncated.
_OVERSIZE_DETAIL = "The legacy runs service returned a response that is too large to relay."


async def _read_capped_raw(
    upstream: httpx.Response, transfer: _Transfer, limit: int
) -> bytes:
    """Buffer the relayed body, refusing to hold more than ``limit`` raw bytes.

    Counted on ``aiter_raw``, unlike ``common.upstream._read_capped_body``'s
    ``aiter_bytes``: the proxy relays raw bytes and never decodes them, which is
    what keeps Content-Encoding, Content-Length, Content-Range and ETag valid.
    The raw length *is* the memory cost, so this bounds what it actually holds -
    a compressed body is measured on the wire and costs this worker only its
    compressed size.

    Content-Length is not consulted: one that overstates the body would reject
    a legitimate small response, and one that understates it changes nothing,
    because the decision is made from the bytes actually read. The stream is
    abandoned the moment the cap is crossed and the caller's ``finally`` closes
    it, so a breach never buffers the oversized remainder.
    """
    body = bytearray()
    async for chunk in upstream.aiter_raw():
        transfer.size += len(chunk)
        # Checked before appending (as _patch_body does), so the buffer itself
        # never holds more than ``limit`` bytes.
        if len(body) + len(chunk) > limit:
            transfer.outcome = "too_large"
            raise HTTPException(502, _OVERSIZE_DETAIL)
        body += chunk
    return bytes(body)


class _DownloadResponse(StreamingResponse):
    """Own the upstream even if downstream send fails before iteration starts."""

    def __init__(self, upstream: httpx.Response, transfer: _Transfer) -> None:
        self.upstream = upstream
        self.transfer = transfer
        headers = _headers(upstream.headers, _RESPONSE_HEADERS)
        if upstream.status_code == 204:
            headers.pop("content-length", None)
        super().__init__(
            self.chunks(), status_code=upstream.status_code, headers=headers
        )

    async def chunks(self) -> AsyncIterator[bytes]:
        try:
            if self.upstream.status_code not in (204, 304):
                async for chunk in self.upstream.aiter_raw():
                    self.transfer.size += len(chunk)
                    yield chunk
        except asyncio.CancelledError:
            self.transfer.outcome = "cancelled"
            raise
        except Exception:
            # Headers are already sent: abort the stream, never fabricate a
            # second response or expose transport exception text to the caller.
            self.transfer.outcome = "stream_error"
            raise
        finally:
            await self.transfer.close(self.upstream)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if not self.transfer.closed:
                self.transfer.outcome = "cancelled"
            await self.transfer.close(self.upstream)


async def proxy_run(
    client: httpx.AsyncClient,
    request: Request,
    operation: Operation,
    *segments: str,
) -> Response:
    """One attempt, caller credentials only, no redirect following or JSON parsing."""
    transfer = _Transfer(operation)
    upstream: httpx.Response | None = None
    handed_off = False
    token = _proxy_active.set(True)
    try:
        path = upstream_url("runs", *segments)
        body = await _patch_body(request) if operation == "update" else b""
        allowed = _REQUEST_HEADERS
        if request.method == "GET":
            allowed |= _READ_HEADERS
        if operation == "download":
            allowed |= {"range"}
        if operation == "update":
            allowed |= {"content-type"}
        headers = _headers(httpx.Headers(request.headers.raw), allowed)
        headers["accept-encoding"] = "identity"
        # Use the pooled client's base URL/timeout, but do not inherit its cookie
        # jar, default credentials, default query parameters or custom headers.
        url = client.build_request(request.method, path).url.copy_with(
            query=request.scope["query_string"] or None
        )
        outbound = httpx.Request(
            request.method,
            url,
            headers=headers,
            content=body,
            extensions={"timeout": client.timeout.as_dict()},
        )
        upstream = await client.send(
            outbound, stream=True, follow_redirects=False, auth=None
        )
        transfer.status = upstream.status_code
        transfer.outcome = (
            "upstream_error"
            if upstream.status_code >= 500
            else "client_error"
            if upstream.status_code >= 400
            else "ok"
        )
        if operation == "download":
            result = _DownloadResponse(upstream, transfer)
            handed_off = True
            return result
        response_headers = _headers(upstream.headers, _RESPONSE_HEADERS)
        if upstream.status_code in (204, 304):
            response_body = b""
            if upstream.status_code == 204:
                response_headers.pop("content-length", None)
        else:
            limit = get_settings().upstream_max_response_bytes
            if upstream.is_stream_consumed:
                # MockTransport may supply an already buffered response. Real
                # responses always take the raw stream path below. Already in
                # memory, so this is a correctness guard, not a memory guard -
                # but leaving it out would give the cap a second, test-only hole.
                response_body = upstream.content
                transfer.size = len(response_body)
                if transfer.size > limit:
                    transfer.outcome = "too_large"
                    raise HTTPException(502, _OVERSIZE_DETAIL)
            else:
                response_body = await _read_capped_raw(upstream, transfer, limit)
            # Starlette computes the actual length; the upstream value must go
            # either way now that a relayed body is known to be bounded.
            response_headers.pop("content-length", None)
        return Response(
            response_body, status_code=upstream.status_code, headers=response_headers
        )
    except httpx.TimeoutException as exc:
        transfer.outcome = "timeout"
        raise HTTPException(
            504, "Timed out while contacting the legacy runs service."
        ) from exc
    except httpx.RequestError as exc:
        transfer.outcome = "transport_error"
        raise HTTPException(502, "Could not reach the legacy runs service.") from exc
    except asyncio.CancelledError:
        transfer.outcome = "cancelled"
        raise
    except HTTPException:
        # A specific outcome (the buffered-body cap's `too_large`) is recorded at
        # its raise site; do not relabel it. A bare HTTPException here is
        # upstream_url's 404 or the inbound PATCH 413, both client errors.
        if transfer.outcome == "ok":
            transfer.outcome = "client_error"
        raise
    finally:
        _proxy_active.reset(token)
        if not handed_off:
            if upstream is not None:
                await transfer.close(upstream)
            else:
                transfer.log()
