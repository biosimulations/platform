import uuid
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from biosim_server.biosim_omex import get_cached_omex_file_from_local, OmexDatabaseServiceMongo
from biosim_server.biosim_runs import BiosimServiceRest, BiosimulatorVersion, DatabaseServiceMongo, OmexSimWorkflow, \
    OmexSimWorkflowInput, OmexSimWorkflowOutput, \
    OmexSimWorkflowStatus
from biosim_server.common.storage import FileServiceGCS
from biosim_server.config import get_settings


@pytest.mark.skipif(len(get_settings().storage_gcs_credentials_file) == 0,
                    reason="gcs_credentials.json file not supplied")
@pytest.mark.asyncio
async def test_sim_workflow(temporal_client: Client,
                            temporal_verify_worker: Worker,
                            omex_test_file: Path,
                            biosim_service_rest: BiosimServiceRest,
                            file_service_gcs: FileServiceGCS,
                            omex_database_service_mongo: OmexDatabaseServiceMongo,
                            database_service_mongo: DatabaseServiceMongo) -> None:
    assert biosim_service_rest is not None

    omex_file = await get_cached_omex_file_from_local(file_service=file_service_gcs, omex_database=omex_database_service_mongo, omex_file=omex_test_file, filename=omex_test_file.name)

    simulator_versions = await biosim_service_rest.get_simulator_versions()
    simulator_version: BiosimulatorVersion | None = None
    for sim in simulator_versions:
        if sim.id == 'copasi':
            simulator_version = sim
    assert simulator_version is not None

    sim_workflow_input = OmexSimWorkflowInput(omex_file=omex_file, simulator_version=simulator_version, cache_buster="0")
    workflow_result: OmexSimWorkflowOutput = await temporal_client.execute_workflow(OmexSimWorkflow.run, args=[sim_workflow_input],
        id=uuid.uuid4().hex, task_queue="verification_tasks", retry_policy=RetryPolicy(maximum_attempts=1))
    assert workflow_result.error_message is None
    assert workflow_result.workflow_status == OmexSimWorkflowStatus.COMPLETED
    assert workflow_result.workflow_id is not None
    assert workflow_result.biosimulator_workflow_run is not None
