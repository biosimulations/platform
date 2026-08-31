"""Routes for the BioSim DB project search API.

Two decoupled GET endpoints replace the legacy ``/projects/summary_filtered``
so paging through slim results is independent of the heavier facet computation
(see ``docs/project-search-api-plan.md``).

``GET /{id}/summary`` is different in kind: the project *detail* contract still
belongs to biosimulations.org, so that route is a passthrough to the upstream
API rather than a view over the platform's own project data.
"""

import json
import logging
from typing import Any

import aiohttp
from aiohttp import ClientResponseError
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import ValidationError

from biosim_server.config import get_settings
from biosim_server.dependencies import get_biosim_service, get_project_database_service
from biosim_server.projects.models import (
    ProjectQueryStat,
    ProjectSearchFilter,
    ProjectStubPage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Projects"])


def require_reindex_token(authorization: str | None = Header(default=None)) -> None:
    """Guard POST /projects/reindex with a bearer token.

    Disabled by default: with no token configured the endpoint returns 503, so
    it can't be triggered over the public ingress. The routine rebuild runs as an
    in-cluster CronJob (direct Mongo), not through this endpoint."""
    token = get_settings().project_reindex_token
    if not token:
        raise HTTPException(status_code=503, detail="reindex endpoint disabled (no token configured)")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid or missing reindex token")


def _parse_filters(filters_json: str) -> list[ProjectSearchFilter]:
    """Parse the ``filters`` query param (JSON array of ProjectSearchFilter).

    An empty/blank value means "no filters". Malformed JSON or a shape mismatch
    is a client error (400), not a 500."""
    if not filters_json or not filters_json.strip():
        return []
    try:
        raw = json.loads(filters_json)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"filters is not valid JSON: {e}")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="filters must be a JSON array")
    try:
        return [ProjectSearchFilter.model_validate(item) for item in raw]
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"invalid filter item: {e}")


@router.get(
    "",
    response_model=ProjectStubPage,
    operation_id="list-projects",
    summary="Search published projects (paginated slim results)",
)
async def list_projects(
    page: int = Query(default=1, ge=1, description="1-indexed page number."),
    perPage: int = Query(default=20, ge=1, le=200, description="Results per page."),
    filters: str = Query(default="", description="JSON array of {target, allowable_values}."),
    searchTerm: str = Query(default="", description="Free-text search across title/abstract/description."),
) -> ProjectStubPage:
    projects_db = get_project_database_service()
    if projects_db is None:
        raise HTTPException(status_code=503, detail="Project database service not available")

    parsed_filters = _parse_filters(filters)
    stubs, total = await projects_db.query_project_stubs(
        page=page, per_page=perPage, filters=parsed_filters, search_term=searchTerm.strip()
    )
    return ProjectStubPage(items=stubs, total=total)


@router.post(
    "/reindex",
    operation_id="reindex-projects",
    dependencies=[Depends(require_reindex_token)],
    summary="Rebuild the platform project search index from source collections",
)
async def reindex_projects() -> dict[str, int]:
    projects_db = get_project_database_service()
    rebuild = getattr(projects_db, "rebuild_index", None)
    if projects_db is None or rebuild is None:
        raise HTTPException(status_code=503, detail="Project search index not available")
    count = await rebuild()
    return {"indexed": count}


@router.get(
    "/stats",
    response_model=list[ProjectQueryStat],
    operation_id="list-project-stats",
    summary="Facet counts (tags & categories) for a project search",
)
async def list_project_stats(
    filters: str = Query(default="", description="JSON array of {target, allowable_values}."),
    searchTerm: str = Query(default="", description="Free-text search across title/abstract/description."),
) -> list[ProjectQueryStat]:
    projects_db = get_project_database_service()
    if projects_db is None:
        raise HTTPException(status_code=503, detail="Project database service not available")

    parsed_filters = _parse_filters(filters)
    return await projects_db.query_project_stats(
        filters=parsed_filters, search_term=searchTerm.strip()
    )


@router.get(
    "/{project_id}/summary",
    operation_id="get-project-summary",
    summary="Full project summary (passthrough to the biosimulations.org API)",
)
async def get_project_summary(project_id: str) -> dict[str, Any]:
    """Proxy ``GET /projects/{id}/summary`` to the biosimulations.org API.

    The detail contract (a ``SimulationRunSummary``: tasks, outputs, run details
    and the full metadata block) is assembled by biosimulations.org and has no
    confirmed schema to model against here — the platform's own project view is
    the slim ``ProjectStub`` built for the list/facet endpoints above, which does
    not carry those fields. So the upstream body is returned unchanged, the same
    way ``JobLogs.logs`` passes the biosimulations.org ``/logs/{id}`` payload
    through untyped.

    The project catalog is published and anonymous-readable, like the two search
    routes above, so this route takes no auth dependency and nothing but the
    project id is sent upstream — no caller credentials are forwarded.
    """
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(status_code=503, detail="Biosim service not available")

    try:
        return await biosim_service.get_project_summary(project_id)
    except ClientResponseError as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if 400 <= e.status < 500:
            # A 4xx upstream is attributable to the id the caller supplied (a
            # malformed id upstream-400s), not to a broken gateway -- forwarding it
            # says "your request was wrong" instead of "our dependency is down".
            raise HTTPException(status_code=e.status, detail=f"Upstream rejected project id: HTTP {e.status}")
        logger.warning(f"Upstream project summary failed for {project_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch project summary: HTTP {e.status}")
    except aiohttp.ClientError as e:
        # The transport error names the upstream host and port; that belongs in the
        # log, not in a public response body (same split as users/router.py's 502s).
        logger.warning(f"Upstream project summary unreachable for {project_id}: {e}", exc_info=e)
        raise HTTPException(status_code=502, detail="Failed to fetch project summary from the biosimulations.org API")
