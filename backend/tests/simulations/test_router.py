"""Tests for the simulations router endpoints."""

from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_omex.models import OmexFile
from biosim_server.biosim_runs import BiosimulatorVersion
from biosim_server.simulations.workflow import SimulationRunWorkflowInput


MOCK_OMEX_FILE = OmexFile(
    file_hash_md5="abc123def456",
    uploaded_filename="test.omex",
    bucket_name="test-bucket",
    omex_gcs_path="omex/abc123def456/test.omex",
    file_size=1024,
)

MOCK_SIMULATOR_VERSIONS = [
    BiosimulatorVersion(
        id="copasi",
        name="COPASI",
        version="4.34.251",
        image_url="ghcr.io/biosimulators/copasi:4.34.251",
        image_digest="sha256:abc123",
        created="2024-01-01T00:00:00Z",
        updated="2024-01-01T00:00:00Z",
    ),
    BiosimulatorVersion(
        id="tellurium",
        name="tellurium",
        version="2.2.10",
        image_url="ghcr.io/biosimulators/tellurium:2.2.10",
        image_digest="sha256:def456",
        created="2024-01-01T00:00:00Z",
        updated="2024-01-01T00:00:00Z",
    ),
]


def _make_request(
    omex_id: str = "abc123def456",
    simulators: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "omex_id": omex_id,
        "name": "Test Run",
        "simulators": simulators or [{"id": "copasi", "version": "4.34.251"}],
        "email_address": "test@example.com",
    }


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_success(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
) -> None:
    """Test successful simulation run request."""
    # Mock omex database
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    # Mock biosim service
    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    # Mock temporal client
    temporal_client = AsyncMock()
    workflow_handle = AsyncMock()
    workflow_handle.id = "sim-run-test"
    temporal_client.start_workflow.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    client = TestClient(app)
    response = client.post("/simulations/run", json=_make_request())

    assert response.status_code == 200
    data = response.json()
    assert "processing_id" in data
    assert data["processing_id"].startswith("sim-run-")
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["simulator_id"] == "copasi"
    assert data["jobs"][0]["version"] == "4.34.251"
    assert data["jobs"][0]["status"] == "processing"
    assert len(data["jobs"][0]["job_id"]) == 32  # hex UUID


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_multiple_simulators(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
) -> None:
    """Test simulation run with multiple simulators."""
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    temporal_client.start_workflow.return_value = AsyncMock(id="sim-run-test")
    mock_get_temporal.return_value = temporal_client

    client = TestClient(app)
    request = _make_request(simulators=[
        {"id": "copasi", "version": "4.34.251"},
        {"id": "tellurium", "version": "2.2.10"},
    ])
    response = client.post("/simulations/run", json=request)

    assert response.status_code == 200
    data = response.json()
    assert len(data["jobs"]) == 2
    assert data["jobs"][0]["simulator_id"] == "copasi"
    assert data["jobs"][1]["simulator_id"] == "tellurium"
    # Each job has a unique ID
    assert data["jobs"][0]["job_id"] != data["jobs"][1]["job_id"]


def _workflow_input_from(temporal_client: AsyncMock) -> SimulationRunWorkflowInput:
    """Pull the SimulationRunWorkflowInput passed to start_workflow(args=[...])."""
    workflow_input: SimulationRunWorkflowInput = temporal_client.start_workflow.call_args.kwargs["args"][0]
    return workflow_input


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_cache_buster_passthrough(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
) -> None:
    """An explicit cache_buster is forwarded to the workflow input."""
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client

    request = _make_request()
    request["cache_buster"] = "salt-123"
    response = TestClient(app).post("/simulations/run", json=request)

    assert response.status_code == 200
    assert _workflow_input_from(temporal_client).cache_buster == "salt-123"


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_cache_buster_defaults_to_zero(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
) -> None:
    """When cache_buster is omitted, the workflow input defaults to "0" (dedup)."""
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client

    response = TestClient(app).post("/simulations/run", json=_make_request())

    assert response.status_code == 200
    assert _workflow_input_from(temporal_client).cache_buster == "0"


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_inserts_before_starting_workflow(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """All SimulationRunRecord inserts must complete BEFORE the workflow starts -- the
    child OmexSimWorkflow's early update_run_status_activity (Mongo update_one with no
    upsert) would otherwise silently no-op against missing rows on cache hits."""
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

    order: list[str] = []

    async def _record_insert(record: object) -> object:
        order.append("insert")
        return record

    async def _record_start(*args: object, **kwargs: object) -> AsyncMock:
        order.append("start")
        return AsyncMock()

    runs_db.insert_simulation_run.side_effect = _record_insert
    temporal_client.start_workflow.side_effect = _record_start

    request = _make_request(simulators=[
        {"id": "copasi", "version": "4.34.251"},
        {"id": "tellurium", "version": "2.2.10"},
    ])
    response = TestClient(app).post("/simulations/run", json=request)

    assert response.status_code == 200
    # Every insert must precede the workflow start.
    assert order == ["insert", "insert", "start"], f"unexpected ordering: {order}"


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_marks_records_failed_when_start_workflow_raises(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """If start_workflow raises after inserts succeed, the rows must be marked FAILED
    so they don't linger as CREATED forever -- no workflow will ever move them out."""
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    temporal_client.start_workflow.side_effect = RuntimeError("Temporal unavailable")
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

    request = _make_request(simulators=[
        {"id": "copasi", "version": "4.34.251"},
        {"id": "tellurium", "version": "2.2.10"},
    ])
    response = TestClient(app).post("/simulations/run", json=request)

    assert response.status_code == 503
    # Both rows were inserted CREATED and then rolled back to FAILED.
    assert runs_db.insert_simulation_run.call_count == 2
    assert runs_db.update_simulation_run.call_count == 2
    for call in runs_db.update_simulation_run.call_args_list:
        assert call.kwargs.get("status") == "FAILED"


@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_omex_not_found(mock_get_omex_db: MagicMock) -> None:
    """Test 404 when OMEX file not found."""
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = None
    mock_get_omex_db.return_value = omex_db

    client = TestClient(app)
    response = client.post("/simulations/run", json=_make_request(omex_id="nonexistent"))

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_simulator_not_found(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
) -> None:
    """Test 400 when requested simulator version not found."""
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    client = TestClient(app)
    request = _make_request(simulators=[{"id": "nonexistent", "version": "1.0.0"}])
    response = client.post("/simulations/run", json=request)

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_success(mock_get_temporal: MagicMock) -> None:
    """Test successful status query."""
    from biosim_server.simulations.models import ConglomerateStatus, SimulationJobStatus

    expected = ConglomerateStatus(
        processing_id="sim-run-test",
        jobs=[
            SimulationJobStatus(
                job_id="abc123",
                simulator_id="copasi",
                version="4.34.251",
                status="success",
                biosimulations_run_id="ext-123",
            )
        ],
    )

    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    workflow_handle.query.return_value = expected
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    client = TestClient(app)
    response = client.get("/simulations/sim-run-test")

    assert response.status_code == 200
    data = response.json()
    assert data["processing_id"] == "sim-run-test"
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["status"] == "success"
    assert data["jobs"][0]["biosimulations_run_id"] == "ext-123"


@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_not_found(mock_get_temporal: MagicMock) -> None:
    """Test 404 when workflow not found AND no DB records exist."""
    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    workflow_handle.query.side_effect = Exception("Workflow not found")
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    client = TestClient(app)
    response = client.get("/simulations/nonexistent")

    assert response.status_code == 404


def _make_run_record(
    run_id: str,
    *,
    processing_id: str,
    status: str = "SUCCEEDED",
    simulator: str = "copasi",
    biosimulations_run_id: str | None = "biosim-abc",
) -> object:
    """Build a SimulationRunRecord for the fallback / hybrid-merge tests."""
    from datetime import datetime, timezone

    from biosim_server.simulations.models import SimulationRunRecord

    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return SimulationRunRecord(
        run_id=run_id,
        processing_id=processing_id,
        name="Run",
        simulator=simulator,
        simulator_version="4.34.251",
        simulator_digest="sha256:abc",
        cache_buster="0",
        email="user@example.com",
        status=status,  # type: ignore[arg-type]
        biosimulations_run_id=biosimulations_run_id,
        submitted=when,
        updated=when,
    )


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_falls_back_to_db_when_workflow_query_fails(
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """If the workflow query fails (e.g. Temporal evicted the history), the endpoint
    builds the response from persisted SimulationRunRecord rows instead of 404."""
    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    workflow_handle.query.side_effect = Exception("workflow history evicted")
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record("job-1", processing_id="sim-run-evicted", status="SUCCEEDED",
                         simulator="copasi", biosimulations_run_id="biosim-1"),
        _make_run_record("job-2", processing_id="sim-run-evicted", status="FAILED",
                         simulator="tellurium", biosimulations_run_id="biosim-2"),
    ]
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).get("/simulations/sim-run-evicted")

    assert response.status_code == 200
    data = response.json()
    assert data["processing_id"] == "sim-run-evicted"
    assert len(data["jobs"]) == 2
    # SUCCEEDED -> "success", FAILED -> "failure" via _DISPLAY_TO_JOB_STATUS.
    by_id = {job["job_id"]: job for job in data["jobs"]}
    assert by_id["job-1"]["status"] == "success"
    assert by_id["job-1"]["biosimulations_run_id"] == "biosim-1"
    assert by_id["job-2"]["status"] == "failure"
    assert by_id["job-2"]["biosimulations_run_id"] == "biosim-2"


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_404_when_workflow_and_db_both_empty(
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """If both the workflow query and the DB lookup come up empty, the endpoint 404s."""
    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    workflow_handle.query.side_effect = Exception("workflow not found")
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = []
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).get("/simulations/sim-run-unknown")

    assert response.status_code == 404


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_hybrid_enriches_biosim_run_id_from_db(
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """When the workflow query returns biosimulations_run_id=None for a job (mid-run
    before the parent has copied it from children), the DB record's id fills in --
    this is the PR2.5 early-write path made visible through GET /simulations/{id}."""
    from biosim_server.simulations.models import ConglomerateStatus, SimulationJobStatus

    workflow_status = ConglomerateStatus(
        processing_id="sim-run-x",
        jobs=[SimulationJobStatus(
            job_id="job-1",
            simulator_id="copasi",
            version="4.34.251",
            status="processing",
            biosimulations_run_id=None,
        )],
    )
    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    workflow_handle.query.return_value = workflow_status
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record("job-1", processing_id="sim-run-x", status="CREATED",
                         biosimulations_run_id="biosim-early"),
    ]
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).get("/simulations/sim-run-x")

    assert response.status_code == 200
    job = response.json()["jobs"][0]
    # Live status from the workflow query...
    assert job["status"] == "processing"
    # ...biosim_run_id enriched from the DB.
    assert job["biosimulations_run_id"] == "biosim-early"


def test_run_simulations_missing_fields() -> None:
    """Test validation error for missing required fields."""
    client = TestClient(app)
    response = client.post("/simulations/run", json={"omex_id": "abc"})
    assert response.status_code == 422


# --------------------------- /status (explicit sub-resource) ---------------------------

@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_explicit_matches_combined(mock_get_temporal: MagicMock) -> None:
    """GET /{id}/status returns the same payload as GET /{id}."""
    from biosim_server.simulations.models import ConglomerateStatus, SimulationJobStatus

    expected = ConglomerateStatus(
        processing_id="sim-run-test",
        jobs=[SimulationJobStatus(job_id="abc123", simulator_id="copasi", version="4.34.251", status="success")],
    )
    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    workflow_handle.query.return_value = expected
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    response = TestClient(app).get("/simulations/sim-run-test/status")
    assert response.status_code == 200
    assert response.json()["processing_id"] == "sim-run-test"


# --------------------------- /results ---------------------------

@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_results(mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock) -> None:
    from biosim_server.biosim_runs import HDF5File

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record("job-1", processing_id="sim-run-r", biosimulations_run_id="biosim-1"),
        _make_run_record("job-2", processing_id="sim-run-r", biosimulations_run_id=None),
    ]
    mock_get_runs_db.return_value = runs_db

    biosim_service = AsyncMock()
    biosim_service.get_hdf5_metadata.return_value = HDF5File(
        filename="results.h5", id="biosim-1", uri="biosim-1", groups=[]
    )
    mock_get_biosim.return_value = biosim_service

    response = TestClient(app).get("/simulations/sim-run-r/results")
    assert response.status_code == 200
    body = response.json()
    by_id = {job["jobId"]: job for job in body["jobs"]}
    assert by_id["job-1"]["hdf5File"] is not None
    biosim_service.get_hdf5_metadata.assert_awaited_once_with("biosim-1")
    # job-2 has no biosimulations_run_id yet -- no results, not an error.
    assert by_id["job-2"]["hdf5File"] is None


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_results_not_found(mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = []
    mock_get_runs_db.return_value = runs_db
    mock_get_biosim.return_value = AsyncMock()

    response = TestClient(app).get("/simulations/nonexistent/results")
    assert response.status_code == 404


# --------------------------- /logs ---------------------------

@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_logs(mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record("job-1", processing_id="sim-run-l", biosimulations_run_id="biosim-1"),
    ]
    mock_get_runs_db.return_value = runs_db

    biosim_service = AsyncMock()
    biosim_service.get_sim_run_logs.return_value = {"stdout": "hello"}
    mock_get_biosim.return_value = biosim_service

    response = TestClient(app).get("/simulations/sim-run-l/logs")
    assert response.status_code == 200
    body = response.json()
    assert body["jobs"][0]["logs"] == {"stdout": "hello"}
    biosim_service.get_sim_run_logs.assert_awaited_once_with("biosim-1")


# --------------------------- /cancel ---------------------------

@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_requires_authentication(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock
) -> None:
    response = TestClient(app).post("/simulations/sim-run-c/cancel")
    assert response.status_code == 401


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_not_found(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock, authenticated_user: object
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = []
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).post("/simulations/nonexistent/cancel")
    assert response.status_code == 404


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_forbidden_for_non_owner(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock, authenticated_user: object
) -> None:
    runs_db = AsyncMock()
    record = _make_run_record("job-1", processing_id="sim-run-c")
    record.email = "someone-else@example.com"  # type: ignore[attr-defined]
    runs_db.get_simulation_runs_by_processing_id.return_value = [record]
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).post("/simulations/sim-run-c/cancel")
    assert response.status_code == 403


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_success(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock, authenticated_user: object
) -> None:
    from biosim_server.simulations.models import SimulationRunRecord

    record: SimulationRunRecord = _make_run_record(  # type: ignore[assignment]
        "job-1", processing_id="sim-run-c", status="CREATED"
    )
    record.email = authenticated_user.email  # type: ignore[attr-defined]

    updated_record = record.model_copy(update={"status": "CANCELLED"})

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.side_effect = [[record], [updated_record]]
    mock_get_runs_db.return_value = runs_db

    # get_workflow_handle is synchronous on the real Temporal client (only the
    # handle's own methods, like .cancel(), are async) -- MagicMock here, not
    # AsyncMock, matches that and matches the other tests in this file.
    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    response = TestClient(app).post("/simulations/sim-run-c/cancel")
    assert response.status_code == 200
    workflow_handle.cancel.assert_awaited_once()
    runs_db.update_simulation_run.assert_awaited_once_with("job-1", status="CANCELLED")
    body = response.json()
    assert body["jobs"][0]["status"] == "cancelled"
