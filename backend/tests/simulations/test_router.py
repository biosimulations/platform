"""Tests for the simulations router endpoints."""

from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_omex.models import OmexFile
from biosim_server.biosim_runs import BiosimulatorVersion
from biosim_server.common.auth import AuthenticatedUser, get_current_user, get_optional_user
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


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_success(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """Test successful simulation run request."""
    # Mock omex database
    omex_db = AsyncMock()
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
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

    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

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


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_multiple_simulators(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """Test simulation run with multiple simulators."""
    omex_db = AsyncMock()
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    temporal_client.start_workflow.return_value = AsyncMock(id="sim-run-test")
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

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


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_cache_buster_passthrough(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """An explicit cache_buster is forwarded to the workflow input."""
    omex_db = AsyncMock()
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

    request = _make_request()
    request["cache_buster"] = "salt-123"
    response = TestClient(app).post("/simulations/run", json=request)

    assert response.status_code == 200
    assert _workflow_input_from(temporal_client).cache_buster == "salt-123"


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_cache_buster_defaults_to_zero(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """When cache_buster is omitted, the workflow input defaults to "0" (dedup)."""
    omex_db = AsyncMock()
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

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
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
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
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

    user = make_authenticated_user(sub="auth0|abc", email="real-owner@example.com")
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
    assert inserted_record.owner_sub == "auth0|abc"
    assert inserted_record.visibility == "private"
    assert inserted_record.omex_id == request["omex_id"]


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
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
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
    assert inserted_record.visibility == "public"
    assert inserted_record.omex_id == request["omex_id"]


def test_run_simulations_invalid_token_is_401() -> None:
    response = TestClient(app).post(
        "/simulations/run",
        json=_make_request(),
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    detail = str(response.json().get("detail", ""))
    assert "Malformed token" in detail or "Invalid token" in detail

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
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
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
    omex_db.find_accessible_omex_file.return_value = None
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
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    client = TestClient(app)
    request = _make_request(simulators=[{"id": "nonexistent", "version": "1.0.0"}])
    response = client.post("/simulations/run", json=request)

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_success(mock_get_temporal: MagicMock, mock_get_runs_db: MagicMock) -> None:
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

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record("abc123", processing_id="sim-run-test", visibility="public"),
    ]
    mock_get_runs_db.return_value = runs_db

    client = TestClient(app)
    response = client.get("/simulations/sim-run-test")

    assert response.status_code == 200
    data = response.json()
    assert data["processing_id"] == "sim-run-test"
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["status"] == "success"
    assert data["jobs"][0]["biosimulations_run_id"] == "ext-123"


def test_get_simulation_status_invalid_token_is_401() -> None:
    response = TestClient(app).get(
        "/simulations/sim-run-test",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    detail = str(response.json().get("detail", ""))
    assert "Malformed token" in detail or "Invalid token" in detail


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_not_found(mock_get_temporal: MagicMock, mock_get_runs_db: MagicMock) -> None:
    """Test 404 when no DB records exist (auth runs before Temporal)."""
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = []
    mock_get_runs_db.return_value = runs_db

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
    owner_sub: str | None = None,
    visibility: str = "public",
) -> object:
    """Build a SimulationRunRecord for the fallback / hybrid-merge tests."""
    from datetime import datetime, timezone
    from typing import Literal

    from biosim_server.simulations.models import SimulationRunRecord

    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    vis: Literal["public", "private"] = "private" if visibility == "private" else "public"
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
        owner_sub=owner_sub,
        visibility=vis,
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

@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_explicit_matches_combined(
    mock_get_temporal: MagicMock, mock_get_runs_db: MagicMock
) -> None:
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

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record("abc123", processing_id="sim-run-test", visibility="public"),
    ]
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).get("/simulations/sim-run-test/status")
    assert response.status_code == 200
    assert response.json()["processing_id"] == "sim-run-test"


def test_get_simulation_status_explicit_invalid_token_is_401() -> None:
    response = TestClient(app).get(
        "/simulations/sim-run-test/status",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    detail = str(response.json().get("detail", ""))
    assert "Malformed token" in detail or "Invalid token" in detail


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_private_anonymous_404(
    mock_get_temporal: MagicMock, mock_get_runs_db: MagicMock
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1", processing_id="sim-run-p", owner_sub="auth0|owner", visibility="private"
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    client = TestClient(app)
    assert client.get("/simulations/sim-run-p").status_code == 404
    assert client.get("/simulations/sim-run-p/status").status_code == 404
    mock_get_temporal.assert_not_called()


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_private_non_owner_404(
    mock_get_temporal: MagicMock, mock_get_runs_db: MagicMock
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1", processing_id="sim-run-p", owner_sub="auth0|owner", visibility="private"
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    user = make_authenticated_user(sub="auth0|other")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        client = TestClient(app)
        assert client.get("/simulations/sim-run-p").status_code == 404
        assert client.get("/simulations/sim-run-p/status").status_code == 404
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    mock_get_temporal.assert_not_called()


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
def test_get_simulation_status_private_owner_200(
    mock_get_temporal: MagicMock, mock_get_runs_db: MagicMock
) -> None:
    from biosim_server.simulations.models import ConglomerateStatus, SimulationJobStatus

    expected = ConglomerateStatus(
        processing_id="sim-run-p",
        jobs=[
            SimulationJobStatus(
                job_id="job-1", simulator_id="copasi", version="4.34.251", status="success"
            )
        ],
    )
    temporal_client = MagicMock()
    workflow_handle = AsyncMock()
    workflow_handle.query.return_value = expected
    temporal_client.get_workflow_handle.return_value = workflow_handle
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1", processing_id="sim-run-p", owner_sub="auth0|owner", visibility="private"
        ),
    ]
    mock_get_runs_db.return_value = runs_db

    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        client = TestClient(app)
        assert client.get("/simulations/sim-run-p").status_code == 200
        assert client.get("/simulations/sim-run-p/status").status_code == 200
    finally:
        app.dependency_overrides.pop(get_optional_user, None)


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


def test_get_simulation_results_invalid_token_is_401() -> None:
    response = TestClient(app).get(
        "/simulations/sim-run-r/results",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    detail = str(response.json().get("detail", ""))
    assert "Malformed token" in detail or "Invalid token" in detail


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_results_not_found(mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = []
    mock_get_runs_db.return_value = runs_db
    mock_get_biosim.return_value = AsyncMock()

    response = TestClient(app).get("/simulations/nonexistent/results")
    assert response.status_code == 404


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_results_private_anonymous_404(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1",
            processing_id="sim-run-r",
            biosimulations_run_id="biosim-1",
            owner_sub="auth0|owner",
            visibility="private",
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    biosim_service = AsyncMock()
    mock_get_biosim.return_value = biosim_service

    response = TestClient(app).get("/simulations/sim-run-r/results")
    assert response.status_code == 404
    biosim_service.get_hdf5_metadata.assert_not_awaited()


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_results_private_non_owner_404(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1",
            processing_id="sim-run-r",
            biosimulations_run_id="biosim-1",
            owner_sub="auth0|owner",
            visibility="private",
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    biosim_service = AsyncMock()
    mock_get_biosim.return_value = biosim_service

    user = make_authenticated_user(sub="auth0|other")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).get("/simulations/sim-run-r/results")
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 404
    biosim_service.get_hdf5_metadata.assert_not_awaited()


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_results_private_owner_200(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    from biosim_server.biosim_runs import HDF5File

    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1",
            processing_id="sim-run-r",
            biosimulations_run_id="biosim-1",
            owner_sub="auth0|owner",
            visibility="private",
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    biosim_service = AsyncMock()
    biosim_service.get_hdf5_metadata.return_value = HDF5File(
        filename="results.h5", id="biosim-1", uri="biosim-1", groups=[]
    )
    mock_get_biosim.return_value = biosim_service

    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).get("/simulations/sim-run-r/results")
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 200
    biosim_service.get_hdf5_metadata.assert_awaited_once_with("biosim-1")


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


def test_get_simulation_logs_invalid_token_is_401() -> None:
    response = TestClient(app).get(
        "/simulations/sim-run-l/logs",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    detail = str(response.json().get("detail", ""))
    assert "Malformed token" in detail or "Invalid token" in detail


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_logs_private_anonymous_404(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1",
            processing_id="sim-run-l",
            biosimulations_run_id="biosim-1",
            owner_sub="auth0|owner",
            visibility="private",
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    biosim_service = AsyncMock()
    mock_get_biosim.return_value = biosim_service

    response = TestClient(app).get("/simulations/sim-run-l/logs")
    assert response.status_code == 404
    biosim_service.get_sim_run_logs.assert_not_awaited()


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_logs_private_non_owner_404(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1",
            processing_id="sim-run-l",
            biosimulations_run_id="biosim-1",
            owner_sub="auth0|owner",
            visibility="private",
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    biosim_service = AsyncMock()
    mock_get_biosim.return_value = biosim_service

    user = make_authenticated_user(sub="auth0|other")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).get("/simulations/sim-run-l/logs")
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 404
    biosim_service.get_sim_run_logs.assert_not_awaited()


@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_get_simulation_logs_private_owner_200(
    mock_get_runs_db: MagicMock, mock_get_biosim: MagicMock
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [
        _make_run_record(
            "job-1",
            processing_id="sim-run-l",
            biosimulations_run_id="biosim-1",
            owner_sub="auth0|owner",
            visibility="private",
        ),
    ]
    mock_get_runs_db.return_value = runs_db
    biosim_service = AsyncMock()
    biosim_service.get_sim_run_logs.return_value = {"stdout": "hello"}
    mock_get_biosim.return_value = biosim_service

    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).get("/simulations/sim-run-l/logs")
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 200
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
def test_cancel_simulation_run_invalid_token_is_401(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock
) -> None:
    response = TestClient(app).post(
        "/simulations/sim-run-c/cancel",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    detail = str(response.json().get("detail", ""))
    assert "Malformed token" in detail or "Invalid token" in detail


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_not_found(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock, authenticated_user: AuthenticatedUser
) -> None:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = []
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).post("/simulations/nonexistent/cancel")
    assert response.status_code == 404


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_forbidden_for_non_owner(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock, authenticated_user: AuthenticatedUser
) -> None:
    runs_db = AsyncMock()
    record = _make_run_record("job-1", processing_id="sim-run-c")
    record.email = "someone-else@example.com"  # type: ignore[attr-defined]
    record.owner_sub = "auth0|someone-else"  # type: ignore[attr-defined]
    runs_db.get_simulation_runs_by_processing_id.return_value = [record]
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).post("/simulations/sim-run-c/cancel")
    assert response.status_code == 403


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_success(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock, authenticated_user: AuthenticatedUser
) -> None:
    from biosim_server.simulations.models import SimulationRunRecord

    record: SimulationRunRecord = _make_run_record(  # type: ignore[assignment]
        "job-1", processing_id="sim-run-c", status="CREATED",
        owner_sub=authenticated_user.sub, visibility="private",
    )
    record.email = authenticated_user.email
    record.owner_sub = authenticated_user.sub

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
def test_cancel_simulation_run_success_matching_sub_different_email(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock, authenticated_user: AuthenticatedUser
) -> None:
    from biosim_server.simulations.models import SimulationRunRecord

    record: SimulationRunRecord = _make_run_record(  # type: ignore[assignment]
        "job-1",
        processing_id="sim-run-c",
        status="CREATED",
        owner_sub=authenticated_user.sub,
        visibility="private",
    )
    record.email = "other@example.com"

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


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_cancel_simulation_run_forbidden_matching_email_different_sub(
    mock_get_runs_db: MagicMock, mock_get_temporal: MagicMock, authenticated_user: AuthenticatedUser
) -> None:
    record = _make_run_record(
        "job-1",
        processing_id="sim-run-c",
        status="CREATED",
        owner_sub="auth0|someone-else",
        visibility="private",
    )
    record.email = authenticated_user.email  # type: ignore[attr-defined]
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = [record]
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).post("/simulations/sim-run-c/cancel")
    assert response.status_code == 403


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


# --------------------------- DELETE /{processing_id} ---------------------------

@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_requires_authentication(mock_get_runs_db: MagicMock) -> None:
    response = TestClient(app).delete("/simulations/sim-run-d")
    assert response.status_code == 401


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_invalid_token_is_401(mock_get_runs_db: MagicMock) -> None:
    response = TestClient(app).delete(
        "/simulations/sim-run-d",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    detail = str(response.json().get("detail", ""))
    assert "Malformed token" in detail or "Invalid token" in detail


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_not_found_roleless_caller_forbidden(
    mock_get_runs_db: MagicMock, authenticated_user: AuthenticatedUser
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
        record = _make_run_record(
            "job-1",
            processing_id="sim-run-d",
            owner_sub="auth0|someone-else",
            visibility="private",
        )
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
def test_delete_simulation_run_success_publisher_matching_sub_different_email(
    mock_get_runs_db: MagicMock,
) -> None:
    user = make_authenticated_user(roles=["publisher"], sub="auth0|pub")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        runs_db = AsyncMock()
        record = _make_run_record(
            "job-1",
            processing_id="sim-run-d",
            owner_sub=user.sub,
            visibility="private",
        )
        record.email = "other@example.com"  # type: ignore[attr-defined]
        runs_db.get_simulation_runs_by_processing_id.return_value = [record]
        mock_get_runs_db.return_value = runs_db

        response = TestClient(app).delete("/simulations/sim-run-d")
        assert response.status_code == 204
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_delete_simulation_run_forbidden_publisher_matching_email_different_sub(
    mock_get_runs_db: MagicMock,
) -> None:
    user = make_authenticated_user(roles=["publisher"], sub="auth0|pub")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        runs_db = AsyncMock()
        record = _make_run_record(
            "job-1",
            processing_id="sim-run-d",
            owner_sub="auth0|someone-else",
            visibility="private",
        )
        record.email = user.email  # type: ignore[attr-defined]
        runs_db.get_simulation_runs_by_processing_id.return_value = [record]
        mock_get_runs_db.return_value = runs_db

        response = TestClient(app).delete("/simulations/sim-run-d")
        assert response.status_code == 403
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
def test_list_simulation_runs_invalid_token_is_401(mock_get_runs_db: MagicMock) -> None:
    response = TestClient(app).post(
        "/simulations/runs",
        json={"type": "all", "filters": [], "pagination": {"page": 1, "perPage": 20}},
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    detail = str(response.json().get("detail", ""))
    assert "Malformed token" in detail or "Invalid token" in detail


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
def test_list_simulation_runs_user_type_authenticated_without_email_200(mock_get_runs_db: MagicMock) -> None:
    """Authenticated with a subject but no email: type "user" still works -- ACL is owner_sub."""
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = ([], 0)
    mock_get_runs_db.return_value = runs_db

    user = make_authenticated_user(email=None, roles=["admin"])
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).post(
            "/simulations/runs",
            json={"type": "user", "filters": [], "pagination": {"page": 1, "perPage": 20}},
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 200
    assert runs_db.query_simulation_runs.call_args.kwargs["viewer"] is user


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_unauthenticated_email_filter_is_not_ownership(
    mock_get_runs_db: MagicMock,
) -> None:
    """Email is a table filter, not ACL. Anonymous callers still only see public rows."""
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = ([], 0)
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).post(
        "/simulations/runs",
        json={
            "type": "all",
            "filters": [{"id": "email", "operator": "equal", "value": "victim@example.com"}],
            "pagination": {"page": 1, "perPage": 20},
        },
    )
    assert response.status_code == 200
    assert runs_db.query_simulation_runs.call_args.kwargs["viewer"] is None


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_email_filter_wrong_user_still_acl_gated(mock_get_runs_db: MagicMock) -> None:
    """A non-admin may filter by another email; Mongo ACL still hides others' private runs."""
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
                "filters": [{"id": "email", "operator": "equal", "value": "victim@example.com"}],
                "pagination": {"page": 1, "perPage": 20},
            },
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 200
    assert runs_db.query_simulation_runs.call_args.kwargs["viewer"] is user


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_email_filter_own_email_allowed(mock_get_runs_db: MagicMock) -> None:
    """Email table filters remain available; ownership is the viewer passed to the query layer."""
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
    assert runs_db.query_simulation_runs.call_args.kwargs["viewer"] is user


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_email_filter_contains_operator_allowed_for_non_admin(
    mock_get_runs_db: MagicMock,
) -> None:
    """Contains-on-email is a table filter; private rows stay hidden by owner_sub/visibility ACL."""
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
                "filters": [{"id": "email", "operator": "contains", "value": "caller@example.com"}],
                "pagination": {"page": 1, "perPage": 20},
            },
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 200


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_email_filter_admin_arbitrary_value_allowed(mock_get_runs_db: MagicMock) -> None:
    """Admin listing is unrestricted by visibility; email remains a table filter."""
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
    assert runs_db.query_simulation_runs.call_args.kwargs["viewer"] is user


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_list_simulation_runs_user_type_ignores_body_user_field(mock_get_runs_db: MagicMock) -> None:
    """type=user scopes by JWT sub, never by the client-supplied user email."""
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
    assert sent_request.user is None
    assert runs_db.query_simulation_runs.call_args.kwargs["viewer"] is user


# --------------------------------------------------------------------------
# OMEX authorization on run creation
#
# The omex_id in the request body is a content hash. It must never work as an
# access capability: another owner's private archive has to be unreachable, and
# unreachable *before* the workflow starts.
# --------------------------------------------------------------------------

def _private_omex(owner_sub: str) -> OmexFile:
    return OmexFile(
        file_hash_md5="abc123def456",
        uploaded_filename="private.omex",
        bucket_name="test-bucket",
        omex_gcs_path="verify/omex/abc123def456.omex",
        file_size=1024,
        owner_sub=owner_sub,
        visibility="private",
    )


def _omex_db_resolving_for(allowed_viewer: str | None, omex_file: OmexFile) -> AsyncMock:
    """An OMEX DB whose resolver only answers for `allowed_viewer` -- like the real one."""
    omex_db = AsyncMock()

    async def _resolve(file_hash_md5: str, viewer_sub: str | None = None) -> OmexFile | None:
        return omex_file if viewer_sub == allowed_viewer else None

    omex_db.find_accessible_omex_file.side_effect = _resolve
    return omex_db


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_other_users_private_omex_is_404_and_never_starts_a_workflow(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """Knowing the hash of Alice's private archive must not let Bob execute it."""
    mock_get_omex_db.return_value = _omex_db_resolving_for("auth0|alice", _private_omex("auth0|alice"))

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client
    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

    bob = make_authenticated_user(sub="auth0|bob", email="bob@example.com")
    app.dependency_overrides[get_optional_user] = lambda: bob
    try:
        response = TestClient(app).post("/simulations/run", json=_make_request())
    finally:
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 404
    temporal_client.start_workflow.assert_not_called()
    runs_db.insert_simulation_run.assert_not_called()


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_anonymous_cannot_execute_a_private_omex(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """A public (anonymous) run can only reference a public archive."""
    mock_get_omex_db.return_value = _omex_db_resolving_for("auth0|alice", _private_omex("auth0|alice"))

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client
    mock_get_runs_db.return_value = AsyncMock()

    response = TestClient(app).post("/simulations/run", json=_make_request())

    assert response.status_code == 404
    temporal_client.start_workflow.assert_not_called()


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_owner_can_execute_own_private_omex(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    omex_db = _omex_db_resolving_for("auth0|alice", _private_omex("auth0|alice"))
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client
    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

    alice = make_authenticated_user(sub="auth0|alice", email="alice@example.com")
    app.dependency_overrides[get_optional_user] = lambda: alice
    try:
        response = TestClient(app).post("/simulations/run", json=_make_request())
    finally:
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    temporal_client.start_workflow.assert_called_once()
    # The resolver was asked on the caller's behalf, not with a body-supplied identity.
    assert omex_db.find_accessible_omex_file.call_args.kwargs["viewer_sub"] == "auth0|alice"


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_persist_failure_is_fatal_before_start_workflow(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """No record means no owner and no ACL -- the workflow must not start at all."""
    omex_db = AsyncMock()
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client

    runs_db = AsyncMock()
    runs_db.insert_simulation_run.side_effect = Exception("mongo is down")
    mock_get_runs_db.return_value = runs_db

    response = TestClient(app).post("/simulations/run", json=_make_request())

    assert response.status_code == 503
    temporal_client.start_workflow.assert_not_called()
    # Partial inserts are rolled back so no half-created run is left behind.
    runs_db.delete_simulation_runs_by_processing_id.assert_called_once()


@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_without_runs_db_is_503_not_an_unowned_run(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
) -> None:
    omex_db = AsyncMock()
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    temporal_client = AsyncMock()
    mock_get_temporal.return_value = temporal_client

    with patch(
        "biosim_server.simulations.router.get_simulation_run_database_service", return_value=None
    ):
        response = TestClient(app).post("/simulations/run", json=_make_request())

    assert response.status_code == 503
    temporal_client.start_workflow.assert_not_called()


# --------------------------------------------------------------------------
# Email redaction on the public listing
# --------------------------------------------------------------------------

def _listing_response(user: AuthenticatedUser | None, records: list[object]) -> dict[str, object]:
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = (records, len(records))
    with patch(
        "biosim_server.simulations.router.get_simulation_run_database_service", return_value=runs_db
    ):
        if user is not None:
            app.dependency_overrides[get_optional_user] = lambda: user
        try:
            response = TestClient(app).post("/simulations/runs", json={"type": "all"})
        finally:
            app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


def test_list_simulation_runs_redacts_email_from_anonymous_callers() -> None:
    """`type=all` is the public catalog -- it must not enumerate runners' emails."""
    record = _make_run_record(
        "job-1", processing_id="sim-run-a", owner_sub="auth0|owner", visibility="public"
    )
    body = _listing_response(None, [record])
    runs = body["runs"]
    assert isinstance(runs, list)
    assert runs[0]["email"] is None
    assert "user@example.com" not in response_text(body)


def test_list_simulation_runs_redacts_email_from_other_users() -> None:
    record = _make_run_record(
        "job-1", processing_id="sim-run-a", owner_sub="auth0|owner", visibility="public"
    )
    body = _listing_response(make_authenticated_user(sub="auth0|someone-else"), [record])
    runs = body["runs"]
    assert isinstance(runs, list)
    assert runs[0]["email"] is None


def test_list_simulation_runs_shows_email_to_the_owner() -> None:
    record = _make_run_record(
        "job-1", processing_id="sim-run-a", owner_sub="auth0|owner", visibility="private"
    )
    body = _listing_response(make_authenticated_user(sub="auth0|owner"), [record])
    runs = body["runs"]
    assert isinstance(runs, list)
    assert runs[0]["email"] == "user@example.com"


def test_list_simulation_runs_shows_email_to_admins() -> None:
    record = _make_run_record(
        "job-1", processing_id="sim-run-a", owner_sub="auth0|owner", visibility="public"
    )
    admin = make_authenticated_user(sub="auth0|admin", roles=["admin"])
    body = _listing_response(admin, [record])
    runs = body["runs"]
    assert isinstance(runs, list)
    assert runs[0]["email"] == "user@example.com"


def response_text(body: dict[str, object]) -> str:
    import json

    return json.dumps(body)


# --------------------------------------------------------------------------
# Publicity control: PATCH /simulations/{id}/visibility
# --------------------------------------------------------------------------

VISIBILITY_URL = "/simulations/sim-run-v/visibility"


def _visibility_mocks(
    records: list[object],
    *,
    own_omex: OmexFile | None = None,
    accessible_omex: OmexFile | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    runs_db = AsyncMock()
    runs_db.get_simulation_runs_by_processing_id.return_value = records
    runs_db.set_visibility_by_processing_id.return_value = len(records)
    omex_db = AsyncMock()
    omex_db.get_omex_file.return_value = own_omex
    omex_db.find_accessible_omex_file.return_value = accessible_omex
    return runs_db, omex_db


def _public_omex(owner_sub: str | None = None) -> OmexFile:
    return OmexFile(
        file_hash_md5="abc123def456",
        uploaded_filename="model.omex",
        bucket_name="test-bucket",
        omex_gcs_path="verify/omex/abc123def456.omex",
        file_size=1024,
        owner_sub=owner_sub,
        visibility="public",
    )


def _owned_private_run() -> object:
    record = _make_run_record(
        "job-1", processing_id="sim-run-v", owner_sub="auth0|owner", visibility="private"
    )
    record.omex_id = "abc123def456"  # type: ignore[attr-defined]
    return record


def test_set_visibility_requires_authentication() -> None:
    response = TestClient(app).patch(VISIBILITY_URL, json={"visibility": "public"})
    assert response.status_code == 401


def test_set_visibility_invalid_token_is_401() -> None:
    response = TestClient(app).patch(
        VISIBILITY_URL, json={"visibility": "public"}, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_set_visibility_rejects_unknown_values() -> None:
    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).patch(VISIBILITY_URL, json={"visibility": "unlisted"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 422


def test_set_visibility_forbidden_for_non_owner() -> None:
    runs_db, omex_db = _visibility_mocks([_owned_private_run()])
    user = make_authenticated_user(sub="auth0|intruder")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch(
            "biosim_server.simulations.router.get_simulation_run_database_service", return_value=runs_db
        ), patch(
            "biosim_server.simulations.router.get_omex_database_service", return_value=omex_db
        ):
            response = TestClient(app).patch(VISIBILITY_URL, json={"visibility": "public"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 403
    runs_db.set_visibility_by_processing_id.assert_not_called()


def test_set_visibility_not_found_for_unknown_run() -> None:
    runs_db, omex_db = _visibility_mocks([])
    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch(
            "biosim_server.simulations.router.get_simulation_run_database_service", return_value=runs_db
        ), patch(
            "biosim_server.simulations.router.get_omex_database_service", return_value=omex_db
        ):
            response = TestClient(app).patch(VISIBILITY_URL, json={"visibility": "public"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 404


def test_owner_publishes_run_and_its_private_omex_together() -> None:
    """A public run must not point at a private archive, so publishing does both."""
    private_omex = _private_omex("auth0|owner")
    runs_db, omex_db = _visibility_mocks([_owned_private_run()], own_omex=private_omex)
    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch(
            "biosim_server.simulations.router.get_simulation_run_database_service", return_value=runs_db
        ), patch(
            "biosim_server.simulations.router.get_omex_database_service", return_value=omex_db
        ):
            response = TestClient(app).patch(VISIBILITY_URL, json={"visibility": "public"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json()["visibility"] == "public"
    runs_db.set_visibility_by_processing_id.assert_called_once_with("sim-run-v", "public")
    omex_db.set_omex_visibility.assert_called_once_with(
        file_hash_md5="abc123def456", owner_sub="auth0|owner", visibility="public"
    )


def test_publishing_leaves_an_already_public_omex_alone() -> None:
    runs_db, omex_db = _visibility_mocks(
        [_owned_private_run()], own_omex=_public_omex("auth0|owner")
    )
    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch(
            "biosim_server.simulations.router.get_simulation_run_database_service", return_value=runs_db
        ), patch(
            "biosim_server.simulations.router.get_omex_database_service", return_value=omex_db
        ):
            response = TestClient(app).patch(VISIBILITY_URL, json={"visibility": "public"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    omex_db.set_omex_visibility.assert_not_called()


def test_admin_cannot_publish_a_run_backed_by_someone_elses_private_omex() -> None:
    """Publishing must never expose an archive the publisher does not own."""
    runs_db, omex_db = _visibility_mocks(
        [_owned_private_run()], own_omex=None, accessible_omex=None
    )
    admin = make_authenticated_user(sub="auth0|admin", roles=["admin"])
    app.dependency_overrides[get_current_user] = lambda: admin
    try:
        with patch(
            "biosim_server.simulations.router.get_simulation_run_database_service", return_value=runs_db
        ), patch(
            "biosim_server.simulations.router.get_omex_database_service", return_value=omex_db
        ):
            response = TestClient(app).patch(VISIBILITY_URL, json={"visibility": "public"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 409
    runs_db.set_visibility_by_processing_id.assert_not_called()
    omex_db.set_omex_visibility.assert_not_called()


def test_owner_unpublishes_without_touching_the_shared_omex() -> None:
    """Going private again must not un-publish an archive other public runs may use."""
    record = _make_run_record(
        "job-1", processing_id="sim-run-v", owner_sub="auth0|owner", visibility="public"
    )
    record.omex_id = "abc123def456"  # type: ignore[attr-defined]
    runs_db, omex_db = _visibility_mocks([record], own_omex=_public_omex("auth0|owner"))
    user = make_authenticated_user(sub="auth0|owner")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch(
            "biosim_server.simulations.router.get_simulation_run_database_service", return_value=runs_db
        ), patch(
            "biosim_server.simulations.router.get_omex_database_service", return_value=omex_db
        ):
            response = TestClient(app).patch(VISIBILITY_URL, json={"visibility": "private"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    runs_db.set_visibility_by_processing_id.assert_called_once_with("sim-run-v", "private")
    omex_db.set_omex_visibility.assert_not_called()


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
@patch("biosim_server.simulations.router.get_temporal_client")
@patch("biosim_server.simulations.router.get_biosim_service")
@patch("biosim_server.simulations.router.get_omex_database_service")
def test_run_simulations_token_without_email_does_not_fall_back_to_the_body(
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_temporal: MagicMock,
    mock_get_runs_db: MagicMock,
) -> None:
    """An identified caller's contact email comes from the token or not at all.

    Auth0 access tokens carry no email claim unless a Post-Login Action adds one,
    so this is the common case -- and it must not become "whatever address the
    client typed", which would look like verified contact data on the record.
    Ownership is unaffected either way: it is the token `sub`.
    """
    omex_db = AsyncMock()
    omex_db.find_accessible_omex_file.return_value = MOCK_OMEX_FILE
    mock_get_omex_db.return_value = omex_db

    biosim_service = AsyncMock()
    biosim_service.get_simulator_versions.return_value = MOCK_SIMULATOR_VERSIONS
    mock_get_biosim.return_value = biosim_service

    mock_get_temporal.return_value = AsyncMock()
    runs_db = AsyncMock()
    mock_get_runs_db.return_value = runs_db

    user = make_authenticated_user(sub="auth0|no-email", email=None)
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        request = _make_request()
        assert request["email_address"]  # the body does supply one
        response = TestClient(app).post("/simulations/run", json=request)
    finally:
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    inserted = runs_db.insert_simulation_run.call_args.args[0]
    assert inserted.email is None
    assert inserted.owner_sub == "auth0|no-email"
    assert inserted.visibility == "private"
