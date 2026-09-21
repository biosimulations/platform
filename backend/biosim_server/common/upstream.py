"""Fetch JSON inputs for explicit platform-owned contracts, without passthrough."""

import logging
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

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


async def fetch_upstream_json(
    client: httpx.AsyncClient, path: str, *, resource: str
) -> dict[str, Any]:
    """Fetch one JSON object without caller headers or query parameters."""
    payload = await fetch_upstream_json_value(client, path, resource=resource)
    if not isinstance(payload, dict):
        raise HTTPException(502, f"The upstream service returned an unexpected {resource}.")
    return payload


async def fetch_upstream_json_value(
    client: httpx.AsyncClient, path: str, *, resource: str
) -> dict[str, Any] | list[Any]:
    """Fetch an object or array with the same isolated request and error policy."""
    try:
        response = await client.get(path)
    except httpx.TimeoutException as exc:
        logger.warning("Upstream timeout while loading %s", resource)
        raise HTTPException(504, f"Timed out while loading the {resource}.") from exc
    except httpx.RequestError as exc:
        logger.warning("Upstream transport failure while loading %s", resource)
        raise HTTPException(502, f"Could not reach the upstream service for the {resource}.") from exc

    if response.status_code >= 500:
        logger.warning("Upstream returned %s while loading %s", response.status_code, resource)
        raise HTTPException(502, f"The upstream service failed while loading the {resource}.")
    if response.status_code == 404:
        raise HTTPException(404, "Not Found")
    if 400 <= response.status_code < 500:
        raise HTTPException(response.status_code, f"The upstream service could not load the {resource}.")
    detail = f"The upstream service returned an unexpected {resource}."
    if response.status_code != 200:
        raise HTTPException(502, detail)
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(502, detail) from exc
    if not isinstance(payload, (dict, list)):
        raise HTTPException(502, detail)
    return payload
