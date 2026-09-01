"""Typed passthrough routes for the run-scoped biosimulations.org resources.

The frontend still reads runs, files, SED-ML specifications, logs, results and
KISAO terms straight from ``api.biosimulations.org``. These routes put the
platform in front of that traffic without changing the contract: the paths are
**wire-identical** to the upstream ones, so migrating the frontend is a base-URL
swap rather than a rewrite.

Why these are not under ``/projects``: every one of them is keyed by a
*simulation run* id, not a project id. (They are also not the ``/simulations/*``
routes, which key off platform *processing* ids and return per-job envelopes --
those are deliberately not wire-compatible replacements.)

Like ``GET /projects/{id}/summary``, these routes are anonymous: the project
catalog is published and anonymous-readable, and nothing but the id is sent
upstream -- no caller credentials are forwarded.

**Scope.** Only the JSON endpoints are mirrored here. The binary/streaming ones
the frontend also uses -- ``/runs/{id}/download``, ``/results/{id}/download``,
``/files/{id}/{path}/download`` and the whole-run ``/results/{id}`` -- are not
implemented, so a frontend cutover must keep pointing those at the upstream host.
Note in particular that ``/results/{run_id}/{output_id:path}`` below would match
``/results/{id}/download`` and treat "download" as an output id; adding the
streaming proxies means registering them *before* that route.
"""

import logging

from fastapi import APIRouter, HTTPException

from biosim_server.biosim_runs.biosim_service import BiosimService
from biosim_server.common.biosim_api import (
    KisaoTerm,
    OutputResults,
    ProjectFile,
    RunLog,
    SedDocumentSpec,
    SimulationRunSummary,
)
from biosim_server.common.upstream_errors import upstream_errors
from biosim_server.dependencies import get_biosim_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Biosimulations passthrough"])


def _require_biosim_service() -> BiosimService:
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(status_code=503, detail="Biosim service not available")
    return biosim_service


@router.get(
    "/runs/{run_id}/summary",
    operation_id="get-run-summary",
    summary="Run summary (passthrough to the biosimulations.org API)",
)
async def get_run_summary(run_id: str) -> SimulationRunSummary:
    """Proxy ``GET /runs/{id}/summary``.

    This is the *run page's* entry point. A caller that already holds a
    ``ProjectSummary`` has this same object embedded under ``simulationRun`` and
    must not call here for it.
    """
    biosim_service = _require_biosim_service()
    with upstream_errors(
        resource="run summary",
        identifier=run_id,
        not_found_detail=f"Run not found: {run_id}",
        bad_request_subject="run id",
    ):
        return await biosim_service.get_run_summary(run_id)


@router.get(
    "/files/{run_id}",
    operation_id="list-run-files",
    summary="Files in a run's archive (passthrough to the biosimulations.org API)",
)
async def list_run_files(run_id: str) -> list[ProjectFile]:
    """Proxy ``GET /files/{run_id}``. The id is a *run* id, not a file id."""
    biosim_service = _require_biosim_service()
    with upstream_errors(
        resource="run files",
        identifier=run_id,
        not_found_detail=f"Run not found: {run_id}",
        bad_request_subject="run id",
    ):
        return await biosim_service.get_run_files(run_id)


@router.get(
    "/specifications/{run_id}",
    operation_id="get-run-specifications",
    summary="SED-ML specifications (passthrough to the biosimulations.org API)",
)
async def get_run_specifications(run_id: str) -> list[SedDocumentSpec]:
    """Proxy ``GET /specifications/{run_id}``. The id is a *run* id.

    Returns an array: a COMBINE archive may hold several SED-ML documents.
    """
    biosim_service = _require_biosim_service()
    with upstream_errors(
        resource="run specifications",
        identifier=run_id,
        not_found_detail=f"Run not found: {run_id}",
        bad_request_subject="run id",
    ):
        return await biosim_service.get_run_specifications(run_id)


@router.get(
    "/logs/{run_id}",
    operation_id="get-run-log",
    summary="Execution log (passthrough to the biosimulations.org API)",
)
async def get_run_log(run_id: str) -> RunLog:
    """Proxy ``GET /logs/{run_id}``.

    Logs legitimately do not exist before a run starts, so a 404 here is a normal
    state rather than an error condition.
    """
    biosim_service = _require_biosim_service()
    with upstream_errors(
        resource="run log",
        identifier=run_id,
        not_found_detail=f"Log not found for run: {run_id}",
        bad_request_subject="run id",
    ):
        return await biosim_service.get_run_log(run_id)


@router.get(
    # `:path` because an output id is composite ("{sedDocLocation}/{outputId}")
    # and therefore contains a '/' -- this accepts it both percent-encoded and
    # as literal extra path segments.
    "/results/{run_id}/{output_id:path}",
    operation_id="get-output-results",
    summary="Results for one output (passthrough to the biosimulations.org API)",
)
async def get_output_results(run_id: str, output_id: str) -> OutputResults:
    """Proxy ``GET /results/{run_id}/{output_id}?includeData=true``.

    One output per call, on purpose: this is the only unbounded payload in the
    passthrough set, so it is fetched lazily by the client that needs that plot
    rather than aggregated into any summary or detail response.

    A 404 here is a normal state -- results do not exist until the run finishes.
    """
    biosim_service = _require_biosim_service()
    with upstream_errors(
        resource="output results",
        identifier=f"{run_id}/{output_id}",
        not_found_detail=f"Results not found for run {run_id}, output {output_id}",
        bad_request_subject="run or output id",
    ):
        return await biosim_service.get_output_results(run_id, output_id)


@router.get(
    "/ontologies/KISAO/{kisao_id}",
    operation_id="get-kisao-term",
    summary="KISAO algorithm term (passthrough to the biosimulations.org API)",
)
async def get_kisao_term(kisao_id: str) -> KisaoTerm:
    """Proxy ``GET /ontologies/KISAO/{id}``, cached, with a local fallback.

    Accepts either id spelling (``KISAO_0000019`` or ``KISAO:0000019``).
    """
    biosim_service = _require_biosim_service()
    with upstream_errors(
        resource="KISAO term",
        identifier=kisao_id,
        not_found_detail=f"KISAO term not found: {kisao_id}",
        bad_request_subject="KISAO id",
    ):
        return await biosim_service.get_kisao_term(kisao_id)
