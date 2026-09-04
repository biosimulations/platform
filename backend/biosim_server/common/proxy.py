"""Small, deliberately narrow helpers for proxying public upstream GETs.

Stop-gap. These helpers exist for exactly two endpoints -- ``GET
/projects/{id}/summary`` and ``GET /runs/{run_id}/summary`` -- so the frontend
can move off the upstream API without waiting on a schema migration. Replacing
them with datamodels this repository owns is tracked in
https://github.com/biosimulations/platform/issues/108. Do not add further
endpoint families to ``proxy_get`` without revisiting that decision first.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Response, status

logger = logging.getLogger(__name__)

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Location is required to mirror redirects. The remaining cache/download headers
# are safe response metadata and useful to callers without exposing credentials.
SAFE_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-disposition",
        "content-type",
        "etag",
        "expires",
        "last-modified",
        "location",
        # Forwarded so the cache metadata above stays correct if a shared cache
        # is ever placed in front of this API.
        "vary",
    }
)


# quote() leaves "." alone -- it is RFC 3986 unreserved -- so a caller-supplied
# id arriving from Starlette as "." or ".." would build a real dot segment that
# httpx resolves away against its base_url ("/projects/../summary" -> "/summary").
# Neither is a valid upstream id, so reject rather than encode: encoding would
# depend on every intermediary preserving it instead of decode-and-normalizing.
DOT_SEGMENTS = frozenset({".", ".."})


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


def _connection_tokens(headers: httpx.Headers) -> set[str]:
    tokens: set[str] = set()
    for value in headers.get_list("connection"):
        tokens.update(token.strip().lower() for token in value.split(",") if token.strip())
    return tokens


# Our own CORSMiddleware owns Origin variance for this API and appends its own
# "Origin" token via Headers.add_vary_header, which concatenates blindly. The
# upstream's Vary: Origin describes the upstream's CORS policy, not ours, so
# forwarding it would only produce a duplicate token.
MIDDLEWARE_OWNED_VARY = frozenset({"origin"})


def _forwarded_vary(value: str) -> str | None:
    """Narrow an upstream Vary to the field names this API is responsible for."""
    kept: list[str] = []
    seen: set[str] = set()
    for token in (token.strip() for token in value.split(",")):
        lowered = token.lower()
        if not token or lowered in MIDDLEWARE_OWNED_VARY or lowered in seen:
            continue
        seen.add(lowered)
        kept.append(token)
    return ", ".join(kept) or None


def _safe_response_headers(headers: httpx.Headers) -> dict[str, str]:
    blocked = HOP_BY_HOP | _connection_tokens(headers)
    forwarded: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered not in SAFE_RESPONSE_HEADERS or lowered in blocked:
            continue
        if lowered == "vary":
            narrowed = _forwarded_vary(value)
            if narrowed is None:
                continue
            value = narrowed
        forwarded[name] = value
    return forwarded


def _target(path: str, query: bytes) -> str:
    if not query:
        return path
    # ASGI exposes the original percent-encoded query as bytes. Latin-1 is a
    # one-to-one decode, avoiding dict parsing that would discard order/repeats.
    return f"{path}?{query.decode('latin-1')}"


async def proxy_get(
    client: httpx.AsyncClient,
    upstream_path: str,
    *,
    query: bytes,
    resource: str,
) -> Response:
    """Proxy one GET without forwarding caller headers or transforming its body."""
    try:
        downstream = await client.get(
            _target(upstream_path, query),
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        logger.warning("Upstream timeout while loading %s", resource)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Timed out while loading the {resource}.",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("Upstream transport failure while loading %s", resource)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach the upstream service for the {resource}.",
        ) from exc

    if downstream.status_code >= 500:
        logger.warning("Upstream returned %s while loading %s", downstream.status_code, resource)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The upstream service failed while loading the {resource}.",
        )

    return Response(
        content=downstream.content,
        status_code=downstream.status_code,
        headers=_safe_response_headers(downstream.headers),
    )
