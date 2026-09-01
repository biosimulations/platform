"""Shared upstream-error mapping for the biosimulations.org proxy routes.

Every passthrough route translates aiohttp failures the same way, and that
translation carries real decisions (a 4xx is the *caller's* bad id, not a broken
gateway; transport failures must not echo the upstream host into a public
response body). Keeping it in one place stops those decisions from drifting
apart across routes as they are copy-pasted.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import aiohttp
from aiohttp import ClientResponseError
from fastapi import HTTPException

logger = logging.getLogger(__name__)


@contextmanager
def upstream_errors(
    *, resource: str, identifier: str, not_found_detail: str, bad_request_subject: str
) -> Iterator[None]:
    """Map biosimulations.org client failures onto platform HTTP status codes.

    * upstream 404          -> 404 with ``not_found_detail``
    * other upstream 4xx    -> forwarded verbatim; a malformed id upstream-400s,
                               and calling that 502 would blame our gateway for
                               the caller's request
    * upstream 5xx          -> 502 (a real gateway failure on our side of the call)
    * transport failure     -> 502, logged with detail but answered without it:
                               the exception text names the upstream host/port,
                               which does not belong in a public response body
    """
    try:
        yield
    except ClientResponseError as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail=not_found_detail)
        if 400 <= e.status < 500:
            raise HTTPException(
                status_code=e.status,
                detail=f"Upstream rejected {bad_request_subject}: HTTP {e.status}",
            )
        logger.warning(f"Upstream {resource} failed for {identifier}: {e}")
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch {resource}: HTTP {e.status}"
        )
    except aiohttp.ClientError as e:
        logger.warning(f"Upstream {resource} unreachable for {identifier}: {e}", exc_info=e)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch {resource} from the biosimulations.org API",
        )
