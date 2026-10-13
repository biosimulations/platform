from temporalio.service import RPCError, RPCStatusCode
import asyncio
import base64
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from biosim_server.api.main import app
from biosim_server.biosim_omex import OmexDatabaseServiceMongo, OmexFile
from biosim_server.biosim_runs import BiosimServiceRest, BiosimulatorVersion, DatabaseServiceMongo
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflowInput
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput
from biosim_server.biosim_verify.database import (
    VERIFICATION_CURSOR_MAX_LENGTH,
    VerificationCursor,
    VerificationDatabaseServiceMongo,
    VerificationIdPage,
    encode_verification_cursor,
)
from biosim_server.biosim_verify.models import (
    VERIFICATION_IDS_DEFAULT_PAGE_SIZE,
    VERIFICATION_IDS_MAX_PAGE_SIZE,
    VerificationLedgerRecord,
    VerificationRecord,
    VerificationRun,
    VerificationType,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.common.auth import AuthenticatedUser, get_current_user, get_optional_user
from biosim_server.common.storage import FileServiceGCS
from biosim_server.config import get_settings
from biosim_server.version import __version__
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response

from temporalio.client import Client
from temporalio.worker import Worker
from tests.biosim_verify.test_omex_verify_workflows import assert_omex_verify_results
from tests.biosim_verify.test_runs_verify_workflow import assert_runs_verify_results


@pytest.mark.asyncio
async def test_root() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.get("/")
        assert response.status_code == 200
        assert response.json() == {'docs': 'https://biosim.biosimulations.org/docs', 'version': __version__ }


@pytest.mark.asyncio
async def test_version() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.get("/version")
        assert response.status_code == 200
        assert response.json() == __version__


@pytest.mark.asyncio
async def test_health_always_ok() -> None:
    """Liveness probe: /health reports "ok" unconditionally, with no dependency checks
    (mongo/temporal down should not flip this -- that's what /ready is for)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@patch("biosim_server.api.main.get_temporal_client")
@patch("biosim_server.api.main.get_mongo_client")
@pytest.mark.asyncio
async def test_ready_when_dependencies_up(mock_get_mongo_client: MagicMock, mock_get_temporal_client: MagicMock) -> None:
    """Readiness probe: /ready returns 200 with both checks true when Mongo answers
    `admin.command` and a Temporal client is available. Both dependencies are mocked
    so this doesn't need real infra running."""
    mock_mongo_client = MagicMock()
    mock_mongo_client.admin.command = AsyncMock(return_value={"ok": 1})
    mock_get_mongo_client.return_value = mock_mongo_client
    mock_get_temporal_client.return_value = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"] == {"mongodb": True, "temporal": True}


@patch("biosim_server.api.main.get_temporal_client")
@patch("biosim_server.api.main.get_mongo_client")
@pytest.mark.asyncio
async def test_ready_when_mongo_down(mock_get_mongo_client: MagicMock, mock_get_temporal_client: MagicMock) -> None:
    """Readiness probe: /ready returns 503 and checks.mongodb=False when
    get_mongo_client() yields no client (e.g. Mongo unreachable), even though
    Temporal is still up -- a single failed dependency should fail the whole probe."""
    mock_get_mongo_client.return_value = None
    mock_get_temporal_client.return_value = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not ready"
        assert body["checks"]["mongodb"] is False


@patch("biosim_server.api.main.get_temporal_client")
@patch("biosim_server.api.main.get_verification_database_service")
def test_get_output_not_found(mock_get_ledger: MagicMock, mock_get_temporal: MagicMock) -> None:
    """GET /verify/{workflow_id} returns 404 when Temporal returns NOT_FOUND and the ledger has no row."""
    from temporalio.service import RPCError, RPCStatusCode

    temporal = MagicMock()
    handle = AsyncMock()
    not_found_err = RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
    handle.describe = AsyncMock(side_effect=not_found_err)
    temporal.get_workflow_handle.return_value = handle
    mock_get_temporal.return_value = temporal

    # ledger returns None → generic 404 (no existence leak)
    ledger = AsyncMock()
    ledger.get_verification = AsyncMock(return_value=None)
    mock_get_ledger.return_value = ledger

    user = AuthenticatedUser(sub="auth0|test-user-id", email="user@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        response = TestClient(app).get("/verify/non-existent-id")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 404
    assert "non-existent-id" in response.json()["detail"]


@patch("biosim_server.api.main.get_cached_omex_file_from_upload", new_callable=AsyncMock)
@patch("biosim_server.api.main.get_biosim_service")
@patch("biosim_server.api.main.get_omex_database_service")
@patch("biosim_server.api.main.get_file_service")
@patch("biosim_server.api.main.get_temporal_client")
def test_verify_omex_unknown_simulator(
    mock_get_temporal: MagicMock,
    mock_get_file: MagicMock,
    mock_get_omex_db: MagicMock,
    mock_get_biosim: MagicMock,
    mock_get_cached: AsyncMock,
) -> None:
    """POST /verify/omex returns 400 to an authenticated caller when a requested simulator is not known."""
    mock_get_file.return_value = MagicMock()
    mock_get_omex_db.return_value = MagicMock()
    mock_get_cached.return_value = OmexFile(
        file_hash_md5="abc123",
        uploaded_filename="t.omex",
        bucket_name="test-bucket",
        omex_gcs_path="omex/abc123/t.omex",
        file_size=1,
    )
    biosim = AsyncMock()
    biosim.get_simulator_versions.return_value = []
    mock_get_biosim.return_value = biosim

    ledger = AsyncMock()

    user = AuthenticatedUser(sub="auth0|test-user-id", email="user@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            response = TestClient(app).post(
                "/verify/omex",
                files={"uploaded_file": ("t.omex", b"not-used", "application/zip")},
                params={"simulators": "unknown-sim"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert response.status_code == 400
    assert "unknown-sim" in response.json()["detail"]
    ledger.insert_verification.assert_not_awaited()
    mock_get_temporal.return_value.start_workflow.assert_not_called()
    # Simulators are resolved before the upload is stored, so a bad one costs no write.
    mock_get_cached.assert_not_awaited()


def _post_verify_omex(
    simulators: str,
    biosim: AsyncMock,
    ledger: AsyncMock,
) -> Response:
    """POST /verify/omex as an authenticated caller, with the heavy deps mocked out.

    Mirrors the patching in test_verify_omex_unknown_simulator so simulator
    resolution is the only thing under test.
    """
    omex_file = OmexFile(
        file_hash_md5="abc123",
        uploaded_filename="t.omex",
        bucket_name="test-bucket",
        omex_gcs_path="omex/abc123/t.omex",
        file_size=1,
    )
    user = AuthenticatedUser(sub="auth0|test-user-id", email="user@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with (
            patch("biosim_server.api.main.get_temporal_client") as mock_get_temporal,
            patch("biosim_server.api.main.get_file_service", return_value=MagicMock()),
            patch("biosim_server.api.main.get_omex_database_service", return_value=MagicMock()),
            patch("biosim_server.api.main.get_biosim_service", return_value=biosim),
            patch("biosim_server.api.main.get_cached_omex_file_from_upload", new=AsyncMock(return_value=omex_file)),
            patch("biosim_server.api.main.get_verification_database_service", return_value=ledger),
        ):
            # The handler asserts the returned handle echoes back the id it asked for.
            mock_get_temporal.return_value.start_workflow = AsyncMock(
                side_effect=lambda *args, **kwargs: MagicMock(id=kwargs["id"], run_id="wf-run-1")
            )
            response = TestClient(app).post(
                "/verify/omex",
                files={"uploaded_file": ("t.omex", b"not-used", "application/zip")},
                params={"simulators": simulators},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    return response


@pytest.mark.parametrize(
    "simulator",
    [
        pytest.param("copasi:4:34", id="too-many-colons"),
        pytest.param("copasi:4.34.251:extra", id="trailing-segment"),
        pytest.param("copasi:", id="empty-version"),
        pytest.param(":4.34.251", id="empty-name"),
        pytest.param(":", id="only-separator"),
    ],
)
def test_verify_omex_malformed_simulator_returns_400_not_500(simulator: str) -> None:
    """A malformed `simulator` param must be a 400, not an unhandled ValueError -> 500.

    Regression test for the unbounded `simulator.split(":")`: a value like
    `copasi:4:34` raised "too many values to unpack" inside the request handler.
    """
    biosim = AsyncMock()
    biosim.get_simulator_versions.return_value = [
        BiosimulatorVersion(
            id="copasi",
            name="COPASI",
            version="4.34.251",
            image_url="ghcr.io/biosimulators/copasi:4.34.251",
            image_digest="sha256:deadbeef",
            created="2026-01-01T00:00:00Z",
            updated="2026-01-01T00:00:00Z",
        )
    ]
    ledger = AsyncMock()

    response = _post_verify_omex(simulators=simulator, biosim=biosim, ledger=ledger)

    assert response.status_code == 400, f"expected 400 for {simulator!r}, got {response.status_code}"
    assert simulator in response.json()["detail"]
    # Rejected before any side effects -- no ledger row, no workflow.
    ledger.insert_verification.assert_not_awaited()


def test_verify_omex_wellformed_simulator_version_still_resolves() -> None:
    """Control for the fix above: `id:version` still splits into exactly one name/version pair."""
    biosim = AsyncMock()
    biosim.get_simulator_versions.return_value = [
        BiosimulatorVersion(
            id="copasi",
            name="COPASI",
            version="4.34.251",
            image_url="ghcr.io/biosimulators/copasi:4.34.251",
            image_digest="sha256:deadbeef",
            created="2026-01-01T00:00:00Z",
            updated="2026-01-01T00:00:00Z",
        )
    ]
    ledger = AsyncMock()

    response = _post_verify_omex(simulators="copasi:4.34.251", biosim=biosim, ledger=ledger)

    assert response.status_code == 200, response.text
    assert response.json()["workflow_status"] == VerifyWorkflowStatus.PENDING
    ledger.insert_verification.assert_awaited_once()


@pytest.fixture
def authenticated_verify_user(authenticated_user: AuthenticatedUser) -> Iterator[AuthenticatedUser]:
    """``authenticated_user`` for the optional-auth /verify/* endpoints too."""
    app.dependency_overrides[get_optional_user] = lambda: authenticated_user
    try:
        yield authenticated_user
    finally:
        app.dependency_overrides.pop(get_optional_user, None)


@pytest.mark.integration
@pytest.mark.skipif(len(get_settings().storage_gcs_credentials_file) == 0,
                    reason="gcs_credentials.json file not supplied")
@pytest.mark.usefixtures("authenticated_verify_user", "verification_database_service_mongo")
@pytest.mark.asyncio
async def test_omex_verify_and_get_output(omex_verify_workflow_input: OmexVerifyWorkflowInput,
                                         omex_verify_workflow_output: VerifyWorkflowOutput,
                                         omex_test_file: Path,
                                         database_service_mongo: DatabaseServiceMongo,
                                         omex_database_service_mongo: OmexDatabaseServiceMongo,
                                         file_service_gcs: FileServiceGCS,
                                         temporal_client: Client,
                                         temporal_verify_worker: Worker,
                                         biosim_service_rest: BiosimServiceRest) -> None:
    assert omex_verify_workflow_input.compare_settings.observables is not None
    query_params: dict[str, float | str | list[str]] = {
        "workflow_id_prefix": "verification-",
        "simulators": [f"{sim.id}:{sim.version}" for sim in omex_verify_workflow_input.requested_simulators],
        "include_outputs": omex_verify_workflow_input.compare_settings.include_outputs,
        "user_description": omex_verify_workflow_input.compare_settings.user_description,
        "observables": omex_verify_workflow_input.compare_settings.observables,
        "rel_tol": omex_verify_workflow_input.compare_settings.rel_tol,
        "abs_tol_min": omex_verify_workflow_input.compare_settings.abs_tol_min,
        "abs_tol_scale": omex_verify_workflow_input.compare_settings.abs_tol_scale
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        with open(omex_test_file, "rb") as file:
            upload_filename = omex_test_file.name
            files = {"uploaded_file": (upload_filename, file, "application/zip")}
            response = await test_client.post("/verify/omex", files=files, params=query_params)
            assert response.status_code == 200

        output = VerifyWorkflowOutput.model_validate(response.json())
        listing = await test_client.get("/verification_ids")
        assert listing.status_code == 200
        assert output.workflow_id in listing.json()["verification_ids"]

        # poll api until job is completed
        while output.workflow_status != VerifyWorkflowStatus.COMPLETED:
            await asyncio.sleep(5)
            response = await test_client.get(f"/verify/{output.workflow_id}")
            if response.status_code == 200:
                output = VerifyWorkflowOutput.model_validate(response.json())
                logging.info(f"polling, job status is: {output.workflow_status}")

        assert_omex_verify_results(observed_results=output, expected_results_template=omex_verify_workflow_output)


@pytest.mark.skipif(len(get_settings().storage_gcs_credentials_file) == 0,
                    reason="gcs_credentials.json file not supplied")
@pytest.mark.usefixtures("authenticated_verify_user", "verification_database_service_mongo")
@pytest.mark.asyncio
async def test_runs_verify_and_get_output(runs_verify_workflow_input: RunsVerifyWorkflowInput,
                                         runs_verify_workflow_output: VerifyWorkflowOutput,
                                         omex_test_file: Path,
                                         file_service_gcs: FileServiceGCS,
                                         database_service_mongo: DatabaseServiceMongo,
                                         omex_database_service_mongo: OmexDatabaseServiceMongo,
                                         temporal_client: Client,
                                         temporal_verify_worker: Worker,
                                         biosim_service_rest: BiosimServiceRest) -> None:
    assert runs_verify_workflow_input.compare_settings.observables is not None
    query_params: dict[str, float | str | list[str]] = {
        "workflow_id_prefix": "verification-",
        "biosimulations_run_ids": runs_verify_workflow_input.biosimulations_run_ids,
        "include_outputs": runs_verify_workflow_input.compare_settings.include_outputs,
        "user_description": runs_verify_workflow_input.compare_settings.user_description,
        "observables": runs_verify_workflow_input.compare_settings.observables,
        "rel_tol": runs_verify_workflow_input.compare_settings.rel_tol,
        "abs_tol_min": runs_verify_workflow_input.compare_settings.abs_tol_min,
        "abs_tol_scale": runs_verify_workflow_input.compare_settings.abs_tol_scale
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.post("/verify/runs", params=query_params)
        assert response.status_code == 200

        output = VerifyWorkflowOutput.model_validate(response.json())
        listing = await test_client.get("/verification_ids")
        assert listing.status_code == 200
        assert output.workflow_id in listing.json()["verification_ids"]

        # poll api until job is completed
        while output.workflow_status != VerifyWorkflowStatus.COMPLETED:
            await asyncio.sleep(5)
            response = await test_client.get(f"/verify/{output.workflow_id}")
            if response.status_code == 200:
                output = VerifyWorkflowOutput.model_validate(response.json())
                logging.info(f"polling workflow_id {output.workflow_id}, workflow status is: {output.workflow_status}")

        assert_runs_verify_results(observed_results=output, expected_results_template=runs_verify_workflow_output)


@pytest.mark.skipif(len(get_settings().storage_gcs_credentials_file) == 0,
                    reason="gcs_credentials.json file not supplied")
@pytest.mark.usefixtures("authenticated_verify_user", "verification_database_service_mongo")
@pytest.mark.asyncio
async def test_runs_verify_not_found(runs_verify_workflow_input: RunsVerifyWorkflowInput,
                                         runs_verify_workflow_output: VerifyWorkflowOutput,
                                         omex_test_file: Path,
                                         file_service_gcs: FileServiceGCS,
                                         database_service_mongo: DatabaseServiceMongo,
                                         temporal_client: Client,
                                         temporal_verify_worker: Worker,
                                         biosim_service_rest: BiosimServiceRest) -> None:
    assert runs_verify_workflow_input.compare_settings.observables is not None
    query_params: dict[str, float | str | list[str]] = {
        "workflow_id_prefix": "verification-",
        "biosimulations_run_ids": ["bad_run_id_1", "bad_run_id_2"],
        "include_outputs": runs_verify_workflow_input.compare_settings.include_outputs,
        "user_description": runs_verify_workflow_input.compare_settings.user_description,
        "observables": runs_verify_workflow_input.compare_settings.observables,
        "rel_tol": runs_verify_workflow_input.compare_settings.rel_tol,
        "abs_tol_min": runs_verify_workflow_input.compare_settings.abs_tol_min,
        "abs_tol_scale": runs_verify_workflow_input.compare_settings.abs_tol_scale
    }

    async with (AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client):
        response = await test_client.post("/verify/runs", params=query_params)
        assert response.status_code == 200

        output = VerifyWorkflowOutput.model_validate(response.json())
        listing = await test_client.get("/verification_ids")
        assert listing.status_code == 200
        assert output.workflow_id in listing.json()["verification_ids"]

        # poll api until job is completed
        while not output.workflow_status.is_done:
            await asyncio.sleep(5)
            response = await test_client.get(f"/verify/{output.workflow_id}")
            if response.status_code == 200:
                output = VerifyWorkflowOutput.model_validate(response.json())
                logging.info(f"polling, job status is: {output.workflow_status}")

        assert output.workflow_status == VerifyWorkflowStatus.RUN_ID_NOT_FOUND
        assert output.workflow_error in [ "Simulation run with id bad_run_id_1 not found.",
                                          "Simulation run with id bad_run_id_2 not found."]

@pytest.mark.asyncio
async def test_verify_omex_anonymous_start_is_ownerless_and_public() -> None:
    """No token (legacy API): 200; archive stored public, ledger row and output ownerless."""
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks()
    ledger = AsyncMock()
    with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
         patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
         patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            response = await test_client.post(
                "/verify/omex",
                files={"uploaded_file": ("m.omex", b"PK\x03\x04fake", "application/zip")},
                params={"simulators": ["copasi"]},
            )
    assert response.status_code == 200, response.text
    assert response.json()["owner_sub"] is None
    stored = omex_database.insert_omex_file.call_args.kwargs["omex_file"]
    assert stored.owner is None and stored.visibility == "public"
    assert ledger.insert_verification.call_args.args[0].owner_sub is None
    assert temporal.start_workflow.call_args.kwargs["args"][0].owner_sub is None


def _verify_omex_mocks(*, insert_raises: bool = False) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Build the four service mocks POST /verify/omex touches on a cache-miss path.

    OMEX cache-miss -> GCS upload -> Mongo insert (optionally failing) -> simulator
    resolution -> Temporal start_workflow.
    """
    file_service = MagicMock()
    file_service.upload_bytes = AsyncMock(return_value="omex/deadbeef/model.omex")

    omex_database = MagicMock()
    omex_database.get_omex_file_by_hash_and_owner = AsyncMock(return_value=None)
    omex_database.get_omex_file = AsyncMock(return_value=None)  # cache miss

    async def _insert(omex_file: OmexFile) -> OmexFile:
        if insert_raises:
            raise RuntimeError("mongo insert failed")
        return omex_file

    omex_database.insert_omex_file = AsyncMock(side_effect=_insert)

    biosim_service = MagicMock()
    biosim_service.get_simulator_versions = AsyncMock(
        return_value=[
            BiosimulatorVersion(
                id="copasi", name="COPASI", version="4.34.251",
                image_url="ghcr.io/biosimulators/copasi:4.34.251",
                image_digest="sha256:abc123",
                created="2024-01-01T00:00:00Z", updated="2024-01-01T00:00:00Z",
            )
        ]
    )

    temporal = MagicMock()

    async def _start_workflow(*_args: object, id: str = "", **_kwargs: object) -> MagicMock:
        handle = MagicMock()
        handle.id = id
        handle.run_id = "run-1"
        return handle

    temporal.start_workflow = AsyncMock(side_effect=_start_workflow)
    return file_service, omex_database, biosim_service, temporal


@pytest.mark.asyncio
async def test_verify_omex_stamps_verified_subject_as_owner_server_side() -> None:
    """The persisted OmexFile.owner is the caller's verified token ``sub`` -- there
    is no request field, query param, or header that lets the client set it."""
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks()
    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock(return_value=None)
    user = AuthenticatedUser(sub="auth0|verify-owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
             patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
             patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
             patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as test_client:
                response = await test_client.post(
                    "/verify/omex",
                    files={"uploaded_file": ("model.omex", b"PK\x03\x04fake", "application/zip")},
                    params={"simulators": ["copasi"], "owner": "auth0|attacker"},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    inserted = omex_database.insert_omex_file.call_args.kwargs["omex_file"]
    assert inserted.owner == "auth0|verify-owner"
    assert inserted.visibility == "private"
    # The spoof attempt in the query string is inert -- FastAPI never binds it.
    assert temporal.start_workflow.await_count == 1


@pytest.mark.asyncio
async def test_verify_omex_workflow_start_log_carries_no_subject_email_or_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Task 6 (logging privacy): the workflow-start log line must carry only
    non-sensitive correlation fields (OMEX hash, workflow id, visibility) --
    never the whole OmexFile repr, whose ``owner`` is a raw Auth0 subject,
    and never the caller's email."""
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks()
    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock(return_value=None)
    raw_sub = "auth0|log-privacy-owner"
    raw_email = "log-owner@example.com"
    user = AuthenticatedUser(sub=raw_sub, email=raw_email)
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    file_bytes = b"PK\x03\x04fake-log-privacy"
    try:
        with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
             patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
             patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
             patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
             caplog.at_level(logging.INFO):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as test_client:
                response = await test_client.post(
                    "/verify/omex",
                    files={"uploaded_file": ("model.omex", file_bytes, "application/zip")},
                    params={"simulators": ["copasi"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    rendered = caplog.text
    assert raw_sub not in rendered
    assert raw_email not in rendered
    # The useful correlation fields survive: OMEX hash, workflow id, visibility.
    file_hash = hashlib.md5(file_bytes).hexdigest()
    assert file_hash in rendered
    assert response.json()["workflow_id"] in rendered
    assert "visibility=private" in rendered


@pytest.mark.asyncio
async def test_verify_omex_does_not_start_workflow_when_omex_persistence_fails() -> None:
    """Invariant: the OMEX policy row must be durable before any workflow starts.
    If the Mongo insert raises, start_workflow must never be reached."""
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks(insert_raises=True)
    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock(return_value=None)
    user = AuthenticatedUser(sub="auth0|verify-owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
             patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
             patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
             patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as test_client:
                response = await test_client.post(
                    "/verify/omex",
                    files={"uploaded_file": ("model.omex", b"PK\x03\x04fake", "application/zip")},
                    params={"simulators": ["copasi"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code >= 500
    temporal.start_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_verify_runs_anonymous_start_is_ownerless() -> None:
    """No token (legacy API): 200 with an ownerless ledger row and workflow input."""
    temporal, ledger = _make_temporal_for_runs(), AsyncMock()
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
         patch("biosim_server.api.main._load_hdf5_metadata_for_preflight", new=AsyncMock(return_value={})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            response = await test_client.post("/verify/runs")
    assert response.status_code == 200, response.text
    assert response.json()["owner_sub"] is None
    assert ledger.insert_verification.call_args.args[0].owner_sub is None
    assert temporal.start_workflow.call_args.kwargs["args"][0].owner_sub is None


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_sub, expected", [(None, 200), ("auth0|owner", 401)])
async def test_get_verify_anonymous_reads_only_ownerless(owner_sub: str | None, expected: int) -> None:
    """No token: an ownerless (anonymous/legacy) verification is readable, an owned one is 401."""
    temporal = _temporal_with_describe(query_result=_make_verify_output(owner_sub=owner_sub))
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            response = await test_client.get("/verify/omex-verification-test")
    assert response.status_code == expected
    if expected == 401:
        assert "auth0|owner" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/verify/runs", "/verify/omex"])
async def test_verify_post_invalid_token_is_401_not_anonymous(path: str) -> None:
    """A present-but-invalid token is rejected, never downgraded to an anonymous start."""
    temporal, ledger = MagicMock(), AsyncMock()
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            response = await test_client.post(path, headers={"Authorization": "Bearer not-a-jwt"},
                                              files={"uploaded_file": ("m.omex", b"x", "application/zip")})
    assert response.status_code == 401
    ledger.insert_verification.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_verify_rejects_non_owner() -> None:
    """GET /verify/{id} with owner_sub set: a different authenticated user is 403."""
    from biosim_server.biosim_verify import CompareSettings

    output = VerifyWorkflowOutput(
        workflow_id="omex-verification-owned",
        compare_settings=CompareSettings(
            user_description="t",
            include_outputs=False,
            rel_tol=0.0001,
            abs_tol_min=0.001,
            abs_tol_scale=0.00001,
        ),
        workflow_status=VerifyWorkflowStatus.COMPLETED,
        timestamp="2024-01-01T00:00:00Z",
        owner_sub="auth0|owner",
    )
    temporal = _temporal_with_describe(query_result=output)

    user = AuthenticatedUser(sub="auth0|stranger", email="stranger@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
                response = await test_client.get("/verify/omex-verification-owned")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_verify_legacy_ownerless_allows_any_authenticated_user() -> None:
    """Ownerless workflows (anonymous or pre-auth) are readable by any caller, logged in or not."""
    from biosim_server.biosim_verify import CompareSettings

    output = VerifyWorkflowOutput(
        workflow_id="omex-verification-legacy",
        compare_settings=CompareSettings(
            user_description="t",
            include_outputs=False,
            rel_tol=0.0001,
            abs_tol_min=0.001,
            abs_tol_scale=0.00001,
        ),
        workflow_status=VerifyWorkflowStatus.COMPLETED,
        timestamp="2024-01-01T00:00:00Z",
        owner_sub=None,
    )
    temporal = _temporal_with_describe(query_result=output)

    user = AuthenticatedUser(sub="auth0|anyone", email="anyone@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
                response = await test_client.get("/verify/omex-verification-legacy")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    assert response.json()["workflow_id"] == "omex-verification-legacy"


@pytest.mark.asyncio
async def test_demopublic() -> None:
    """GET /api/v1/demo/public needs no auth dependency at all -- reachable with
    no Authorization header and no dependency_overrides in play."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.get("/api/v1/demo/public")
        assert response.status_code == 200
        assert response.json() == {'message': 'This endpoint is public. Anyone can call it.'}

@pytest.mark.asyncio
async def test_demo_private_me() -> None:
    """GET /api/v1/demo/private/me with get_current_user overridden (rather than a
    real token) returns the injected user's email -- confirms the route reads the
    resolved AuthenticatedUser correctly without needing real JWT verification."""
    user = AuthenticatedUser(sub="auth0|test-user-id", email="user@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            response = await test_client.get("/api/v1/demo/private/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)

    assert response.status_code == 200
    assert response.json() == {'name': 'user@example.com'}


@pytest.mark.asyncio
async def test_demo_private_me_requires_authentication() -> None:
    """GET /api/v1/demo/private/me with no override and no bearer token: get_current_user
    rejects the request with 401 before the handler runs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.get("/api/v1/demo/private/me")
        assert response.status_code == 401


@asynccontextmanager
async def _authenticated_as(roles: list[str] | None = None) -> AsyncIterator[AsyncClient]:
    """Overrides get_current_user for the duration of the `with` block, yielding a client to call through it."""
    user = AuthenticatedUser(sub="auth0|test-user-id", email="user@example.com", roles=roles or [])
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)


@pytest.mark.asyncio
async def test_demo_private_animal_requires_authentication() -> None:
    """GET /api/v1/demo/private/animal with no token: rejected 401 before role
    checking even runs, same as /private/me."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.get("/api/v1/demo/private/animal")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_demo_private_animal_rejects_user_without_required_role() -> None:
    """Authenticated but with a role the endpoint doesn't recognize -- 403, not 401,
    since identity is verified but authorization still fails."""
    async with _authenticated_as(roles=["some-other-role"]) as test_client:
        response = await test_client.get("/api/v1/demo/private/animal")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_demo_private_animal_returns_zebra_for_admin() -> None:
    """Role -> response mapping: "admin" gets Zebra."""
    async with _authenticated_as(roles=["admin"]) as test_client:
        response = await test_client.get("/api/v1/demo/private/animal")
    assert response.status_code == 200
    assert response.json() == {'role': 'admin', 'animal': 'Zebra'}


@pytest.mark.asyncio
async def test_demo_private_animal_returns_giraffe_for_publisher() -> None:
    """Role -> response mapping: "publisher" gets Giraffe."""
    async with _authenticated_as(roles=["publisher"]) as test_client:
        response = await test_client.get("/api/v1/demo/private/animal")
    assert response.status_code == 200
    assert response.json() == {'role': 'publisher', 'animal': 'Giraffe'}


@pytest.mark.asyncio
async def test_demo_private_animal_returns_tiger_for_user() -> None:
    """Role -> response mapping: "user" gets Tiger."""
    async with _authenticated_as(roles=["user"]) as test_client:
        response = await test_client.get("/api/v1/demo/private/animal")
    assert response.status_code == 200
    assert response.json() == {'role': 'user', 'animal': 'Tiger'}


@pytest.mark.asyncio
async def test_demo_private_animal_prefers_most_privileged_role_when_multiple_present() -> None:
    """When a user carries multiple roles (["user", "admin"]), the endpoint picks the
    most privileged one (admin/Zebra) rather than e.g. the first or last in the list."""
    async with _authenticated_as(roles=["user", "admin"]) as test_client:
        response = await test_client.get("/api/v1/demo/private/animal")
    assert response.status_code == 200
    assert response.json() == {'role': 'admin', 'animal': 'Zebra'}


# Keycloak Auth Tests
#
# Unlike the tests above, which bypass verification via
# app.dependency_overrides[get_current_user], this one exercises the real
# JWT-verification path in common/auth/auth0.py end-to-end -- JWKS fetch,
# RS256 signature check, issuer/audience check, and email-claim extraction --
# against a token issued by a live Keycloak testcontainer
# (tests/fixtures/keycloak/realm.json). Requires the keycloak_async_client /
# alice_token fixture chain, hence @pytest.mark.integration_local.

@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_protected_route(keycloak_async_client: AsyncClient, alice_token: str) -> None:
    """GET /api/v1/demo/private/me with a real Keycloak-issued token for Alice:
    200, with `name` equal to Alice's email claim from the real, verified JWT."""
    response = await keycloak_async_client.get(
        "/api/v1/demo/private/me",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"name": "alice@example.com"}



# ---------------------------------------------------------------------------
# GET /verify/{workflow_id} — hardened handler tests (plan tests 17-26)
# ---------------------------------------------------------------------------

def _make_verify_output(
    workflow_id: str = "omex-verification-test",
    status: VerifyWorkflowStatus = VerifyWorkflowStatus.COMPLETED,
    owner_sub: str | None = "auth0|owner",
) -> VerifyWorkflowOutput:
    from biosim_server.biosim_verify import CompareSettings
    return VerifyWorkflowOutput(
        workflow_id=workflow_id,
        compare_settings=CompareSettings(
            user_description="t", include_outputs=False,
            rel_tol=1e-4, abs_tol_min=1e-3, abs_tol_scale=1e-5,
        ),
        workflow_status=status,
        timestamp="2025-01-01T00:00:00Z",
        owner_sub=owner_sub,
    )


def _temporal_with_describe(
    workflow_type: str = "OmexVerifyWorkflow",
    exec_status: object = None,
    query_result: VerifyWorkflowOutput | None = None,
    query_side_effect: Exception | None = None,
) -> MagicMock:
    from temporalio.client import WorkflowExecutionStatus
    if exec_status is None:
        exec_status = WorkflowExecutionStatus.COMPLETED
    desc = MagicMock()
    desc.workflow_type = workflow_type
    desc.status = exec_status
    handle = AsyncMock()
    handle.describe = AsyncMock(return_value=desc)
    if query_side_effect:
        handle.query = AsyncMock(side_effect=query_side_effect)
    else:
        handle.query = AsyncMock(return_value=query_result)
    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle
    return temporal


@pytest.mark.asyncio
async def test_get_verify_owner_completed_200() -> None:
    """Owner calls GET /verify on a COMPLETED workflow → 200, full body round-trips."""
    output = _make_verify_output(owner_sub="auth0|owner")
    temporal = _temporal_with_describe(query_result=output)
    user = AuthenticatedUser(sub="auth0|owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(f"/verify/{output.workflow_id}")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 200
    VerifyWorkflowOutput.model_validate(resp.json())


@pytest.mark.asyncio
async def test_get_verify_expired_id_ledger_row_404_with_distinct_detail() -> None:
    """NOT_FOUND + ledger row owned by caller → 404 'no longer retained'."""
    from temporalio.service import RPCError, RPCStatusCode
    from biosim_server.biosim_verify.models import VerificationRecord, VerificationType
    from datetime import datetime, timezone

    not_found_err = RPCError("nf", RPCStatusCode.NOT_FOUND, b"")
    handle = AsyncMock()
    handle.describe = AsyncMock(side_effect=not_found_err)
    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle

    row = VerificationLedgerRecord(
        workflow_id="omex-verification-expired",
        verify_type=VerificationType.OMEX,
        owner_sub="auth0|owner",
        created=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    ledger = AsyncMock()
    ledger.get_verification = AsyncMock(return_value=row)

    user = AuthenticatedUser(sub="auth0|owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/verify/omex-verification-expired")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 404
    assert "no longer retained" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_verify_expired_id_other_owners_row_generic_404() -> None:
    """NOT_FOUND + ledger row belonging to someone else → generic 404 (no existence leak)."""
    from temporalio.service import RPCError, RPCStatusCode
    from biosim_server.biosim_verify.models import VerificationLedgerRecord, VerificationType
    from datetime import datetime, timezone

    not_found_err = RPCError("nf", RPCStatusCode.NOT_FOUND, b"")
    handle = AsyncMock()
    handle.describe = AsyncMock(side_effect=not_found_err)
    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle

    row = VerificationLedgerRecord(
        workflow_id="omex-verification-other",
        verify_type=VerificationType.OMEX,
        owner_sub="auth0|other-person",
        created=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    ledger = AsyncMock()
    ledger.get_verification = AsyncMock(return_value=row)

    user = AuthenticatedUser(sub="auth0|stranger", email="stranger@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/verify/omex-verification-other")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 404
    assert "no longer retained" not in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_verify_non_verify_workflow_type_is_404() -> None:
    """describe returns a non-verification workflow_type (e.g. SimulationRunWorkflow) → 404."""
    output = _make_verify_output()
    temporal = _temporal_with_describe(workflow_type="SimulationRunWorkflow", query_result=output)
    user = AuthenticatedUser(sub="auth0|owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/verify/sim-run-fake-id")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_verify_failed_workflow_reconciled_to_failed_status() -> None:
    """Temporal says FAILED; workflow query returns IN_PROGRESS → response is FAILED."""
    from temporalio.client import WorkflowExecutionStatus
    output = _make_verify_output(status=VerifyWorkflowStatus.IN_PROGRESS, owner_sub="auth0|owner")
    temporal = _temporal_with_describe(
        exec_status=WorkflowExecutionStatus.FAILED, query_result=output
    )
    user = AuthenticatedUser(sub="auth0|owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/verify/omex-verification-test")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 200
    body = VerifyWorkflowOutput.model_validate(resp.json())
    assert body.workflow_status == VerifyWorkflowStatus.FAILED
    assert body.workflow_error is not None


@pytest.mark.asyncio
async def test_get_verify_temporal_none_is_503() -> None:
    """Temporal client None → 503, not 404."""
    user = AuthenticatedUser(sub="auth0|owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/verify/omex-verification-test")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_get_verify_temporal_unavailable_rpc_error_is_503() -> None:
    """Non-NOT_FOUND RPCError from describe → 503."""
    from temporalio.service import RPCError, RPCStatusCode
    unavail_err = RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
    handle = AsyncMock()
    handle.describe = AsyncMock(side_effect=unavail_err)
    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle

    user = AuthenticatedUser(sub="auth0|owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/verify/omex-verification-test")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /verification_ids — plan tests 10-16 (optional auth: ownerless plus self)
# ---------------------------------------------------------------------------

async def _get_verification_ids(ledger: object, params: dict[str, object] | None = None) -> Response:
    with patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.get("/verification_ids", params=params)  # type: ignore[arg-type]


def _ledger_returning(ids: list[str], next_cursor: VerificationCursor | None = None) -> AsyncMock:
    ledger = AsyncMock()
    ledger.list_verification_ids = AsyncMock(
        return_value=VerificationIdPage(verification_ids=ids, next_cursor=next_cursor)
    )
    return ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [["wf-b", "wf-a"], ["wf-only"], []])
async def test_list_verification_ids_anonymous_gets_the_first_bounded_page(ids: list[str]) -> None:
    """No token → 200 with the ownerless first page, in ledger order."""
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_optional_user, None)
    ledger = _ledger_returning(ids)
    resp = await _get_verification_ids(ledger)
    assert resp.status_code == 200
    assert resp.json() == {"verification_ids": ids, "records": [], "next_cursor": None}
    ledger.list_verification_ids.assert_awaited_once_with(
        None, limit=VERIFICATION_IDS_DEFAULT_PAGE_SIZE, after=None, omex_hash=None
    )


@pytest.mark.asyncio
async def test_list_verification_ids_returns_and_accepts_an_opaque_cursor() -> None:
    last = VerificationCursor(created=datetime(2025, 1, 2, 3, 4, 5), workflow_id="wf-b")
    ledger = _ledger_returning(["wf-a", "wf-b"], next_cursor=last)
    resp = await _get_verification_ids(ledger, {"limit": 2})
    assert resp.status_code == 200
    token = resp.json()["next_cursor"]
    assert token == encode_verification_cursor(last)

    ledger = _ledger_returning([])
    resp = await _get_verification_ids(ledger, {"limit": 2, "cursor": token})
    assert resp.status_code == 200
    ledger.list_verification_ids.assert_awaited_once_with(None, limit=2, after=last, omex_hash=None)


@pytest.mark.asyncio
async def test_list_verification_ids_with_omex_hash_filter() -> None:
    rec = VerificationRecord(
        omex_hash="hash123",
        run_ids=[VerificationRun(id="wf-1", created=datetime(2025, 1, 1, tzinfo=UTC), status="COMPLETED")],
    )
    ledger = AsyncMock()
    ledger.list_verification_ids = AsyncMock(
        return_value=VerificationIdPage(verification_ids=["wf-1"], records=[rec], next_cursor=None)
    )
    resp = await _get_verification_ids(ledger, {"omex_hash": "hash123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["verification_ids"] == ["wf-1"]
    assert len(data["records"]) == 1
    assert data["records"][0]["omex_hash"] == "hash123"
    assert data["records"][0]["run_ids"][0]["id"] == "wf-1"
    assert data["records"][0]["run_ids"][0]["status"] == "COMPLETED"
    ledger.list_verification_ids.assert_awaited_once_with(
        None, limit=VERIFICATION_IDS_DEFAULT_PAGE_SIZE, after=None, omex_hash="hash123"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,status",
    [(0, 422), (VERIFICATION_IDS_MAX_PAGE_SIZE + 1, 422), (VERIFICATION_IDS_MAX_PAGE_SIZE, 200), (1, 200)],
)
async def test_list_verification_ids_limit_bounds(limit: int, status: int) -> None:
    ledger = _ledger_returning([])
    resp = await _get_verification_ids(ledger, {"limit": limit})
    assert resp.status_code == status, resp.text
    if status == 422:
        ledger.list_verification_ids.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor,status",
    [
        ("!!!", 400),
        ("bm90IGpzb24", 400),  # "not json": no separator
        # 0001-01-01T00:00:00+01:00\nwf -- overflows on UTC normalisation (PR #120 review: was a 500)
        (base64.urlsafe_b64encode(b"0001-01-01T00:00:00+01:00\nwf").decode().rstrip("="), 400),
        ("x" * (VERIFICATION_CURSOR_MAX_LENGTH + 1), 422),
    ],
)
async def test_list_verification_ids_malformed_cursor_is_rejected_without_db_call(cursor: str, status: int) -> None:
    ledger = _ledger_returning([])
    resp = await _get_verification_ids(ledger, {"cursor": cursor})
    assert resp.status_code == status, resp.text
    if status == 400:
        assert resp.json() == {"detail": "Invalid cursor"}
    ledger.list_verification_ids.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", ["Bearer not-a-jwt", "Bearer "])
async def test_list_verification_ids_rejects_invalid_token(authorization: str) -> None:
    """Invalid credentials must never fall back to anonymous listing."""
    app.dependency_overrides.pop(get_optional_user, None)
    ledger = _ledger_returning(["wf-a"])
    with patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/verification_ids", headers={"Authorization": authorization})
    assert resp.status_code == 401
    ledger.list_verification_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_verification_ids_ledger_none_is_503() -> None:
    """Ledger service None → 503."""
    resp = await _get_verification_ids(None)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_list_verification_ids_ledger_raises_503(caplog: pytest.LogCaptureFixture) -> None:
    """Ledger raises → 503, detail does not contain raw exception text."""
    ledger = AsyncMock()
    ledger.list_verification_ids = AsyncMock(side_effect=RuntimeError("mongo exploded"))
    resp = await _get_verification_ids(ledger)
    assert resp.status_code == 503
    assert "mongo exploded" not in resp.json()["detail"]
    assert "mongo exploded" not in caplog.text
    assert "Failed to list verification IDs" in caplog.text


@pytest.mark.asyncio
async def test_list_verification_ids_follows_cursor_end_to_end(
    verification_database_service_mongo: VerificationDatabaseServiceMongo,
) -> None:
    """Real Mongo through the route: following next_cursor yields every id once, in order."""
    tie = datetime(2025, 9, 1, tzinfo=UTC)
    for workflow_id, created in [
        ("wf-e", datetime(2025, 9, 3, tzinfo=UTC)), ("wf-c", tie), ("wf-b", tie),
        ("wf-d", datetime(2025, 9, 2, tzinfo=UTC)), ("wf-a", datetime(2025, 8, 1, tzinfo=UTC)),
    ]:
        await verification_database_service_mongo.insert_verification(VerificationLedgerRecord(
            workflow_id=workflow_id, verify_type=VerificationType.RUNS, owner_sub=None, created=created,
        ))

    seen: list[str] = []
    params: dict[str, object] = {"limit": 2}
    while True:
        resp = await _get_verification_ids(verification_database_service_mongo, params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["verification_ids"]) <= 2
        seen.extend(body["verification_ids"])
        if body["next_cursor"] is None:
            break
        params = {"limit": 2, "cursor": body["next_cursor"]}
    assert seen == ["wf-e", "wf-d", "wf-b", "wf-c", "wf-a"]


# ---------------------------------------------------------------------------
# POST /verify/omex and /verify/runs — ledger behaviour (plan tests 27-30)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_omex_inserts_ledger_row_before_start_workflow() -> None:
    """POST /verify/omex: ledger.insert_verification called before start_workflow."""
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks()
    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock(return_value=None)

    call_order: list[str] = []

    async def _insert(record: object) -> None:
        call_order.append("insert")

    async def _start(*_a: object, id: str = "", **_kw: object) -> MagicMock:
        call_order.append("start")
        h = MagicMock()
        h.id = id
        h.run_id = "run-1"
        return h

    ledger.insert_verification = AsyncMock(side_effect=_insert)
    temporal.start_workflow = AsyncMock(side_effect=_start)

    user = AuthenticatedUser(sub="auth0|verify-owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
             patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
             patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
             patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/verify/omex",
                    files={"uploaded_file": ("m.omex", b"PK\x03\x04fake", "application/zip")},
                    params={"simulators": ["copasi"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)

    assert resp.status_code == 200
    assert call_order == ["insert", "start"]
    inserted = ledger.insert_verification.call_args[0][0]
    assert inserted.workflow_id == resp.json()["workflow_id"]
    assert inserted.owner_sub == "auth0|verify-owner"


@pytest.mark.asyncio
async def test_verify_omex_ledger_insert_raises_returns_503_no_workflow() -> None:
    """If ledger insert raises → 503, start_workflow never called."""
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks()
    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock(side_effect=RuntimeError("mongo"))

    user = AuthenticatedUser(sub="auth0|verify-owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
             patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
             patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
             patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as c:
                resp = await c.post(
                    "/verify/omex",
                    files={"uploaded_file": ("m.omex", b"PK\x03\x04fake", "application/zip")},
                    params={"simulators": ["copasi"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 503
    temporal.start_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_verify_omex_start_workflow_failure_deletes_ledger_row() -> None:
    """If start_workflow raises → 503 and delete_verification called for cleanup."""
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks()
    temporal.start_workflow = AsyncMock(side_effect=RPCError("temporal down", RPCStatusCode.INVALID_ARGUMENT, b""))

    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock(return_value=None)
    ledger.delete_verification = AsyncMock(return_value=None)

    user = AuthenticatedUser(sub="auth0|verify-owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
             patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
             patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
             patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as c:
                resp = await c.post(
                    "/verify/omex",
                    files={"uploaded_file": ("m.omex", b"PK\x03\x04fake", "application/zip")},
                    params={"simulators": ["copasi"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 503
    ledger.delete_verification.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_omex_ledger_none_returns_503() -> None:
    """Ledger service None → 503 before start_workflow."""
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks()
    user = AuthenticatedUser(sub="auth0|verify-owner", email="owner@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
             patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
             patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
             patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=None):
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as c:
                resp = await c.post(
                    "/verify/omex",
                    files={"uploaded_file": ("m.omex", b"PK\x03\x04fake", "application/zip")},
                    params={"simulators": ["copasi"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 503
    temporal.start_workflow.assert_not_called()


# ---------------------------------------------------------------------------
# POST /verify/runs — preflight (plan tests 39-42)
# ---------------------------------------------------------------------------

def _make_temporal_for_runs() -> MagicMock:
    temporal = MagicMock()

    async def _start(*_a: object, id: str = "", **_kw: object) -> MagicMock:
        h = MagicMock()
        h.id = id
        h.run_id = "run-r"
        return h

    temporal.start_workflow = AsyncMock(side_effect=_start)
    return temporal


def _make_hdf5_file(run_id: str, datasets: dict[str, list[str]]) -> "object":
    from biosim_server.biosim_runs.models import HDF5File, HDF5Group, HDF5Dataset, HDF5Attribute
    groups = []
    for name, labels in datasets.items():
        attr = HDF5Attribute(key="sedmlDataSetLabels", value=labels)
        ds = HDF5Dataset(name=name, shape=[len(labels), 10], attributes=[attr])
        groups.append(HDF5Group(name="g", attributes=[], datasets=[ds]))
    return HDF5File(filename="f.h5", id=run_id, uri=f"uri/{run_id}", groups=groups)


@pytest.mark.asyncio
async def test_verify_runs_disjoint_metadata_returns_400() -> None:
    """Preflight: two runs with no common datasets → 400, no start_workflow."""
    temporal = _make_temporal_for_runs()
    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock()
    ledger.delete_verification = AsyncMock()

    r1 = _make_hdf5_file("r1", {"ds/A": ["t", "x"]})
    r2 = _make_hdf5_file("r2", {"ds/B": ["t", "y"]})

    user = AuthenticatedUser(sub="auth0|u", email="u@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
             patch("biosim_server.api.main._load_hdf5_metadata_for_preflight",
                   new=AsyncMock(return_value={"r1": r1, "r2": r2})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/verify/runs",
                    params={"biosimulations_run_ids": ["r1", "r2"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 400
    assert "no datasets in common" in resp.json()["detail"]
    assert "r1" in resp.json()["detail"] and "r2" in resp.json()["detail"]
    temporal.start_workflow.assert_not_called()
    ledger.insert_verification.assert_not_called()


@pytest.mark.asyncio
async def test_verify_runs_overlapping_metadata_proceeds() -> None:
    """Preflight passes when runs share at least one dataset → 200."""
    temporal = _make_temporal_for_runs()
    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock(return_value=None)
    ledger.delete_verification = AsyncMock()

    r1 = _make_hdf5_file("r1", {"ds/shared": ["t", "x"]})
    r2 = _make_hdf5_file("r2", {"ds/shared": ["t", "x"]})

    user = AuthenticatedUser(sub="auth0|u", email="u@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
             patch("biosim_server.api.main._load_hdf5_metadata_for_preflight",
                   new=AsyncMock(return_value={"r1": r1, "r2": r2})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/verify/runs",
                    params={"biosimulations_run_ids": ["r1", "r2"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_verify_runs_preflight_skipped_when_only_one_metadata_available() -> None:
    """Preflight skipped when fewer than 2 runs have retrievable metadata → 200."""
    temporal = _make_temporal_for_runs()
    ledger = AsyncMock()
    ledger.insert_verification = AsyncMock(return_value=None)
    ledger.delete_verification = AsyncMock()

    # Only one run returned metadata; the other 404'd upstream
    r1 = _make_hdf5_file("r1", {"ds/A": ["t", "x"]})

    user = AuthenticatedUser(sub="auth0|u", email="u@example.com")
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: user
    try:
        with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
             patch("biosim_server.api.main._load_hdf5_metadata_for_preflight",
                   new=AsyncMock(return_value={"r1": r1})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/verify/runs",
                    params={"biosimulations_run_ids": ["r1", "r2"]},
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PR #120 B2 — per-request selection bounds (anonymous callers unless stated)
# ---------------------------------------------------------------------------

from biosim_server.biosim_verify.models import MAX_VERIFY_RUN_IDS, MAX_VERIFY_SIMULATORS  # noqa: E402


async def _post_verify_runs_anonymously(run_ids: list[str]) -> tuple[Response, MagicMock, AsyncMock, AsyncMock]:
    temporal, ledger, preflight = _make_temporal_for_runs(), AsyncMock(), AsyncMock(return_value={})
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
         patch("biosim_server.api.main._load_hdf5_metadata_for_preflight", new=preflight):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/verify/runs", params={"biosimulations_run_ids": run_ids})
    return response, temporal, ledger, preflight


def _assert_no_runs_work(temporal: MagicMock, ledger: AsyncMock, preflight: AsyncMock) -> None:
    preflight.assert_not_awaited()
    ledger.insert_verification.assert_not_awaited()
    temporal.start_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_verify_runs_anonymous_duplicate_flood_is_rejected_before_any_work() -> None:
    """Astra's reproduction: 100 copies of one run id for one quota unit."""
    response, temporal, ledger, preflight = await _post_verify_runs_anonymously(["r1"] * 100)
    assert response.status_code == 422, response.text
    _assert_no_runs_work(temporal, ledger, preflight)


@pytest.mark.asyncio
async def test_verify_runs_rejects_duplicate_run_ids() -> None:
    response, temporal, ledger, preflight = await _post_verify_runs_anonymously(["r1", "r2", "r1"])
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Duplicate run IDs are not allowed: r1"
    _assert_no_runs_work(temporal, ledger, preflight)


@pytest.mark.asyncio
async def test_verify_runs_rejects_more_than_max_run_ids() -> None:
    run_ids = [f"r{i}" for i in range(MAX_VERIFY_RUN_IDS + 1)]
    response, temporal, ledger, preflight = await _post_verify_runs_anonymously(run_ids)
    assert response.status_code == 422, response.text
    _assert_no_runs_work(temporal, ledger, preflight)


@pytest.mark.asyncio
async def test_verify_runs_workflow_input_is_the_bounded_submitted_list() -> None:
    run_ids = [f"r{i}" for i in reversed(range(MAX_VERIFY_RUN_IDS))]
    response, temporal, ledger, preflight = await _post_verify_runs_anonymously(run_ids)
    assert response.status_code == 200, response.text
    workflow_input = temporal.start_workflow.call_args.kwargs["args"][0]
    assert isinstance(workflow_input, RunsVerifyWorkflowInput)
    assert workflow_input.biosimulations_run_ids == run_ids  # same ids, same order
    ledger.insert_verification.assert_awaited_once()


def _second_simulator() -> BiosimulatorVersion:
    return BiosimulatorVersion(
        id="tellurium", name="tellurium", version="2.2.10",
        image_url="ghcr.io/biosimulators/tellurium:2.2.10", image_digest="sha256:def456",
        created="2024-01-01T00:00:00Z", updated="2024-01-01T00:00:00Z",
    )


async def _post_verify_omex_anonymously(
    simulators: list[str], *, extra_versions: list[BiosimulatorVersion] | None = None
) -> tuple[Response, MagicMock, MagicMock, MagicMock, MagicMock, AsyncMock]:
    file_service, omex_database, biosim_service, temporal = _verify_omex_mocks()
    if extra_versions:
        biosim_service.get_simulator_versions.return_value = [
            *biosim_service.get_simulator_versions.return_value, *extra_versions
        ]
    ledger = AsyncMock()
    with patch("biosim_server.api.main.get_file_service", return_value=file_service), \
         patch("biosim_server.api.main.get_omex_database_service", return_value=omex_database), \
         patch("biosim_server.api.main.get_biosim_service", return_value=biosim_service), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/verify/omex",
                files={"uploaded_file": ("model.omex", b"PK\x03\x04fake", "application/zip")},
                params={"simulators": simulators},
            )
    return response, file_service, omex_database, biosim_service, temporal, ledger


def _assert_no_omex_storage_or_workflow(
    file_service: MagicMock, omex_database: MagicMock, temporal: MagicMock, ledger: AsyncMock
) -> None:
    file_service.upload_bytes.assert_not_awaited()
    omex_database.insert_omex_file.assert_not_awaited()
    ledger.insert_verification.assert_not_awaited()
    temporal.start_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_verify_omex_rejects_more_than_max_simulators() -> None:
    simulators = [f"sim{i}" for i in range(MAX_VERIFY_SIMULATORS + 1)]
    response, files, omex, biosim, temporal, ledger = await _post_verify_omex_anonymously(simulators)
    assert response.status_code == 422, response.text
    biosim.get_simulator_versions.assert_not_awaited()
    _assert_no_omex_storage_or_workflow(files, omex, temporal, ledger)


@pytest.mark.asyncio
async def test_verify_omex_rejects_duplicate_simulator_strings() -> None:
    response, files, omex, biosim, temporal, ledger = await _post_verify_omex_anonymously(["copasi", "copasi"])
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Duplicate simulators are not allowed: copasi"
    biosim.get_simulator_versions.assert_not_awaited()
    _assert_no_omex_storage_or_workflow(files, omex, temporal, ledger)


@pytest.mark.asyncio
async def test_verify_omex_rejects_simulators_resolving_to_the_same_version() -> None:
    response, files, omex, _biosim, temporal, ledger = await _post_verify_omex_anonymously(
        ["copasi", "copasi:4.34.251"]
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == (
        "Simulators copasi and copasi:4.34.251 resolve to the same simulator version copasi:4.34.251."
    )
    _assert_no_omex_storage_or_workflow(files, omex, temporal, ledger)


@pytest.mark.asyncio
async def test_verify_omex_workflow_input_simulators_are_bounded_and_ordered() -> None:
    response, files, _omex, _biosim, temporal, ledger = await _post_verify_omex_anonymously(
        ["tellurium", "copasi"], extra_versions=[_second_simulator()]
    )
    assert response.status_code == 200, response.text
    workflow_input = temporal.start_workflow.call_args.kwargs["args"][0]
    assert isinstance(workflow_input, OmexVerifyWorkflowInput)
    assert [f"{sv.id}:{sv.version}" for sv in workflow_input.requested_simulators] == [
        "tellurium:2.2.10", "copasi:4.34.251",
    ]
    files.upload_bytes.assert_awaited_once()
    ledger.insert_verification.assert_awaited_once()
