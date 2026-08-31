import logging
import uuid
from datetime import timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from biosim_server.biosim_runs import BiosimulatorVersion
from biosim_server.dependencies import (
    get_temporal_client,
    get_biosim_service,
    get_omex_database_service,
    get_simulation_run_database_service,
)
from biosim_server.simulations.models import (
    RunSimulationRequest,
    ConglomerateStatus,
    JobLogs,
    JobResult,
    SetSimulationVisibilityRequest,
    SetSimulationVisibilityResponse,
    SimulationJobStatus,
    SimulationRunLogs,
    SimulationRunRecord,
    SimulationRunResults,
    ListSimulationRunsRequest,
    ListSimulationRunsResponse,
    SimulationRun,
)
from biosim_server.simulations.workflow import SimulationRunWorkflow, SimulationRunWorkflowInput

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user, get_optional_user
from biosim_server.common.auth.roles import (
    ADMIN_ROLE,
    PUBLISHER_ROLE,
    authorize_simulation_run_access,
    authorize_simulation_run_mutation,
    require_roles,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulations", tags=["Simulations"])


@router.post(
    "/run",
    response_model=ConglomerateStatus,
    operation_id="run-simulations",
    dependencies=[Depends(get_temporal_client), Depends(get_biosim_service), Depends(get_omex_database_service)],
    summary="Run simulations for an OMEX archive across selected simulators",
)
async def run_simulations(
    request: RunSimulationRequest,
    user: AuthenticatedUser | None = Depends(get_optional_user),
) -> ConglomerateStatus:
    # Server-authoritative identity first: ownership and visibility are derived
    # from the verified token, never from the request body.
    if user is not None and not user.sub:
        raise HTTPException(status_code=401, detail="Authenticated identity missing subject")
    owner_sub = user.sub if user is not None else None
    visibility: Literal["public", "private"] = "private" if user is not None else "public"

    # Resolve the OMEX *before* touching Temporal or biosimulations.org. The
    # omex_id is a content hash, not a capability: find_accessible_omex_file only
    # returns the caller's own resource or a public/legacy one, so another
    # owner's private archive is a 404 here and never reaches start_workflow.
    # This is also what keeps an anonymous (public) run from referencing a
    # private archive -- an anonymous caller can only resolve public resources.
    omex_database = get_omex_database_service()
    if omex_database is None:
        raise HTTPException(status_code=503, detail="OMEX database service not available")
    omex_file = await omex_database.find_accessible_omex_file(
        file_hash_md5=request.omex_id, viewer_sub=owner_sub
    )
    if omex_file is None:
        raise HTTPException(status_code=404, detail=f"OMEX file not found for omex_id: {request.omex_id}")

    # Resolve each SimulatorSelection to a BiosimulatorVersion
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(status_code=503, detail="Biosim service not available")
    all_simulator_versions = await biosim_service.get_simulator_versions()

    simulator_versions: list[BiosimulatorVersion] = []
    for selection in request.simulators:
        matched: Optional[BiosimulatorVersion] = None
        for sv in all_simulator_versions:
            if sv.id == selection.id and sv.version == selection.version:
                matched = sv
                break
        if matched is None:
            raise HTTPException(
                status_code=400,
                detail=f"Simulator {selection.id}:{selection.version} not found.",
            )
        simulator_versions.append(matched)

    # Generate job IDs and workflow ID
    job_ids = [uuid.uuid4().hex for _ in simulator_versions]
    workflow_id = f"sim-run-{uuid.uuid4()}"

    # Default "0" reuses cached results across identical submissions; a caller-supplied
    # salt forces fresh biosimulations.org runs (parity with /verify/omex's cache_buster).
    cache_buster = request.cache_buster or "0"

    omex_id = request.omex_id

    workflow_input = SimulationRunWorkflowInput(
        omex_file=omex_file,
        simulators=simulator_versions,
        job_ids=job_ids,
        cache_buster=cache_buster,
    )

    temporal_client = get_temporal_client()
    if temporal_client is None:
        raise HTTPException(status_code=503, detail="Temporal service not available")

    # Persist a queryable run record per simulator job (status CREATED) BEFORE starting
    # the workflow. A child OmexSimWorkflow that cache-hits and dispatches its early
    # update_run_status_activity in ~milliseconds would otherwise race the API's inserts
    # -- update_one with no upsert silently no-ops against a missing row, dropping the
    # biosimulations_run_id on the floor. Insert failure is fatal (see below).
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")
    for job_id, sim in zip(job_ids, simulator_versions):
        try:
            await runs_db.insert_simulation_run(SimulationRunRecord(
                run_id=job_id,
                processing_id=workflow_id,
                name=request.name,
                simulator=sim.id,
                simulator_version=sim.version,
                simulator_digest=sim.image_digest,
                cache_buster=cache_buster,
                # Contact metadata only -- ownership is owner_sub, never this.
                # An authenticated caller's email comes from the verified token
                # and nothing else: falling back to the body for a token with no
                # email claim would stamp an unverified, client-chosen address
                # onto an identified user's record (audit plan 4.1, item 8).
                # Anonymous callers have no verified identity to contradict, so
                # their self-declared address is kept as before.
                email=(user.email if user is not None else request.email_address),
                status="CREATED",
                owner_sub=owner_sub,
                visibility=visibility,
                omex_id=omex_id,
            ))
        except Exception as e:
            # Fatal, and fatal *before* start_workflow: the record is where a run's
            # owner and visibility live. A workflow started without it would run
            # unowned and unreachable -- invisible in the listing and, worse,
            # ungoverned by any ACL. Roll back whatever we did insert.
            logger.error(f"Failed to persist simulation run record {job_id}: {e}", exc_info=e)
            try:
                await runs_db.delete_simulation_runs_by_processing_id(workflow_id)
            except Exception as cleanup_e:
                logger.warning(f"Rollback of partial run records for {workflow_id} failed: {cleanup_e}")
            raise HTTPException(status_code=503, detail="Failed to persist simulation run record")

    # Start Temporal workflow. If this raises, mark the just-inserted rows FAILED so
    # they don't linger as CREATED forever (no workflow will ever move them out).
    try:
        await temporal_client.start_workflow(
            SimulationRunWorkflow.run,
            args=[workflow_input],
            task_queue="verification_tasks",
            id=workflow_id,
        )
    except Exception as e:
        logger.error(f"Failed to start SimulationRunWorkflow {workflow_id}: {e}", exc_info=e)
        for job_id in job_ids:
            try:
                await runs_db.update_simulation_run(job_id, status="FAILED")
            except Exception as cleanup_e:
                logger.warning(f"Cleanup of run record {job_id} after start_workflow failure failed: {cleanup_e}")
        raise HTTPException(status_code=503, detail=f"Failed to start simulation workflow: {e}")
    logger.info(f"Started SimulationRunWorkflow with id {workflow_id}")

    # Return initial status with all jobs as "processing"
    jobs = [
        SimulationJobStatus(
            job_id=job_id,
            simulator_id=sim.id,
            version=sim.version,
            status="processing",
        )
        for job_id, sim in zip(job_ids, simulator_versions)
    ]
    return ConglomerateStatus(processing_id=workflow_id, jobs=jobs)

@router.post(
    "/runs",
    response_model=ListSimulationRunsResponse,
    operation_id="list-simulation-runs",
    summary="List simulation runs (owner-scoped, paginated, sortable, filterable)",
)
async def list_simulation_runs(
    request: ListSimulationRunsRequest,
    user: AuthenticatedUser | None = Depends(get_optional_user),
) -> ListSimulationRunsResponse:
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")

    if request.type == "user":
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not user.sub:
            raise HTTPException(status_code=401, detail="Authenticated identity missing subject")
        # Client-supplied request.user is a filter hint, never ownership identity.
        request.user = None

    # ACL + table filters are applied together inside Mongo, so `total` counts
    # only rows the caller may see -- pagination can't leak private-run counts.
    records, total = await runs_db.query_simulation_runs(request, viewer=user)
    viewer_sub = user.sub if user is not None else None
    viewer_is_admin = user is not None and ADMIN_ROLE in user.roles
    return ListSimulationRunsResponse(
        runs=[
            SimulationRun.from_record(record, viewer_sub=viewer_sub, viewer_is_admin=viewer_is_admin)
            for record in records
        ],
        pagination=request.pagination.model_copy(update={"total": total}),
    )


async def _get_conglomerate_status(
    processing_id: str,
    user: AuthenticatedUser | None,
) -> ConglomerateStatus:
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")
    records = await runs_db.get_simulation_runs_by_processing_id(processing_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"Simulation run not found: {processing_id}")
    authorize_simulation_run_access(user, records, action="view")

    temporal_client = get_temporal_client()
    if temporal_client is None:
        raise HTTPException(status_code=503, detail="Temporal service not available")

    # Try the workflow query first. Don't 404 on failure -- the workflow's Temporal
    # history may have been evicted while the SimulationRunRecord rows still exist
    # in Mongo (the listing endpoint still surfaces them). Fall back to DB below.
    status: ConglomerateStatus | None = None
    try:
        workflow_handle = temporal_client.get_workflow_handle(
            workflow_id=processing_id,
            result_type=ConglomerateStatus,
        )
        status = await workflow_handle.query(
            "get_status",
            result_type=ConglomerateStatus,
            rpc_timeout=timedelta(seconds=60),
        )
    except Exception as e:
        logger.info(f"Workflow query failed for {processing_id} (will try DB fallback): {e}")

    if status is not None:
        # Hybrid: workflow-query for live state, DB for the early run id per job.
        id_by_run = {r.run_id: r.biosimulations_run_id for r in records}
        for job in status.jobs:
            if job.biosimulations_run_id is None:
                job.biosimulations_run_id = id_by_run.get(job.job_id)
        return status

    return _conglomerate_status_from_records(processing_id, records)


@router.get(
    "/{processing_id}",
    response_model=ConglomerateStatus,
    operation_id="get-simulation-status",
    dependencies=[Depends(get_temporal_client)],
    summary="Get status of a simulation run",
)
async def get_simulation_status(
    processing_id: str,
    user: AuthenticatedUser | None = Depends(get_optional_user),
) -> ConglomerateStatus:
    return await _get_conglomerate_status(processing_id, user)


@router.get(
    "/{processing_id}/status",
    response_model=ConglomerateStatus,
    operation_id="get-simulation-status-explicit",
    dependencies=[Depends(get_temporal_client)],
    summary="Get status of a simulation run (explicit sub-resource, same data as GET /{id})",
)
async def get_simulation_status_explicit(
    processing_id: str,
    user: AuthenticatedUser | None = Depends(get_optional_user),
) -> ConglomerateStatus:
    return await _get_conglomerate_status(processing_id, user)


@router.get(
    "/{processing_id}/results",
    response_model=SimulationRunResults,
    operation_id="get-simulation-results",
    summary="Get the result-dataset catalog for each job in a simulation run",
)
async def get_simulation_results(
    processing_id: str,
    user: AuthenticatedUser | None = Depends(get_optional_user),
) -> SimulationRunResults:
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(status_code=503, detail="Biosim service not available")

    records = await runs_db.get_simulation_runs_by_processing_id(processing_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"Simulation run not found: {processing_id}")
    authorize_simulation_run_access(user, records, action="view")

    jobs: list[JobResult] = []
    for record in records:
        hdf5_file = None
        if record.biosimulations_run_id is not None:
            try:
                hdf5_file = await biosim_service.get_hdf5_metadata(record.biosimulations_run_id)
            except Exception as e:
                logger.info(f"No results yet for run {record.run_id} ({record.biosimulations_run_id}): {e}")
        jobs.append(JobResult(job_id=record.run_id, simulator_id=record.simulator, hdf5_file=hdf5_file))

    return SimulationRunResults(processing_id=processing_id, jobs=jobs)


@router.get(
    "/{processing_id}/logs",
    response_model=SimulationRunLogs,
    operation_id="get-simulation-logs",
    summary="Get simulator logs for each job in a simulation run",
)
async def get_simulation_logs(
    processing_id: str,
    user: AuthenticatedUser | None = Depends(get_optional_user),
) -> SimulationRunLogs:
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(status_code=503, detail="Biosim service not available")

    records = await runs_db.get_simulation_runs_by_processing_id(processing_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"Simulation run not found: {processing_id}")
    authorize_simulation_run_access(user, records, action="view")

    jobs: list[JobLogs] = []
    for record in records:
        logs = None
        if record.biosimulations_run_id is not None:
            try:
                logs = await biosim_service.get_sim_run_logs(record.biosimulations_run_id)
            except Exception as e:
                logger.info(f"No logs yet for run {record.run_id} ({record.biosimulations_run_id}): {e}")
        jobs.append(JobLogs(job_id=record.run_id, simulator_id=record.simulator, logs=logs))

    return SimulationRunLogs(processing_id=processing_id, jobs=jobs)


@router.post(
    "/{processing_id}/cancel",
    response_model=ConglomerateStatus,
    operation_id="cancel-simulation-run",
    summary="Cancel a simulation run (owner or admin)",
)
async def cancel_simulation_run(
    processing_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ConglomerateStatus:
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")

    records = await runs_db.get_simulation_runs_by_processing_id(processing_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"Simulation run not found: {processing_id}")
    authorize_simulation_run_mutation(user, records, action="cancel")

    temporal_client = get_temporal_client()
    if temporal_client is None:
        raise HTTPException(status_code=503, detail="Temporal service not available")

    try:
        workflow_handle = temporal_client.get_workflow_handle(workflow_id=processing_id)
        await workflow_handle.cancel()
    except Exception as e:
        logger.warning(f"Failed to cancel Temporal workflow {processing_id}: {e}")

    # The workflow itself has no cancellation handler to update run records, and
    # Temporal cancellation is cooperative (may take a moment to settle), so update
    # the queryable records directly here for an immediate, consistent frontend view.
    for record in records:
        if record.status in ("CREATED",):
            try:
                await runs_db.update_simulation_run(record.run_id, status="CANCELLED")
            except Exception as e:
                logger.warning(f"Failed to mark run {record.run_id} CANCELLED: {e}")

    updated_records = await runs_db.get_simulation_runs_by_processing_id(processing_id)
    return _conglomerate_status_from_records(processing_id, updated_records)


@router.patch(
    "/{processing_id}/visibility",
    response_model=SetSimulationVisibilityResponse,
    operation_id="set-simulation-visibility",
    summary="Publish or unpublish a simulation run (owner or admin)",
)
async def set_simulation_visibility(
    processing_id: str,
    request: SetSimulationVisibilityRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> SetSimulationVisibilityResponse:
    """Owner-only publicity control for a run.

    Publishing also publishes the caller's own OMEX resource for the run, in the
    same authorized action -- otherwise a public run would point at a private
    archive that nobody but the owner could execute. If the run's archive can't
    be published by this caller (e.g. an admin publishing somebody else's run,
    whose private archive is not theirs to expose), the request is refused
    rather than silently producing an inconsistent public run.
    """
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")

    records = await runs_db.get_simulation_runs_by_processing_id(processing_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"Simulation run not found: {processing_id}")
    authorize_simulation_run_mutation(user, records, action="change the visibility of")

    if request.visibility == "public":
        await _publish_referenced_omex(records, user)

    updated = await runs_db.set_visibility_by_processing_id(processing_id, request.visibility)
    return SetSimulationVisibilityResponse(
        processing_id=processing_id,
        visibility=request.visibility,
        updated_runs=updated,
    )


async def _publish_referenced_omex(records: list[SimulationRunRecord], user: AuthenticatedUser) -> None:
    """Make sure every archive a run references is public before the run becomes public."""
    omex_database = get_omex_database_service()
    if omex_database is None:
        raise HTTPException(status_code=503, detail="OMEX database service not available")

    for omex_id in {record.omex_id for record in records if record.omex_id}:
        own = await omex_database.get_omex_file(file_hash_md5=omex_id, owner_sub=user.sub)
        if own is not None:
            if not own.is_public:
                await omex_database.set_omex_visibility(
                    file_hash_md5=omex_id, owner_sub=user.sub, visibility="public"
                )
            continue
        # Not the caller's archive -- it may only be published if it is already
        # public. Never flip another owner's resource to public on their behalf.
        accessible = await omex_database.find_accessible_omex_file(
            file_hash_md5=omex_id, viewer_sub=user.sub
        )
        if accessible is None or not accessible.is_public:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot publish run: its OMEX archive {omex_id} is private and "
                    "not owned by the caller"
                ),
            )


@router.delete(
    "/{processing_id}",
    status_code=204,
    operation_id="delete-simulation-run",
    summary="Delete a simulation run's records (admin: any run; publisher: own runs only)",
)
async def delete_simulation_run(
    processing_id: str,
    user: AuthenticatedUser = Depends(require_roles(ADMIN_ROLE, PUBLISHER_ROLE)),
) -> Response:
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")

    records = await runs_db.get_simulation_runs_by_processing_id(processing_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"Simulation run not found: {processing_id}")

    authorize_simulation_run_mutation(user, records, action="delete")

    await runs_db.delete_simulation_runs_by_processing_id(processing_id)
    return Response(status_code=204)


# Maps the SimulationRunRecord display status back onto the SimulationJobStatus
# internal status. Inverse of job_status_to_display in simulations.models.
_DISPLAY_TO_JOB_STATUS: dict[str, str] = {
    "CREATED": "processing",
    "SUCCEEDED": "success",
    "FAILED": "failure",
    "CANCELLED": "cancelled",
}


def _conglomerate_status_from_records(
    processing_id: str, records: list[SimulationRunRecord]
) -> ConglomerateStatus:
    """Build a ConglomerateStatus from persisted records when the Temporal workflow
    query is unavailable (history evicted, workflow never existed, Temporal down)."""
    jobs = [
        SimulationJobStatus(
            job_id=record.run_id,
            simulator_id=record.simulator,
            version=record.simulator_version,
            status=_DISPLAY_TO_JOB_STATUS.get(record.status, "processing"),
            biosimulations_run_id=record.biosimulations_run_id,
        )
        for record in records
    ]
    return ConglomerateStatus(processing_id=processing_id, jobs=jobs)
