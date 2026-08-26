"""FastAPI router for OMEX compatibility checking."""

import hashlib
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import aiohttp
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from biosim_server.biosim_omex.omex_storage import get_cached_omex_file_from_raw
from biosim_server.common.ratelimit import compatibility_rate_limit
from biosim_server.compatibility.models import CompatibilityResponse
from biosim_server.compatibility.omex_parser import parse_omex_content
from biosim_server.compatibility.simulator_matcher import find_compatible_simulators
from biosim_server.dependencies import get_biosim_service, get_file_service, get_omex_database_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compatibility", tags=["Compatibility"])

_MAX_ARCHIVE_REDIRECTS = 5


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_archive_url_safe(url: str) -> None:
    """Reject ``archive_url`` values that could be used for SSRF.

    Allows only http/https, no userinfo, and a host that resolves exclusively
    to public unicast addresses (no loopback, RFC1918, link-local/metadata).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="archive_url must be an http or https URL")
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(status_code=400, detail="archive_url must not include userinfo")
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="archive_url is missing a host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="archive_url host could not be resolved") from exc
    if not infos:
        raise HTTPException(status_code=400, detail="archive_url host could not be resolved")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_disallowed_ip(ip):
            raise HTTPException(
                status_code=400,
                detail="archive_url must not target a private or reserved address",
            )


async def _download_archive(url: str) -> bytes:
    current = url
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession() as session:
        for _ in range(_MAX_ARCHIVE_REDIRECTS + 1):
            assert_archive_url_safe(current)
            try:
                async with session.get(
                    current, timeout=timeout, allow_redirects=False
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            raise HTTPException(
                                status_code=400,
                                detail="Failed to download archive from URL: redirect without Location",
                            )
                        current = urljoin(current, location)
                        continue
                    if resp.status != 200:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Failed to download archive from URL: HTTP {resp.status}",
                        )
                    return await resp.read()
            except HTTPException:
                raise
            except aiohttp.ClientError as e:
                raise HTTPException(
                    status_code=400, detail=f"Failed to download archive from URL: {e}"
                ) from e
        raise HTTPException(status_code=400, detail="Failed to download archive from URL: too many redirects")


@router.post(
    "/check",
    response_model=CompatibilityResponse,
    operation_id="check-compatibility",
    summary="Check OMEX archive compatibility with simulators",
    description="Upload an OMEX archive or provide a URL to find compatible simulators based on model format and algorithm requirements.",
    dependencies=[Depends(compatibility_rate_limit)],
)
async def check_compatibility(
    uploaded_file: UploadFile | None = File(None, description="OMEX/COMBINE archive to check for compatibility"),
    archive_url: str | None = Query(None, description="URL to an OMEX/COMBINE archive (alternative to file upload)"),
    verbose: bool = Query(False, description="Include per-version algorithm and ontology details"),
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
        file_content = await _download_archive(archive_url)
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

    # Cache the OMEX file to database + GCS so it can be used by /simulations/run
    file_service = get_file_service()
    omex_database = get_omex_database_service()
    if file_service is not None and omex_database is not None:
        filename = uploaded_file.filename if uploaded_file is not None else f"{omex_id}.omex"
        try:
            await get_cached_omex_file_from_raw(file_service, omex_database, file_content, filename)
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
