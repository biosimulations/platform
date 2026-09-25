"""Fetch JSON inputs for explicit platform-owned contracts, without passthrough."""

import json
import logging
import time
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
    """Fetch one JSON object without caller headers or query parameters."""
    payload = await fetch_upstream_json_value(client, path, resource=resource, page=page)
    if not isinstance(payload, dict):
        raise HTTPException(502, f"The upstream service returned an unexpected {resource}.")
    return payload


async def _read_capped_body(response: httpx.Response, resource: str, limit: int) -> bytes:
    """Read the decoded body, refusing to buffer more than ``limit`` bytes.

    ``aiter_bytes`` yields *decoded* bytes, so a gzip bomb or a mis-declared
    Content-Length cannot get past the cap: what is measured is what would have
    to be held in memory and parsed. The stream is abandoned as soon as the cap
    is crossed, and the caller's ``async with`` closes it.
    """
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > limit:
            raise HTTPException(502, _OVERSIZE_DETAIL.format(resource=resource))
    return bytes(body)


async def fetch_upstream_json_value(
    client: httpx.AsyncClient, path: str, *, resource: str, page: str | None = None
) -> dict[str, Any] | list[Any]:
    """Fetch an object or array with the same isolated request and error policy.

    The body is streamed and capped at ``UPSTREAM_MAX_RESPONSE_BYTES`` decoded
    bytes rather than buffered whole and parsed: three or four such responses are
    assembled into one page, so an unbounded body is unbounded worker memory and
    synchronous CPU on the request path. Exceeding the cap is a sanitized 502 --
    never a truncated payload, and never a partially parsed contract.

    ``page`` labels the fetch for the page-phase instrumentation (a bounded
    enum-like string, e.g. ``"run"``); ordinary summary routes omit it.
    """
    limit = get_settings().upstream_max_response_bytes
    started = time.monotonic()
    try:
        async with client.stream("GET", path) as response:
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
