"""Fetch JSON inputs for explicit platform-owned contracts, without passthrough."""

import json
import logging
import time
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException, status

from biosim_server.config import get_settings

logger = logging.getLogger(__name__)

# quote() leaves "." alone -- it is RFC 3986 unreserved -- so a caller-supplied
# id arriving from Starlette as "." or ".." would build a real dot segment that
# httpx resolves away against its base_url ("/projects/../summary" -> "/summary").
# Neither is a valid upstream id, so reject rather than encode: encoding would
# depend on every intermediary preserving it instead of decode-and-normalizing.
DOT_SEGMENTS = frozenset({".", ".."})

# Per-phase upstream timeout: httpx applies this to each of connect/read/write/pool
# independently, so it bounds no request *in total* -- a slow-but-progressing body
# can legitimately outlive it. The shared client (dependencies.get_http_client) is
# built from this constant and pages/service.py derives its page budgets from the
# number of serial hops it implies, so the three cannot drift apart silently.
UPSTREAM_TIMEOUT_SECONDS = 30.0

# Consumer-facing detail for a body that exceeds the configured cap. Sanitized:
# it names the resource class and nothing about the limit, the body, or upstream.
_OVERSIZE_DETAIL = "The upstream service returned a {resource} that is too large to load."

# The complete header set of a platform-owned fetch. An uncompressed body is
# requested so that what is read is what is held; User-Agent is the value the
# pooled client sent before requests were built afresh.
_PUBLIC_FETCH_HEADERS = {
    "accept": "application/json",
    "accept-encoding": "identity",
    "user-agent": f"python-httpx/{httpx.__version__}",
}

# A server may compress regardless, so compressed bodies are inflated here rather
# than by httpx, which inflates each network read whole before its length can be
# checked: at ~1000x, one 64 KiB read became ~64 MiB in memory however small the
# cap. No inflate step may produce more than this.
_INFLATE_STEP_BYTES = 64 * 1024

# zlib window bits for each supported Content-Encoding: a gzip header or a zlib
# wrapper. Anything else, including stacked codings, is refused (see _inflater).
_INFLATE_WBITS = {
    "gzip": zlib.MAX_WBITS | 16,
    "x-gzip": zlib.MAX_WBITS | 16,
    "deflate": zlib.MAX_WBITS,
}


class _UndecodableBody(Exception):
    """An unsupported Content-Encoding, or compressed bytes that do not inflate."""


def upstream_url(*segments: str) -> str:
    """Build an upstream path with every segment quoted independently.

    Raises 404 for a dot-only segment, before any URL exists to request.
    """
    for segment in segments:
        if segment in DOT_SEGMENTS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not Found",
            )
    return "/" + "/".join(quote(segment, safe="") for segment in segments)


def _log_fetch(
    resource: str,
    outcome: str,
    *,
    page: str | None,
    started: float,
    failure: bool,
    bytes_read: int | None = None,
) -> None:
    """One bounded record per upstream fetch (SHARED-MIN-001).

    Fields are a fixed vocabulary -- resource class, outcome, duration, decoded
    bytes, and (only for page assembly) which page -- so the formatter can pass
    them through and an operator can sum byte counts per page. Deliberately no
    upstream URL, resource id, response body, or caller identity: the same
    discipline the auth path applies to claims.
    """
    extra: dict[str, Any] = {
        "upstream_resource": resource,
        "upstream_outcome": outcome,
        "upstream_duration_ms": int((time.monotonic() - started) * 1000),
    }
    if page is not None:
        extra["page"] = page
    if bytes_read is not None:
        extra["upstream_bytes"] = bytes_read
    logger.log(logging.WARNING if failure else logging.INFO, "Upstream fetch", extra=extra)


def _failure(
    status_code: int,
    detail: str,
    *,
    resource: str,
    outcome: str,
    page: str | None,
    started: float,
    bytes_read: int | None = None,
) -> HTTPException:
    """Log a failed fetch and build the exception to raise for it."""
    _log_fetch(resource, outcome, page=page, started=started, failure=True, bytes_read=bytes_read)
    return HTTPException(status_code, detail)


async def fetch_upstream_json(
    client: httpx.AsyncClient, path: str, *, resource: str, page: str | None = None
) -> dict[str, Any]:
    """Fetch one JSON object without caller headers, query parameters or pooled-client state."""
    payload = await fetch_upstream_json_value(client, path, resource=resource, page=page)
    if not isinstance(payload, dict):
        raise HTTPException(502, f"The upstream service returned an unexpected {resource}.")
    return payload


@asynccontextmanager
async def _public_stream(client: httpx.AsyncClient, path: str) -> AsyncIterator[httpx.Response]:
    """Stream one GET that carries nothing from the pooled client but its pool.

    httpx's ``send`` stores every upstream ``Set-Cookie`` in the client's jar, and
    a pooled client serves many callers (this one was once shared with the legacy
    runs proxy, which now has its own). ``client.stream`` would replay that jar,
    plus the client's default headers, auth and query parameters, on this
    anonymous request. So the request is built afresh: the client contributes only
    its base URL (stripped of query defaults and credentials) and timeout, and the
    jar is never read. Clearing the jar instead would race concurrent requests.
    """
    url = client.build_request("GET", path).url.copy_with(query=None, userinfo=b"")
    request = httpx.Request(
        "GET", url, headers=_PUBLIC_FETCH_HEADERS, extensions={"timeout": client.timeout.as_dict()},
    )
    response = await client.send(request, stream=True, follow_redirects=False, auth=None)
    try:
        yield response
    finally:
        await response.aclose()


def _inflater(content_encoding: str) -> "zlib._Decompress | None":
    """A decompressor for the response's coding, or None for an uncompressed body.

    Only what httpx itself decoded unconditionally is supported (gzip, deflate),
    plus the registered ``x-gzip`` alias. A coding nobody asked for (``br`` and the
    like) or a stack of them is an unexpected body, never guessed at.
    """
    codings = [coding.strip().lower() for coding in content_encoding.split(",")]
    codings = [coding for coding in codings if coding and coding != "identity"]
    if not codings:
        return None
    if len(codings) == 1 and codings[0] in _INFLATE_WBITS:
        return zlib.decompressobj(_INFLATE_WBITS[codings[0]])
    raise _UndecodableBody("unsupported Content-Encoding")


async def _read_capped_body(response: httpx.Response, resource: str, limit: int) -> bytes:
    """Read the decoded body without ever holding more than ``limit`` bytes of it.

    The raw stream is read and any compression inflated at most
    ``_INFLATE_STEP_BYTES`` at a time, each step checked against the cap before it
    is kept. Peak memory is therefore the cap plus one step, whatever the
    compression ratio, and Content-Length decides nothing. The stream is abandoned
    as soon as the cap is crossed, and the caller's ``async with`` closes it.
    """
    if response.is_stream_consumed:
        # MockTransport may supply an already buffered (and decoded) body. Real
        # responses always take the stream path below; checking here keeps the
        # cap from having a test-only hole.
        if len(response.content) > limit:
            raise HTTPException(502, _OVERSIZE_DETAIL.format(resource=resource))
        return response.content
    inflater = _inflater(response.headers.get("content-encoding", ""))
    body = bytearray()

    def keep(chunk: bytes) -> None:
        # Checked before appending, so the buffer itself never exceeds ``limit``.
        if len(body) + len(chunk) > limit:
            raise HTTPException(502, _OVERSIZE_DETAIL.format(resource=resource))
        body.extend(chunk)

    async for raw in response.aiter_raw():
        if inflater is None:
            keep(raw)
            continue
        pending = raw
        while pending:
            try:
                chunk = inflater.decompress(pending, _INFLATE_STEP_BYTES)
            except zlib.error as exc:
                raise _UndecodableBody("compressed body does not inflate") from exc
            if not chunk and inflater.unconsumed_tail == pending:
                raise _UndecodableBody("compressed body made no progress")
            pending = inflater.unconsumed_tail
            keep(chunk)
    if inflater is not None:
        # A step can stop mid-output with every input byte consumed; drain it.
        # A truncated stream simply ends short and fails JSON validation.
        while not inflater.eof and (chunk := inflater.decompress(b"", _INFLATE_STEP_BYTES)):
            keep(chunk)
    return bytes(body)


async def fetch_upstream_json_value(
    client: httpx.AsyncClient, path: str, *, resource: str, page: str | None = None
) -> dict[str, Any] | list[Any]:
    """Fetch an object or array with the same isolated request and error policy.

    The body is streamed and capped at ``UPSTREAM_MAX_RESPONSE_BYTES`` decoded
    bytes -- inflated in bounded steps if compressed -- rather than buffered whole
    and parsed: three or four such responses are
    assembled into one page, so an unbounded body is unbounded worker memory and
    synchronous CPU on the request path. Exceeding the cap is a sanitized 502 --
    never a truncated payload, and never a partially parsed contract.

    ``page`` labels the fetch for the page-phase instrumentation (a bounded
    enum-like string, e.g. ``"run"``); ordinary summary routes omit it.
    """
    limit = get_settings().upstream_max_response_bytes
    started = time.monotonic()
    try:
        async with _public_stream(client, path) as response:
            if response.status_code >= 500:
                raise _failure(
                    502, f"The upstream service failed while loading the {resource}.",
                    resource=resource, outcome="upstream_error", page=page, started=started,
                )
            if response.status_code == 404:
                raise _failure(
                    404, "Not Found",
                    resource=resource, outcome="not_found", page=page, started=started,
                )
            if 400 <= response.status_code < 500:
                raise _failure(
                    response.status_code,
                    f"The upstream service could not load the {resource}.",
                    resource=resource, outcome="client_error", page=page, started=started,
                )
            detail = f"The upstream service returned an unexpected {resource}."
            if response.status_code != 200:
                raise _failure(
                    502, detail,
                    resource=resource, outcome="unexpected_status", page=page, started=started,
                )
            try:
                body = await _read_capped_body(response, resource, limit)
            except _UndecodableBody as exc:
                raise _failure(
                    502, detail,
                    resource=resource, outcome="invalid_body", page=page, started=started,
                ) from exc
            except HTTPException:
                _log_fetch(resource, "too_large", page=page, started=started, failure=True, bytes_read=limit)
                raise
    except httpx.TimeoutException as exc:
        raise _failure(
            504, f"Timed out while loading the {resource}.",
            resource=resource, outcome="timeout", page=page, started=started,
        ) from exc
    except httpx.RequestError as exc:
        raise _failure(
            502, f"Could not reach the upstream service for the {resource}.",
            resource=resource, outcome="transport_error", page=page, started=started,
        ) from exc

    try:
        payload = json.loads(body)
    except ValueError as exc:
        _log_fetch(resource, "invalid_body", page=page, started=started, failure=True, bytes_read=len(body))
        raise HTTPException(502, detail) from exc
    if not isinstance(payload, (dict, list)):
        _log_fetch(resource, "invalid_body", page=page, started=started, failure=True, bytes_read=len(body))
        raise HTTPException(502, detail)
    _log_fetch(resource, "ok", page=page, started=started, failure=False, bytes_read=len(body))
    return payload
