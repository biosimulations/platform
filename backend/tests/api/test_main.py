import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from biosim_server.api.main import app
from biosim_server.biosim_omex import OmexDatabaseServiceMongo
from biosim_server.biosim_runs import BiosimServiceRest, DatabaseServiceMongo
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflowInput
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput
from biosim_server.biosim_verify.models import VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.common.auth import AuthenticatedUser, get_current_user
from biosim_server.common.storage import FileServiceGCS
from biosim_server.config import get_settings
from biosim_server.version import __version__
from httpx import ASGITransport, AsyncClient
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


@pytest.mark.asyncio
async def test_get_output_not_found(omex_verify_workflow_input: OmexVerifyWorkflowInput,
                                    omex_verify_workflow_output: VerifyWorkflowOutput) -> None:

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        # test with non-existent verification_id
        response = await test_client.get("/verify_omex/non-existent-id")
        assert response.status_code == 404


@pytest.mark.integration
@pytest.mark.skipif(len(get_settings().storage_gcs_credentials_file) == 0,
                    reason="gcs_credentials.json file not supplied")
@pytest.mark.usefixtures("authenticated_user")
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
@pytest.mark.usefixtures("authenticated_user")
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
@pytest.mark.usefixtures("authenticated_user")
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
async def test_verify_omex_requires_authentication() -> None:
    """POST /verify/omex with no bearer token is rejected 401 before the handler runs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.post(
            "/verify/omex",
            files={"uploaded_file": ("empty.omex", b"", "application/zip")},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_verify_runs_requires_authentication() -> None:
    """POST /verify/runs with no bearer token is rejected 401 before the handler runs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        response = await test_client.post("/verify/runs")
        assert response.status_code == 401


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
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            response = await test_client.get("/api/v1/demo/private/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

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
    user = AuthenticatedUser(sub="auth0|test-user-id", email="user@example.com", roles=roles)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


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