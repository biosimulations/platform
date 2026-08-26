import logging
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from biosim_server.biosim_runs import BiosimulatorVersion
from biosim_server.dependencies import (
    get_temporal_client,
    get_biosim_service,
    get_omex_database_service,
    get_simulation_run_database_service,
)
from biosim_server.simulations.database import InvalidDateFilterError
from biosim_server.simulations.models import (
    RunSimulationRequest,
    ConglomerateStatus,
    JobLogs,
    JobResult,
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
    is_owner,
    is_ownerless,
    require_owner_or_admin,
    require_roles,
)
from biosim_server.common.ratelimit import workflow_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulations", tags=["Simulations"])


@router.post(
    "/run",
    response_model=ConglomerateStatus,
    operation_id="run-simulations",
    dependencies=[Depends(get_temporal_client), Depends(get_biosim_service), Depends(get_omex_database_service), Depends(workflow_rate_limit)],
    summary="Run simulations for an OMEX archive across selected simulators",
)
async def run_simulations(
    request: RunSimulationRequest,
    user: AuthenticatedUser | None = Depends(get_optional_user),
) -> ConglomerateStatus:
    # P1 #9 Option B: anonymous submission stays allowed. The frontend does
    # not attach a bearer token today; requiring auth here would break the
    # UI. Rate limiting (workflow_rate_limit / P1 #10) is the load-bearing
    # control. Recorded in backend/CLAUDE.md → Authentication.
    # Look up OMEX file by omex_id (which is the file_hash_md5)
    omex_database = get_omex_database_service()
    if omex_database is None:
        raise HTTPException(status_code=503, detail="OMEX database service not available")
    omex_file = await omex_database.get_omex_file(file_hash_md5=request.omex_id)
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
    # biosimulations_run_id on the floor. Insert failures are still non-fatal.
    runs_db = get_simulation_run_database_service()
    if runs_db is not None:
        for job_id, sim in zip(job_ids, simulator_versions):
            try:
                await runs_db.insert_simulation_run(
                    SimulationRunRecord(
                        run_id=job_id,
                        processing_id=workflow_id,
                        name=request.name,
                        simulator=sim.id,
                        simulator_version=sim.version,
                        simulator_digest=sim.image_digest,
                        cache_buster=cache_buster,
                        # Trust the verified token's email over the client-supplied field
                        # when the caller is authenticated -- request.email_address is
                        # otherwise spoofable and would undermine ownership checks (e.g.
                        # delete_simulation_run) downstream. Anonymous submissions keep
                        # using the request body field unchanged.
                        email=(
                            user.email
                            if user is not None and user.email
                            else request.email_address
                        ),
                        # sub is never client-suppliable -- it comes only from a verified
                        # token, unlike email_address above. None for anonymous
                        owner_sub=(user.sub if user is not None else None),
                        status="CREATED",
                    )
                )
            except Exception as e:
                logger.error(f"Failed to persist simulation run record {job_id}: {e}", exc_info=e)
    else:
        logger.warning("Simulation run database service not available; run not persisted for listing")

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
        if runs_db is not None:
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
    # Never trust a client-supplied owner_sub -- it is not a filter the caller
    # may choose; the handler stamps it from a verified token below.
    request.owner_sub = None

    # Any email-based scoping of the listing -- "type": "user" or a "filters"
    # entry on the "email" field -- requires a verified identity. Without this,
    # an anonymous or authenticated-as-someone-else caller could read another
    # user's full run history just by knowing/guessing their email, via either
    # mechanism. "type": "all" with no email filter stays open to anonymous
    # browsing.
    wants_email_scope = request.type == "user" or any(f.id == "email" for f in request.filters)
    if wants_email_scope:
        if user is None:
            raise _missing_bearer("Authentication required to filter runs by email")
        if ADMIN_ROLE not in user.roles:
            # Non-admins self-scope by owner_sub (primary). Legacy rows with
            # no owner_sub are included only via a verified-email match.
            # contains/starts_with/is_any on email would turn "filter by my
            # own email" into an email-harvesting probe.
            if request.type == "user":
                request.owner_sub = user.sub
                request.user = user.email if user.email_verified and user.email else None
            for table_filter in request.filters:
                if table_filter.id != "email":
                    continue
                if table_filter.operator not in (None, "equal", "is"):
                    raise HTTPException(status_code=403, detail="Only an exact-match email filter is allowed")
                if not user.email or str(table_filter.value or "").lower() != user.email.lower():
                    raise HTTPException(status_code=403, detail="Can only filter runs by your own email")
        elif request.type == "user" and not request.user:
            raise HTTPException(status_code=400, detail="user (email) is required when type is 'user'")

    if request.type == "user" and user is not None and ADMIN_ROLE not in user.roles:
        if not request.owner_sub:
            raise HTTPException(
                status_code=400,
                detail="Cannot list your runs without a verified identity",
            )

    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")

    try:
        records, total = await runs_db.query_simulation_runs(request)
    except InvalidDateFilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    is_admin = user is not None and ADMIN_ROLE in user.roles
    return ListSimulationRunsResponse(
        runs=[
            SimulationRun.from_record(
                record,
                include_email=is_admin or (user is not None and is_owner(user, record)),
            )
            for record in records
        ],
        pagination=request.pagination.model_copy(update={"total": total}),
    )


async def _get_conglomerate_status(processing_id: str) -> ConglomerateStatus:
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

    # Fetch SimulationRunRecord rows for this processing_id once -- they enrich the
    # workflow-query view with the early biosimulations_run_id, AND serve as the
    # sole source when the workflow query failed above.
    records: list[SimulationRunRecord] = []
    runs_db = get_simulation_run_database_service()
    if runs_db is not None:
        try:
            records = await runs_db.get_simulation_runs_by_processing_id(processing_id)
        except Exception as e:
            logger.warning(f"Failed to read SimulationRunRecords for {processing_id}: {e}")

    if status is not None:
        # Hybrid: workflow-query for live state, DB for the early run id per job.
        id_by_run = {r.run_id: r.biosimulations_run_id for r in records}
        for job in status.jobs:
            if job.biosimulations_run_id is None:
                job.biosimulations_run_id = id_by_run.get(job.job_id)
        return status

    if not records:
        raise HTTPException(status_code=404, detail=f"Simulation run not found: {processing_id}")
    return _conglomerate_status_from_records(processing_id, records)


@router.get(
    "/{processing_id}",
    response_model=ConglomerateStatus,
    operation_id="get-simulation-status",
    dependencies=[Depends(get_temporal_client)],
    summary="Get status of a simulation run",
)
async def get_simulation_status(processing_id: str) -> ConglomerateStatus:
    return await _get_conglomerate_status(processing_id)


@router.get(
    "/{processing_id}/status",
    response_model=ConglomerateStatus,
    operation_id="get-simulation-status-explicit",
    dependencies=[Depends(get_temporal_client)],
    summary="Get status of a simulation run (explicit sub-resource, same data as GET /{id})",
)
async def get_simulation_status_explicit(processing_id: str) -> ConglomerateStatus:
    return await _get_conglomerate_status(processing_id)


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
    _authorize_run_read(user, records, action="view results for")

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
    _authorize_run_read(user, records, action="view logs for")

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
    require_owner_or_admin(user, records, action="cancel")

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

    require_owner_or_admin(user, records, action="delete")

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


def _missing_bearer(detail: str) -> HTTPException:
    """401 for a missing token on an otherwise-optional-auth path.

    Mirrors common.auth.auth0._unauthorized's RFC 6750 challenge so every
    401 from this router carries WWW-Authenticate, not only those raised
    inside get_current_user.
    """
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={
            "WWW-Authenticate": (
                'Bearer realm="api", error="invalid_request", '
                'error_description="Missing bearer token"'
            )
        },
    )


def _authorize_run_read(
    user: AuthenticatedUser | None,
    records: list[SimulationRunRecord],
    *,
    action: str,
) -> None:
    """P1 #11: genuinely anonymous runs stay publicly readable; owned runs do not.

    An ownerless record has neither owner_sub nor email. If every record in
    the set is ownerless, anyone holding the processing_id may read results
    or logs. Otherwise the caller must be the owner or an admin -- 401 when
    there is no token, 403 when there is a token that does not own the run.
    GET /{id} and /status are deliberately left open.
    """
    if all(is_ownerless(record) for record in records):
        return
    if user is None:
        raise _missing_bearer(f"Authentication required to {action} this simulation run")
    require_owner_or_admin(user, records, action=action)


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
