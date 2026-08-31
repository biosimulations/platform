"""Integration test for POST /simulations/run against the local stack.

Uses testcontainers MongoDB + in-memory Temporal + FileServiceLocal +
BiosimServiceMock — no external network calls, no creds required.

The mock's default run_biosim_sim returns RUNNING and never transitions,
so the test patches it to return SUCCEEDED + pre-populate HDF5 metadata.
That keeps the SimulationRunWorkflow deterministic without waiting on
real polling.
"""

import asyncio
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from jose import jwt  # type: ignore[import-untyped]
from temporalio.worker import Worker

from biosim_server.biosim_omex import OmexDatabaseServiceMongo
from biosim_server.biosim_runs import (
    BiosimSimulationRun,
    BiosimSimulationRunStatus,
    BiosimulatorVersion,
    DatabaseServiceMongo,
    HDF5File,
)
from biosim_server.common.storage import FileServiceLocal
from biosim_server.simulations import SimulationRunDatabaseServiceMongo
from tests.fixtures.biosim_service_mock import BiosimServiceMock


def _patch_mock_to_succeed(mock: BiosimServiceMock) -> None:
    """Override run_biosim_sim so each submitted sim is immediately SUCCEEDED
    and a synthetic HDF5File is registered under the same id. The workflow's
    poll activity will then see SUCCEEDED on the first call and return."""

    async def succeed(
        local_omex_path: str, omex_name: str, simulator_version: BiosimulatorVersion
    ) -> BiosimSimulationRun:
        sim_id = "mock_" + uuid.uuid4().hex
        sim_run = BiosimSimulationRun(
            id=sim_id,
            name=omex_name,
            simulator_version=simulator_version,
            status=BiosimSimulationRunStatus.SUCCEEDED,
        )
        mock.sim_runs[sim_id] = sim_run
        mock.hdf5_files[sim_id] = HDF5File(
            filename="reports.h5",
            id=sim_id,
            uri=f"mock://{sim_id}/reports.h5",
            groups=[],
        )
        return sim_run

    mock.run_biosim_sim = succeed  # type: ignore[method-assign]


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_simulations_run(
    omex_test_file: Path,
    omex_database_service_mongo: OmexDatabaseServiceMongo,
    database_service_mongo: DatabaseServiceMongo,
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
    file_service_local: FileServiceLocal,
    biosim_service_mock: BiosimServiceMock,
    temporal_verify_worker: Worker,
    keycloak_async_client: AsyncClient,
    alice_token: str,
) -> None:
    """POST /simulations/run end-to-end: register an OMEX via /compatibility/check,
    kick off a tellurium run, poll until terminal, assert success shape.

    POST /simulations/runs requires auth (see router.list_simulation_runs), so
    those calls go through keycloak_async_client with a real Alice token; the
    submit and status calls stay anonymous since they accept optional auth and
    the test asserts the anonymous-submission email path.
    """
    _patch_mock_to_succeed(biosim_service_mock)
    # keycloak_async_client already verifies real JWTs against the Keycloak
    # container (see its fixture chain); aliased to test_client so the calls
    # below read the same as before this endpoint required auth.
    test_client = keycloak_async_client
    auth_headers = {"Authorization": f"Bearer {alice_token}"}

    # Register the OMEX file so /simulations/run can look it up by omex_id.
    with open(omex_test_file, "rb") as f:
        compat_resp = await test_client.post(
            "/compatibility/check",
            files={"uploaded_file": (omex_test_file.name, f, "application/zip")},
        )
    assert compat_resp.status_code == 200, compat_resp.text
    omex_id = compat_resp.json()["omex_id"]

    # Tellurium is in BiosimServiceMock.get_simulator_versions() — use it.
    run_payload = {
        "omex_id": omex_id,
        "name": "integration-local test run",
        "simulators": [{"id": "tellurium", "version": "2.2.10"}],
        "is_commercial": False,
        "email_address": "test@example.com",
        "newsletter_consent": False,
    }
    run_resp = await test_client.post("/simulations/run", json=run_payload)
    assert run_resp.status_code == 200, run_resp.text
    initial = run_resp.json()

    # Initial shape: one job, status "processing".
    assert "processing_id" in initial
    processing_id = initial["processing_id"]
    assert isinstance(processing_id, str) and processing_id.startswith("sim-run-")
    assert len(initial["jobs"]) == 1
    assert initial["jobs"][0]["simulator_id"] == "tellurium"
    assert initial["jobs"][0]["version"] == "2.2.10"
    assert initial["jobs"][0]["status"] == "processing"

    # Poll until terminal — workflow is fast since the mock returns SUCCEEDED
    # on the first poll, but it still needs to traverse submit -> poll -> save.
    terminal = {"success", "failure"}
    final = None
    for _ in range(60):  # up to ~30s
        status_resp = await test_client.get(f"/simulations/{processing_id}")
        assert status_resp.status_code == 200, status_resp.text
        body = status_resp.json()
        if all(job["status"] in terminal for job in body["jobs"]):
            final = body
            break
        await asyncio.sleep(0.5)

    assert final is not None, "simulation did not reach terminal state in time"
    assert final["processing_id"] == processing_id
    assert len(final["jobs"]) == 1
    job = final["jobs"][0]
    assert job["status"] == "success", f"expected success, got job={job}"
    assert job["simulator_id"] == "tellurium"
    assert job["version"] == "2.2.10"
    assert job["biosimulations_run_id"] is not None
    assert job["biosimulations_run_id"].startswith("mock_")
    assert job["error"] is None

    # The run should now be persisted and queryable via POST /simulations/runs,
    # with the workflow having updated its status from CREATED to SUCCEEDED.
    list_resp = await test_client.post(
        "/simulations/runs",
        json={"type": "all", "filters": [], "pagination": {"page": 1, "perPage": 20}},
        headers=auth_headers,
    )
    assert list_resp.status_code == 200, list_resp.text
    listing = list_resp.json()
    assert listing["pagination"]["_total"] == 1
    assert len(listing["runs"]) == 1
    run = listing["runs"][0]
    assert run["id"] == job["job_id"]
    assert run["name"] == "integration-local test run"
    assert run["simulator"] == "tellurium"
    assert run["simulatorVersion"] == "2.2.10"
    assert run["email"] == "test@example.com"
    assert run["status"] == "SUCCEEDED"

    # Owner-scoped query: router.list_simulation_runs overrides `user` with the
    # caller's own verified email for type "user" (a client-supplied `user`
    # would otherwise let any authenticated caller read anyone else's runs),
    # so this is scoped to Alice regardless of the `user` field below. Alice
    # never submitted a run (the run above was anonymous), so it's still 0.
    other = await test_client.post(
        "/simulations/runs",
        json={"type": "user", "user": "someone-else@example.com",
              "filters": [], "pagination": {"page": 1, "perPage": 20}},
        headers=auth_headers,
    )
    assert other.status_code == 200, other.text
    assert other.json()["pagination"]["_total"] == 0


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_simulations_run_authenticated_vs_anonymous_persistence(
    omex_test_file: Path,
    omex_database_service_mongo: OmexDatabaseServiceMongo,
    database_service_mongo: DatabaseServiceMongo,
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
    file_service_local: FileServiceLocal,
    biosim_service_mock: BiosimServiceMock,
    temporal_verify_worker: Worker,
    keycloak_async_client: AsyncClient,
    charlie_token: str,
    bob_token: str,
) -> None:
    _patch_mock_to_succeed(biosim_service_mock)
    charlie_headers = {"Authorization": f"Bearer {charlie_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}
    charlie_sub = jwt.get_unverified_claims(charlie_token)["sub"]
    with open(omex_test_file, "rb") as f:
        omex_id = (
            await keycloak_async_client.post(
                "/compatibility/check",
                files={"uploaded_file": (omex_test_file.name, f, "application/zip")},
            )
        ).json()["omex_id"]
    payload = {
        "omex_id": omex_id,
        "name": "anon-run",
        "simulators": [{"id": "tellurium", "version": "2.2.10"}],
        "email_address": "anon@example.com",
    }
    anon = await keycloak_async_client.post("/simulations/run", json=payload)
    assert anon.status_code == 200
    anon_id = anon.json()["processing_id"]
    anon_rows = await simulation_run_database_service_mongo.get_simulation_runs_by_processing_id(anon_id)
    assert anon_rows[0].owner_sub is None
    assert anon_rows[0].visibility == "public"
    payload["name"] = "charlie-run"
    auth = await keycloak_async_client.post(
        "/simulations/run", json=payload, headers=charlie_headers
    )
    assert auth.status_code == 200
    auth_id = auth.json()["processing_id"]
    auth_rows = await simulation_run_database_service_mongo.get_simulation_runs_by_processing_id(auth_id)
    assert auth_rows[0].owner_sub == charlie_sub
    assert auth_rows[0].visibility == "private"
    bob_list = await keycloak_async_client.post(
        "/simulations/runs",
        json={"type": "all", "filters": [], "pagination": {"page": 1, "perPage": 20}},
        headers=bob_headers,
    )
    ids = {r["name"] for r in bob_list.json()["runs"]}
    assert "anon-run" in ids
    assert "charlie-run" not in ids
    charlie_mine = await keycloak_async_client.post(
        "/simulations/runs",
        json={"type": "user", "filters": [], "pagination": {"page": 1, "perPage": 20}},
        headers=charlie_headers,
    )
    names = {r["name"] for r in charlie_mine.json()["runs"]}
    assert "charlie-run" in names


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_private_omex_cannot_be_executed_by_another_user(
    omex_test_file: Path,
    omex_database_service_mongo: OmexDatabaseServiceMongo,
    database_service_mongo: DatabaseServiceMongo,
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
    file_service_local: FileServiceLocal,
    biosim_service_mock: BiosimServiceMock,
    temporal_verify_worker: Worker,
    keycloak_async_client: AsyncClient,
    charlie_token: str,
    bob_token: str,
) -> None:
    """End to end: an OMEX hash is not an access capability.

    Charlie ingests an archive while authenticated (private, owned by him). Bob
    knows the hash -- it is returned in the compatibility response -- but must
    not be able to run it, and an anonymous caller must not either.
    """
    _patch_mock_to_succeed(biosim_service_mock)
    charlie_headers = {"Authorization": f"Bearer {charlie_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}
    charlie_sub = jwt.get_unverified_claims(charlie_token)["sub"]

    with open(omex_test_file, "rb") as f:
        compat = await keycloak_async_client.post(
            "/compatibility/check",
            files={"uploaded_file": (omex_test_file.name, f, "application/zip")},
            headers=charlie_headers,
        )
    assert compat.status_code == 200, compat.text
    omex_id = compat.json()["omex_id"]

    stored = await omex_database_service_mongo.get_omex_file(
        file_hash_md5=omex_id, owner_sub=charlie_sub
    )
    assert stored is not None
    assert stored.owner_sub == charlie_sub
    assert stored.visibility == "private"

    payload = {
        "omex_id": omex_id,
        "name": "private-omex-run",
        "simulators": [{"id": "tellurium", "version": "2.2.10"}],
    }

    # Bob knows the hash. That is not enough.
    denied = await keycloak_async_client.post(
        "/simulations/run", json=payload, headers=bob_headers
    )
    assert denied.status_code == 404, denied.text

    # Neither is anonymity.
    denied_anon = await keycloak_async_client.post("/simulations/run", json=payload)
    assert denied_anon.status_code == 404, denied_anon.text

    # The owner can.
    allowed = await keycloak_async_client.post(
        "/simulations/run", json=payload, headers=charlie_headers
    )
    assert allowed.status_code == 200, allowed.text
    rows = await simulation_run_database_service_mongo.get_simulation_runs_by_processing_id(
        allowed.json()["processing_id"]
    )
    assert rows[0].owner_sub == charlie_sub
    assert rows[0].visibility == "private"
    assert rows[0].omex_id == omex_id


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_publish_then_unpublish_a_private_run(
    omex_test_file: Path,
    omex_database_service_mongo: OmexDatabaseServiceMongo,
    database_service_mongo: DatabaseServiceMongo,
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
    file_service_local: FileServiceLocal,
    biosim_service_mock: BiosimServiceMock,
    temporal_verify_worker: Worker,
    keycloak_async_client: AsyncClient,
    charlie_token: str,
    bob_token: str,
) -> None:
    """Owner-only publicity control, with run/OMEX consistency across the flip."""
    _patch_mock_to_succeed(biosim_service_mock)
    charlie_headers = {"Authorization": f"Bearer {charlie_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}
    charlie_sub = jwt.get_unverified_claims(charlie_token)["sub"]

    with open(omex_test_file, "rb") as f:
        omex_id = (
            await keycloak_async_client.post(
                "/compatibility/check",
                files={"uploaded_file": (omex_test_file.name, f, "application/zip")},
                headers=charlie_headers,
            )
        ).json()["omex_id"]

    created = await keycloak_async_client.post(
        "/simulations/run",
        json={
            "omex_id": omex_id,
            "name": "publishable-run",
            "simulators": [{"id": "tellurium", "version": "2.2.10"}],
        },
        headers=charlie_headers,
    )
    assert created.status_code == 200, created.text
    processing_id = created.json()["processing_id"]

    # Private: Bob sees a 404, not a 403 -- the id itself is concealed.
    assert (await keycloak_async_client.get(
        f"/simulations/{processing_id}", headers=bob_headers)).status_code == 404

    # Bob cannot publish somebody else's run.
    forbidden = await keycloak_async_client.patch(
        f"/simulations/{processing_id}/visibility",
        json={"visibility": "public"},
        headers=bob_headers,
    )
    assert forbidden.status_code == 403

    published = await keycloak_async_client.patch(
        f"/simulations/{processing_id}/visibility",
        json={"visibility": "public"},
        headers=charlie_headers,
    )
    assert published.status_code == 200, published.text
    assert published.json()["visibility"] == "public"

    # The archive was published in the same action, so the public run is runnable.
    omex_after = await omex_database_service_mongo.get_omex_file(
        file_hash_md5=omex_id, owner_sub=charlie_sub
    )
    assert omex_after is not None and omex_after.visibility == "public"

    # Anonymous access now succeeds.
    assert (await keycloak_async_client.get(
        f"/simulations/{processing_id}")).status_code == 200

    # ... and stops again on unpublish.
    unpublished = await keycloak_async_client.patch(
        f"/simulations/{processing_id}/visibility",
        json={"visibility": "private"},
        headers=charlie_headers,
    )
    assert unpublished.status_code == 200
    assert (await keycloak_async_client.get(
        f"/simulations/{processing_id}")).status_code == 404
