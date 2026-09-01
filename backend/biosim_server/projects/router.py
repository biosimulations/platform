"""Routes for the BioSim DB project search API.

Two decoupled GET endpoints replace the legacy ``/projects/summary_filtered``
so paging through slim results is independent of the heavier facet computation
(see ``docs/project-search-api-plan.md``).

``GET /{id}/summary`` is different in kind: the project *detail* contract still
belongs to biosimulations.org, so that route is a passthrough to the upstream
API rather than a view over the platform's own project data.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import ValidationError

from biosim_server.common.biosim_api import ProjectSummary
from biosim_server.common.upstream_errors import upstream_errors
from biosim_server.config import get_settings
from biosim_server.dependencies import get_biosim_service, get_project_database_service
from biosim_server.common.biosim_api import ProjectFile, SedDocumentSpec
from biosim_server.projects.models import (
    ProjectDetail,
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
async def get_project_summary(project_id: str) -> ProjectSummary:
    """Proxy ``GET /projects/{id}/summary`` to the biosimulations.org API.

    The detail contract is a nested envelope (``id`` + ``simulationRun`` with
    ``metadata`` and ``run`` sizes, plus unmodeled extras such as tasks/outputs/
    owner). The platform validates the fields it types and keeps remaining
    upstream keys via ``extra="allow"``. The slim ``ProjectStub`` used by the
    list/facet endpoints above does not carry this nested detail.

    The project catalog is published and anonymous-readable, like the two search
    routes above, so this route takes no auth dependency and nothing but the
    project id is sent upstream — no caller credentials are forwarded.
    """
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(status_code=503, detail="Biosim service not available")

    with upstream_errors(
        resource="project summary",
        identifier=project_id,
        not_found_detail=f"Project not found: {project_id}",
        bad_request_subject="project id",
    ):
        return await biosim_service.get_project_summary(project_id)


@router.get(
    "/{project_id}/detail",
    operation_id="get-project-detail",
    summary="Project summary plus its run's files, specifications and (optionally) log",
)
async def get_project_detail(
    project_id: str,
    include: list[str] = Query(
        default=[],
        description="Optional extra resources to fetch. Currently only 'log'.",
    ),
) -> ProjectDetail:
    """Compose the project page's data in one round trip.

    Call graph:

    1. ``GET /projects/{id}/summary`` -- mandatory; its failure fails the request.
    2. The run id comes **only** from that summary's embedded ``simulationRun``.
       The run summary itself is already in hand, so ``/runs/{id}/summary`` is
       never called here. With no run id there is nothing to key a dependent
       request on, so the summary is returned alone rather than requesting a
       malformed upstream URL.
    3. Files and the SED-ML specifications are fetched concurrently, best-effort.
    4. The log is fetched only for ``?include=log``.

    Results and KISAO terms are never fetched here -- see ProjectDetail.
    """
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(status_code=503, detail="Biosim service not available")

    with upstream_errors(
        resource="project summary",
        identifier=project_id,
        not_found_detail=f"Project not found: {project_id}",
        bad_request_subject="project id",
    ):
        summary = await biosim_service.get_project_summary(project_id)

    run_id = summary.simulation_run.id
    if not run_id:
        logger.info(f"Project {project_id} has no simulationRun.id; returning summary only")
        return ProjectDetail(summary=summary)

    files_result, spec_result = await asyncio.gather(
        biosim_service.get_run_files(run_id),
        biosim_service.get_run_specifications(run_id),
        return_exceptions=True,
    )
    if isinstance(files_result, BaseException):
        logger.info(f"No files for run {run_id} of project {project_id}: {files_result}")
    if isinstance(spec_result, BaseException):
        logger.info(f"No specification for run {run_id} of project {project_id}: {spec_result}")

    files: list[ProjectFile] = files_result if isinstance(files_result, list) else []
    specifications: list[SedDocumentSpec] = spec_result if isinstance(spec_result, list) else []

    log = None
    if "log" in include:
        try:
            log = await biosim_service.get_run_log(run_id)
        except Exception as e:
            # Best-effort, like the other secondaries: a run that has not started
            # has no log yet, and that must not fail the page.
            logger.info(f"No log for run {run_id} of project {project_id}: {e}")

    return ProjectDetail(summary=summary, files=files, specifications=specifications, log=log)
