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
from httpx import ASGITransport, AsyncClient
from temporalio.worker import Worker

from biosim_server.api.main import app
from biosim_server.biosim_omex import OmexDatabaseServiceMongo
from biosim_server.biosim_runs import (
    BiosimSimulationRun,
    BiosimSimulationRunStatus,
    BiosimulatorVersion,
    DatabaseServiceMongo,
    HDF5File,
)
from biosim_server.common.storage import FileServiceLocal
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
    file_service_local: FileServiceLocal,
    biosim_service_mock: BiosimServiceMock,
    temporal_verify_worker: Worker,
) -> None:
    """POST /simulations/run end-to-end: register an OMEX via /compatibility/check,
    kick off a tellurium run, poll until terminal, assert success shape."""
    _patch_mock_to_succeed(biosim_service_mock)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
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
