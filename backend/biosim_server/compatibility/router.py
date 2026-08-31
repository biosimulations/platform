"""FastAPI router for OMEX compatibility checking."""

import hashlib
import logging

import aiohttp
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from yarl import URL

from biosim_server.biosim_omex.omex_storage import get_cached_omex_file_from_raw
from biosim_server.common.auth.auth0 import AuthenticatedUser, get_optional_user
from biosim_server.compatibility.models import CompatibilityResponse
from biosim_server.compatibility.omex_parser import parse_omex_content
from biosim_server.compatibility.simulator_matcher import find_compatible_simulators
from biosim_server.compatibility.url_guard import MAX_REDIRECTS, BlockedUrlError, assert_fetchable_url
from biosim_server.dependencies import get_biosim_service, get_file_service, get_omex_database_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compatibility", tags=["Compatibility"])


@router.post(
    "/check",
    response_model=CompatibilityResponse,
    operation_id="check-compatibility",
    summary="Check OMEX archive compatibility with simulators",
    description="Upload an OMEX archive or provide a URL to find compatible simulators based on model format and algorithm requirements."
)
async def check_compatibility(
    uploaded_file: UploadFile | None = File(None, description="OMEX/COMBINE archive to check for compatibility"),
    archive_url: str | None = Query(None, description="URL to an OMEX/COMBINE archive (alternative to file upload)"),
    verbose: bool = Query(False, description="Include per-version algorithm and ontology details"),
    user: AuthenticatedUser | None = Depends(get_optional_user),
) -> CompatibilityResponse:
    """Check which simulators can run the given OMEX archive.

    Provide either an uploaded file or an archive URL. Analyzes the OMEX
    archive to extract model formats and required simulation algorithms,
    then matches against available biosimulator capabilities.
    """
    # Get file content from upload or URL
    if uploaded_file is not None:
        try:
            file_content = await uploaded_file.read()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {e}")
    elif archive_url is not None:
        try:
            file_content = await _download_archive(archive_url)
        except BlockedUrlError as e:
            raise HTTPException(status_code=400, detail=f"Refusing to fetch archive URL: {e}")
        except aiohttp.ClientError as e:
            raise HTTPException(status_code=400, detail=f"Failed to download archive from URL: {e}")
    else:
        raise HTTPException(status_code=400, detail="Provide either uploaded_file or archive_url")

    omex_id = hashlib.md5(file_content).hexdigest()

    # Parse OMEX content
    try:
        omex_content = parse_omex_content(file_content)
    except Exception as e:
        logger.error(f"Failed to parse OMEX file: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to parse OMEX archive: {e}")

    if not omex_content.sedml_files:
        raise HTTPException(status_code=400, detail="No SED-ML files found in the OMEX archive")

    if not omex_content.simulations:
        if omex_content.parse_errors:
            raise HTTPException(
                status_code=400,
                detail="No simulations could be read from the SED-ML files: "
                       + "; ".join(omex_content.parse_errors),
            )
        raise HTTPException(status_code=400, detail="No simulations found in the SED-ML files")

    # Cache the OMEX file to database + GCS so it can be used by /simulations/run.
    # Ownership is stamped server-side from the verified token: anonymous ingest
    # (the frontend's run wizard) yields owner_sub=None + visibility=public, an
    # authenticated ingest yields the caller's own private resource. An invalid
    # bearer token 401s in the dependency rather than quietly creating a public
    # cache entry as if it were anonymous.
    file_service = get_file_service()
    omex_database = get_omex_database_service()
    if file_service is not None and omex_database is not None:
        filename = uploaded_file.filename if uploaded_file is not None else f"{omex_id}.omex"
        try:
            await get_cached_omex_file_from_raw(
                file_service, omex_database, file_content, filename,
                owner_sub=user.sub if user is not None else None,
            )
        except Exception as e:
            logger.warning(f"Failed to cache OMEX file: {e}", exc_info=True)

    # Get available simulators
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(status_code=503, detail="Biosim service not available")

    try:
        simulator_versions = await biosim_service.get_simulator_versions()
    except Exception as e:
        logger.error(f"Failed to fetch simulator versions: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to fetch simulator information: {e}")

    # Find compatible simulators
    eligible = await find_compatible_simulators(omex_content, simulator_versions, verbose=verbose)

    return CompatibilityResponse(
        omex_id=omex_id,
        omex_content=omex_content,
        eligible_simulators=eligible,
    )


async def _download_archive(archive_url: str) -> bytes:
    """Fetch a caller-supplied archive URL, re-validating every redirect hop.

    aiohttp's own redirect handling is disabled: a public URL that 302s to
    ``http://169.254.169.254/`` would otherwise walk straight past the
    pre-flight check. Each hop goes back through assert_fetchable_url instead.
    """
    url = archive_url
    # Validated before any connection is opened, so a blocked URL costs nothing.
    await assert_fetchable_url(url)
    async with aiohttp.ClientSession() as session:
        for _ in range(MAX_REDIRECTS + 1):
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise BlockedUrlError("redirect response with no Location header")
                    url = str(URL(url).join(URL(location)))
                    await assert_fetchable_url(url)
                    continue
                if resp.status != 200:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to download archive from URL: HTTP {resp.status}",
                    )
                return await resp.read()
    raise BlockedUrlError(f"too many redirects (>{MAX_REDIRECTS})")
