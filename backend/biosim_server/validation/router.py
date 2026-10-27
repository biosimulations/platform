"""Server-side relay for the COMBINE API's two validation endpoints.

**Why this exists.** ``combine.api.biosimulations.org`` returns no
``Access-Control-Allow-Origin`` header for any origin -- not for
``https://biosim.biosimulations.org``, not for ``http://localhost:3000``. Its
preflight answers 200 with no ``Access-Control-*`` headers at all, which a
browser treats as a denial. So the validate-* utility pages cannot reach it from
the client anywhere: production, deploy preview or dev. Relaying server-side
moves the request onto the Platform's own origin, which the frontend is already
allowed to call, and CORS stops being involved.

**What this is not.** It is a relay, not an owned contract. The validation report
is COMBINE's schema; we neither model nor validate it. By
``docs/legacy-api-passthrough-policy.md`` that needs justifying rather than
assuming, and the justification is the policy's first acceptability criterion:
the alternative is not a worse contract, it is a feature that cannot work in any
browser. The report shape also belongs to the COMBINE validators rather than to
us -- there is no Platform concept here to own. If the Platform ever grows its
own validation, this module is deleted rather than promoted.

**No caller input reaches the upstream path.** Both upstream paths are literals.
Unlike the summary and page endpoints there is no id to quote, so the
dot-segment class of bug (``upstream_url``) cannot arise here.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from biosim_server.common.ratelimit import validation_rate_limit
from biosim_server.config import get_settings
from biosim_server.dependencies import get_combine_http_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/validation", tags=["Validation"])

# Content-Type carries the multipart boundary, so it has to survive; Accept is
# harmless. Everything else is dropped -- in particular Authorization and
# Cookie. COMBINE is a public third-party validator with no notion of a
# Platform principal, so forwarding a caller's token would hand our users'
# credentials to a service that has no use for them. The frontend attaches the
# Platform token to Platform-bound requests, which includes these routes; this
# allowlist is what stops it leaving the process.
_REQUEST_HEADERS = frozenset({"content-type", "accept"})
_RESPONSE_HEADERS = frozenset({"content-type"})

_OVERSIZE_REQUEST = "The uploaded document is larger than this endpoint accepts."

_RESPONSES: dict[int | str, dict[str, str]] = {
    413: {"description": "The uploaded document exceeded the size limit."},
    429: {"description": "Validation rate limit exceeded."},
    502: {"description": "The validation service failed, was unreachable, or answered unreadably."},
    504: {"description": "Timed out while contacting the validation service."},
}

_DESCRIPTION = (
    "Relayed to the COMBINE API server-side. **Works with or without "
    "authentication**: a token, if present and valid, only raises the "
    "rate-limit ceiling, and one that cannot be validated is treated as no "
    "token rather than refused. The upstream status and body are returned "
    "unchanged for 2xx and 4xx -- the validation report *is* a 400 for an "
    "invalid document, so the body matters on failure. A 5xx becomes a "
    "sanitized 502. No caller credentials are forwarded upstream."
)


async def _read_capped_body(request: Request, limit: int) -> bytes:
    """Buffer the request body, refusing to exceed ``limit``.

    The declared length is checked first so an honest oversize upload is
    refused without reading it. A missing or understated Content-Length cannot
    get past the running count, which is checked *before* each chunk is kept.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, _OVERSIZE_REQUEST)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed Content-Length.") from None

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, _OVERSIZE_REQUEST)
        body.extend(chunk)
    return bytes(body)


async def _relay(
    client: httpx.AsyncClient, request: Request, upstream_path: str, resource: str
) -> Response:
    """Relay one validation POST. One attempt, no retry, no redirect following."""
    settings = get_settings()
    body = await _read_capped_body(request, settings.combine_max_request_bytes)

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _REQUEST_HEADERS
    }
    headers.setdefault("accept", "application/json")
    # Ask for identity so the response cap below counts the same bytes we
    # return. With a content encoding, a small compressed body could inflate
    # past the cap after it had already been accepted.
    headers["accept-encoding"] = "identity"

    limit = settings.combine_max_response_bytes
    try:
        async with client.stream(
            "POST", upstream_path, content=body, headers=headers, follow_redirects=False
        ) as upstream:
            if upstream.status_code >= 500:
                logger.warning(
                    "Validation service returned %s during %s", upstream.status_code, resource
                )
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    f"The validation service failed during the {resource}.",
                )
            payload = bytearray()
            if upstream.is_stream_consumed:
                # httpx.MockTransport hands back an already-buffered response, so
                # aiter_raw() would raise StreamConsumed. Real responses always
                # take the streaming path below. The cap is re-applied here
                # rather than skipped: the body is already in memory so this is
                # not a memory guard, but omitting it would leave the cap with a
                # hole that only tests could walk through -- and then the cap
                # would be untestable, which is how caps rot.
                payload.extend(upstream.content)
                if len(payload) > limit:
                    logger.warning("Validation service response exceeded the cap during %s", resource)
                    raise HTTPException(
                        status.HTTP_502_BAD_GATEWAY,
                        f"The validation service returned an oversized {resource} report.",
                    )
            else:
                async for chunk in upstream.aiter_raw():
                    if len(payload) + len(chunk) > limit:
                        logger.warning("Validation service response exceeded the cap during %s", resource)
                        raise HTTPException(
                            status.HTTP_502_BAD_GATEWAY,
                            f"The validation service returned an oversized {resource} report.",
                        )
                    payload.extend(chunk)
            response_headers = {
                name: value
                for name, value in upstream.headers.items()
                if name.lower() in _RESPONSE_HEADERS
            }
    except httpx.TimeoutException as exc:
        logger.warning("Validation service timeout during %s", resource)
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, f"Timed out while running the {resource}."
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("Validation service transport failure during %s", resource)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Could not reach the validation service for the {resource}.",
        ) from exc

    # Starlette recomputes Content-Length; the upstream value is not forwarded.
    return Response(
        content=bytes(payload), status_code=upstream.status_code, headers=response_headers
    )


@router.post(
    "/model",
    response_model=None,
    operation_id="validate-model",
    summary="Validate a model document via the COMBINE API",
    description=_DESCRIPTION,
    dependencies=[Depends(validation_rate_limit)],
    responses=_RESPONSES,
)
async def validate_model(
    request: Request,
    client: httpx.AsyncClient = Depends(get_combine_http_client),
) -> Response:
    """Relay ``POST /model/validate``. Body is the caller's multipart form."""
    return await _relay(client, request, "/model/validate", "model validation")


@router.post(
    "/sed-ml",
    response_model=None,
    operation_id="validate-sedml",
    summary="Validate a SED-ML document via the COMBINE API",
    description=_DESCRIPTION,
    dependencies=[Depends(validation_rate_limit)],
    responses=_RESPONSES,
)
async def validate_sedml(
    request: Request,
    client: httpx.AsyncClient = Depends(get_combine_http_client),
) -> Response:
    """Relay ``POST /sed-ml/validate``. Body is the caller's multipart form."""
    return await _relay(client, request, "/sed-ml/validate", "simulation validation")
