"""Tests for the simulations router endpoints."""

from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_omex.models import OmexFile
from biosim_server.biosim_runs import BiosimulatorVersion
from biosim_server.common.auth import get_current_user, get_optional_user
from biosim_server.simulations.workflow import SimulationRunWorkflowInput
from tests.fixtures.auth_fixtures import make_authenticated_user


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
def test_run_simulations_trusts_authenticated_email_over_request_body(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """An authenticated caller's token email overrides a spoofed request-body email_address."""
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

    user = make_authenticated_user(email="real-owner@example.com")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        request = _make_request()
        assert request["email_address"] != user.email
        response = TestClient(app).post("/simulations/run", json=request)
    finally:
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    inserted_record = runs_db.insert_simulation_run.call_args.args[0]
    assert inserted_record.email == user.email
    assert inserted_record.owner_sub == user.sub


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_anonymous_still_uses_request_body_email(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """No bearer token -- behavior is unchanged from before the email-trust fix."""
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

    request = _make_request()
    response = TestClient(app).post("/simulations/run", json=request)

    assert response.status_code == 200
    inserted_record = runs_db.insert_simulation_run.call_args.args[0]
    assert inserted_record.email == request["email_address"]
    assert inserted_record.owner_sub is None


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_allows_anonymous_callers(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
) -> None:
    """P1 #9 Option B: POST /simulations/run stays reachable without a bearer token.

    Recorded in backend/CLAUDE.md → Authentication. Rate limiting (P1 #10)
    is the load-bearing control for this decision.
    """
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    temporal_client.start_workflow.return_value = AsyncMock(id="sim-run-test")
    mock_get_temporal.return_value = temporal_client

    response = TestClient(app).post("/simulations/run", json=_make_request())
    assert response.status_code == 200


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
    email: str | None = "user@example.com",
    owner_sub: str | None = None,
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
        email=email,
        owner_sub=owner_sub,
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
        _make_run_record(
            "job-1",
            processing_id="sim-run-r",
            biosimulations_run_id="biosim-1",
            email=None,
        ),
        _make_run_record(
            "job-2",
            processing_id="sim-run-r",
            biosimulations_run_id=None,
            email=None,
        ),
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
        _make_run_record(
            "job-1",
            processing_id="sim-run-l",
            biosimulations_run_id="biosim-1",
            email=None,
        ),
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


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_results_anonymous_cannot_read_owned_run(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1",
            processing_id="sim-run-r",
            owner_sub="auth0|owner",
            email="owner@example.com",
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    mock_get_biosim.return_value = AsyncMock()

    response = TestClient(app).get("/simulations/sim-run-r/results")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_results_owner_can_read(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        runs_db = AsyncMock()
        runs_db.get_simulation_runs_by_processing_id.return_value = [
            _make_run_record(
                "job-1",
                processing_id="sim-run-r",
                owner_sub=user.sub,
                email=user.email,
                biosimulations_run_id=None,
            ),
        ]
        mock_get_runs_db.return_value = runs_db
        mock_get_biosim.return_value = AsyncMock()

        response = TestClient(app).get("/simulations/sim-run-r/results")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_optional_user, None)


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_logs_non_owner_forbidden(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    user = make_authenticated_user(sub="auth0|other")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        runs_db = AsyncMock()
        runs_db.get_simulation_runs_by_processing_id.return_value = [
            _make_run_record(
                "job-1",
                processing_id="sim-run-l",
                owner_sub="auth0|owner",
                email="owner@example.com",
            ),
        ]
        mock_get_runs_db.return_value = runs_db
        mock_get_biosim.return_value = AsyncMock()

        response = TestClient(app).get("/simulations/sim-run-l/logs")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_optional_user, None)


@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_remains_public(mock_get_temporal: MagicMock) -> None:
    """P1 #11 leaves GET /{id} and /status open -- a processing_id is not a secret."""
    from biosim_server.simulations.models import ConglomerateStatus, SimulationJobStatus

    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    workflow_handle.query.return_value = ConglomerateStatus(
        processing_id="sim-run-public",
        jobs=[
            SimulationJobStatus(
                job_id="job-1", simulator_id="copasi", version="4.34.251", status="processing"
            )
        ],
    )
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    status_response = TestClient(app).get("/simulations/sim-run-public/status")
    assert status_response.status_code == 200
    id_response = TestClient(app).get("/simulations/sim-run-public")
    assert id_response.status_code == 200


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
    record.owner_sub = authenticated_user.sub  # type: ignore[attr-defined]

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


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_success_as_admin_non_owner(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock
) -> None:
    from biosim_server.simulations.models import SimulationRunRecord

    user = make_authenticated_user(roles=["admin"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        record: SimulationRunRecord = _make_run_record(  # type: ignore[assignment]
            "job-1", processing_id="sim-run-c", status="CREATED"
        )
        record.email = "someone-else@example.com"
        updated_record = record.model_copy(update={"status": "CANCELLED"})

        runs_db = AsyncMock()
        runs_db.get_simulation_runs_by_processing_id.side_effect = [[record], [updated_record]]
        mock_get_runs_db.return_value = runs_db

        temporal_client = MagicMock()
        workflow_handle = AsyncMock()
        temporal_client.get_workflow_handle.return_value = workflow_handle
        mock_get_temporal.return_value = temporal_client

        response = TestClient(app).post("/simulations/sim-run-c/cancel")
        assert response.status_code == 200
        workflow_handle.cancel.assert_awaited_once()
        body = response.json()
        assert body["jobs"][0]["status"] == "cancelled"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_legacy_run_succeeds_with_verified_email(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock
) -> None:
    """P1 #8: a verified-email token may still own a pre-owner_sub (legacy) run."""
    from biosim_server.simulations.models import SimulationRunRecord

    user = make_authenticated_user(email="legacy-owner@example.com", email_verified=True)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        record: SimulationRunRecord = _make_run_record(  # type: ignore[assignment]
            "job-1",
            processing_id="sim-run-legacy",
            status="CREATED",
            email=user.email,
            owner_sub=None,
        )
        updated_record = record.model_copy(update={"status": "CANCELLED"})
        runs_db = AsyncMock()
        runs_db.get_simulation_runs_by_processing_id.side_effect = [[record], [updated_record]]
        mock_get_runs_db.return_value = runs_db

        temporal_client = MagicMock()
        workflow_handle = AsyncMock()
        temporal_client.get_workflow_handle.return_value = workflow_handle
        mock_get_temporal.return_value = temporal_client

        response = TestClient(app).post("/simulations/sim-run-legacy/cancel")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_legacy_run_forbidden_without_verified_email(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock
) -> None:
    """Same email, unverified -- must not grant ownership of a legacy run."""
    user = make_authenticated_user(email="legacy-owner@example.com", email_verified=False)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        record = _make_run_record(
            "job-1",
            processing_id="sim-run-legacy",
            status="CREATED",
            email=user.email,
            owner_sub=None,
        )
        runs_db = AsyncMock()
        runs_db.get_simulation_runs_by_processing_id.return_value = [record]
        mock_get_runs_db.return_value = runs_db

        response = TestClient(app).post("/simulations/sim-run-legacy/cancel")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# --------------------------- DELETE /{processing_id} ---------------------------

@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_requires_authentication(mock_get_runs_db: MagicMock) -> None:
    response = TestClient(app).delete("/simulations/sim-run-d")
    assert response.status_code == 401


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_not_found_roleless_caller_forbidden(
    mock_get_runs_db: MagicMock, authenticated_user: object
) -> None:
    """The admin-or-publisher role gate runs (via require_roles) before records are
    fetched, so a roleless caller is rejected with 403 even for a nonexistent run --
    it never leaks whether the run exists to someone who couldn't act on it regardless."""
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = []
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).delete("/simulations/nonexistent")
    assert response.status_code == 403


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_not_found_as_admin(mock_get_runs_db: MagicMock) -> None:
    user = make_authenticated_user(roles=["admin"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        runs_db = AsyncMock()
        runs_db.get_simulation_runs_by_processing_id.return_value = []
        mock_get_runs_db.return_value = runs_db

        response = TestClient(app).delete("/simulations/nonexistent")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_forbidden_for_plain_user_role(mock_get_runs_db: MagicMock) -> None:
    user = make_authenticated_user(roles=["user"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        runs_db = AsyncMock()
        record = _make_run_record("job-1", processing_id="sim-run-d")
        record.email = user.email  # type: ignore[attr-defined]
        runs_db.get_simulation_runs_by_processing_id.return_value = [record]
        mock_get_runs_db.return_value = runs_db

        response = TestClient(app).delete("/simulations/sim-run-d")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_forbidden_for_publisher_non_owner(mock_get_runs_db: MagicMock) -> None:
    user = make_authenticated_user(roles=["publisher"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        runs_db = AsyncMock()
        record = _make_run_record("job-1", processing_id="sim-run-d")
        record.email = "someone-else@example.com"  # type: ignore[attr-defined]
        runs_db.get_simulation_runs_by_processing_id.return_value = [record]
        mock_get_runs_db.return_value = runs_db

        response = TestClient(app).delete("/simulations/sim-run-d")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_success_as_publisher_owner(mock_get_runs_db: MagicMock) -> None:
    user = make_authenticated_user(roles=["publisher"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        runs_db = AsyncMock()
        record = _make_run_record("job-1", processing_id="sim-run-d")
        record.email = user.email  # type: ignore[attr-defined]
        record.owner_sub = user.sub  # type: ignore[attr-defined]
        runs_db.get_simulation_runs_by_processing_id.return_value = [record]
        mock_get_runs_db.return_value = runs_db

        response = TestClient(app).delete("/simulations/sim-run-d")
        assert response.status_code == 204
        runs_db.delete_simulation_runs_by_processing_id.assert_awaited_once_with("sim-run-d")
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_success_as_admin_non_owner(mock_get_runs_db: MagicMock) -> None:
    user = make_authenticated_user(roles=["admin"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        runs_db = AsyncMock()
        record = _make_run_record("job-1", processing_id="sim-run-d")
        record.email = "someone-else@example.com"  # type: ignore[attr-defined]
        runs_db.get_simulation_runs_by_processing_id.return_value = [record]
        mock_get_runs_db.return_value = runs_db

        response = TestClient(app).delete("/simulations/sim-run-d")
        assert response.status_code == 204
        runs_db.delete_simulation_runs_by_processing_id.assert_awaited_once_with("sim-run-d")
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# --------------------------- POST /runs (listing) ---------------------------
# Regression coverage for a bug where list_simulation_runs required get_current_user
# (mandatory auth), which 401'd the frontend's public "Browse Simulation Runs" page --
# that page calls POST /simulations/runs with no Authorization header at all. Fixed by
# switching to get_optional_user: anonymous browsing works for "all" with no email
# scoping. Any email-based scoping -- "type": "user" or an "email" filter -- now
# requires authentication and self-scopes to the caller's own email unless they're
# admin (see test_list_simulation_runs_email_filter_* below); an authenticated
# caller's verified email always overrides a client-supplied one.

@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_all_works_anonymously(mock_get_runs_db: MagicMock) -> None:
    """No Authorization header, type "all": 200, not 401 -- the public listing page's request shape."""
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = ([], 0)
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).post(
        "/simulations/runs",
        json={"type": "all", "filters": [], "pagination": {"page": 1, "perPage": 20}},
    )
    assert response.status_code == 200


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_user_type_anonymous_rejected(mock_get_runs_db: MagicMock) -> None:
    """No token, type "user": 401 -- any email-scoping of the listing requires a
    verified identity now; an anonymous caller can no longer read someone's run
    history just by typing their email into a text box."""
    response = TestClient(app).post(
        "/simulations/runs",
        json={"type": "user", "user": "someone@example.com", "filters": [], "pagination": {"page": 1, "perPage": 20}},
    )
    assert response.status_code == 401


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_user_type_anonymous_without_email_401(mock_get_runs_db: MagicMock) -> None:
    """No token, type "user", no `user` email in the body: still 401 -- auth is
    checked before the missing-email case."""
    response = TestClient(app).post(
        "/simulations/runs",
        json={"type": "user", "filters": [], "pagination": {"page": 1, "perPage": 20}},
    )
    assert response.status_code == 401


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_user_type_authenticated_without_email_400(mock_get_runs_db: MagicMock) -> None:
    """Authenticated but the token carries no email, type "user": 400 -- nothing to
    scope to (mirrors the old anonymous-without-email case, now for an authenticated
    caller whose identity provider didn't supply an email claim)."""
    user = make_authenticated_user(email=None, roles=["admin"])
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).post(
            "/simulations/runs",
            json={"type": "user", "filters": [], "pagination": {"page": 1, "perPage": 20}},
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 400


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_unauthenticated_email_filter_rejected(mock_get_runs_db: MagicMock) -> None:
    """No token, type "all" but an "email" filter present: 401 -- closes the gap where
    the generic filters list could be used to read another user's runs by email
    without ever going through "type": "user"."""
    response = TestClient(app).post(
        "/simulations/runs",
        json={
            "type": "all",
            "filters": [{"id": "email", "operator": "equal", "value": "victim@example.com"}],
            "pagination": {"page": 1, "perPage": 20},
        },
    )
    assert response.status_code == 401


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_email_filter_wrong_user_rejected(mock_get_runs_db: MagicMock) -> None:
    """Authenticated as a plain user, "email" filter for someone else's address: 403."""
    user = make_authenticated_user(email="caller@example.com", roles=["user"])
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).post(
            "/simulations/runs",
            json={
                "type": "all",
                "filters": [{"id": "email", "operator": "equal", "value": "victim@example.com"}],
                "pagination": {"page": 1, "perPage": 20},
            },
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 403


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_email_filter_own_email_allowed(mock_get_runs_db: MagicMock) -> None:
    """Authenticated as a plain user, "email" filter for their own address (any case):
    200, and the query actually sent scopes to their (lowercased) email only."""
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = ([], 0)
    mock_get_runs_db.return_value = runs_db

    user = make_authenticated_user(email="caller@example.com", roles=["user"])
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).post(
            "/simulations/runs",
            json={
                "type": "all",
                "filters": [{"id": "email", "operator": "equal", "value": "Caller@Example.com"}],
                "pagination": {"page": 1, "perPage": 20},
            },
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    sent_request = runs_db.query_simulation_runs.call_args.args[0]
    assert sent_request.filters[0].value == "Caller@Example.com"


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_email_filter_contains_operator_rejected_for_non_admin(
    mock_get_runs_db: MagicMock,
) -> None:
    """Own email but operator "contains" instead of an exact match: 403 -- non-admins
    can't use a partial-match email filter, which would turn "filter by my own email"
    into an email-harvesting probe."""
    user = make_authenticated_user(email="caller@example.com", roles=["user"])
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).post(
            "/simulations/runs",
            json={
                "type": "all",
                "filters": [{"id": "email", "operator": "contains", "value": "caller@example.com"}],
                "pagination": {"page": 1, "perPage": 20},
            },
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 403


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_email_filter_admin_arbitrary_value_allowed(mock_get_runs_db: MagicMock) -> None:
    """Caller with the admin role: arbitrary email + a partial-match operator is
    allowed -- the admin carve-out for cross-user email search."""
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = ([], 0)
    mock_get_runs_db.return_value = runs_db

    user = make_authenticated_user(email="admin@example.com", roles=["admin"])
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).post(
            "/simulations/runs",
            json={
                "type": "all",
                "filters": [{"id": "email", "operator": "contains", "value": "victim"}],
                "pagination": {"page": 1, "perPage": 20},
            },
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 200


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_user_type_authenticated_overrides_request_email(mock_get_runs_db: MagicMock) -> None:
    """Authenticated caller, type "user", with a spoofed `user` email in the body: the
    verified token email wins, so an authenticated caller can't read someone else's runs
    by lying about the `user` field."""
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = ([], 0)
    mock_get_runs_db.return_value = runs_db

    user = make_authenticated_user(email="real-caller@example.com")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).post(
            "/simulations/runs",
            json={"type": "user", "user": "someone-else@example.com",
                  "filters": [], "pagination": {"page": 1, "perPage": 20}},
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    sent_request = runs_db.query_simulation_runs.call_args.args[0]
    assert sent_request.user == "real-caller@example.com"
