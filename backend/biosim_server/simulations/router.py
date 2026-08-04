import logging
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

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
    SimulationJobStatus,
    SimulationRunRecord,
    ListSimulationRunsRequest,
    ListSimulationRunsResponse,
    SimulationRun,
)
from biosim_server.simulations.workflow import SimulationRunWorkflow, SimulationRunWorkflowInput

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulations", tags=["Simulations"])


@router.post(
    "/run",
    response_model=ConglomerateStatus,
    operation_id="run-simulations",
    dependencies=[Depends(get_temporal_client), Depends(get_biosim_service), Depends(get_omex_database_service)],
    summary="Run simulations for an OMEX archive across selected simulators",
)
async def run_simulations(request: RunSimulationRequest) -> ConglomerateStatus:
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
                await runs_db.insert_simulation_run(SimulationRunRecord(
                    run_id=job_id,
                    processing_id=workflow_id,
                    name=request.name,
                    simulator=sim.id,
                    simulator_version=sim.version,
                    simulator_digest=sim.image_digest,
                    cache_buster=cache_buster,
                    email=request.email_address,
                    status="CREATED",
                ))
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

'''
@router.post(
    "/runs",
    response_model=ListSimulationRunsResponse,
    operation_id="list-simulation-runs",
    summary="List simulation runs (owner-scoped, paginated, sortable, filterable)",
)
async def list_simulation_runs(request: ListSimulationRunsRequest) -> ListSimulationRunsResponse:
    if request.type == "user" and not request.user:
        raise HTTPException(status_code=400, detail="user (email) is required when type is 'user'")

    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        raise HTTPException(status_code=503, detail="Simulation run database service not available")

    records, total = await runs_db.query_simulation_runs(request)
    return ListSimulationRunsResponse(
        runs=[SimulationRun.from_record(record) for record in records],
        pagination=request.pagination.model_copy(update={"total": total}),
    )
'''

@router.post("/simulations/runs")
async def get_runs(
    query: RunsQuery,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if query.type == "user":
        query.user = user.email
)

@router.get(
    "/{processing_id}",
    response_model=ConglomerateStatus,
    operation_id="get-simulation-status",
    dependencies=[Depends(get_temporal_client)],
    summary="Get status of a simulation run",
)
async def get_simulation_status(processing_id: str) -> ConglomerateStatus:
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


# Maps the SimulationRunRecord display status back onto the SimulationJobStatus
# internal status. Inverse of job_status_to_display in simulations.models.
_DISPLAY_TO_JOB_STATUS: dict[str, str] = {"CREATED": "processing", "SUCCEEDED": "success", "FAILED": "failure"}


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
