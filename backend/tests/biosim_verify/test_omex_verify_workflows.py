import logging
import uuid
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from biosim_server.biosim_omex import get_cached_omex_file_from_local, OmexDatabaseService
from biosim_server.biosim_runs import BiosimServiceRest, DatabaseService
from biosim_server.biosim_verify import ComparisonStatistics
from biosim_server.biosim_verify.models import VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflow, OmexVerifyWorkflowInput
from biosim_server.common.storage import FileServiceGCS
from biosim_server.config import get_settings
from tests.fixtures.database_fixtures import omex_database_service_mongo
from tests.fixtures.gcs_fixtures import file_service_gcs_test_base_path


@pytest.mark.skipif(len(get_settings().storage_gcs_credentials_file) == 0,
                    reason="gcs_credentials.json file not supplied")
@pytest.mark.asyncio
async def test_omex_verify_workflow_GCS(temporal_client: Client, temporal_verify_worker: Worker,
                                       omex_verify_workflow_input: OmexVerifyWorkflowInput,
                                       omex_verify_workflow_output: VerifyWorkflowOutput,
                                       biosim_service_rest: BiosimServiceRest,
                                       file_service_gcs: FileServiceGCS,
                                       file_service_gcs_test_base_path: Path,
                                       omex_test_file: Path,
                                       database_service_mongo: DatabaseService,
                                       omex_database_service_mongo: OmexDatabaseService,
                                       omex_verify_workflow_output_file: Path) -> None:
    assert biosim_service_rest is not None

    omex_file = await get_cached_omex_file_from_local(file_service=file_service_gcs, omex_database=omex_database_service_mongo, omex_file=omex_test_file, filename=omex_test_file.name)

    await file_service_gcs.upload_file(file_path=omex_test_file, gcs_path=omex_file.omex_gcs_path)
    workflow_id = uuid.uuid4().hex
    logging.info(f"Stored test omex file at {omex_file.omex_gcs_path}")
    omex_verify_workflow_input.omex_file = omex_file

    observed_results: VerifyWorkflowOutput = await temporal_client.execute_workflow(
        OmexVerifyWorkflow.run, args=[omex_verify_workflow_input],
        # result_type=VerifyWorkflowOutput,
        id=workflow_id, task_queue="verification_tasks", retry_policy=RetryPolicy(maximum_attempts=1))

    # uncomment to refresh the expected results
    # with open(omex_verify_workflow_output_file, "w") as f:
    #     f.write(observed_results.model_dump_json(indent=2))

    assert_omex_verify_results(observed_results=observed_results, expected_results_template=omex_verify_workflow_output)


def assert_omex_verify_results(observed_results: VerifyWorkflowOutput,
                               expected_results_template: VerifyWorkflowOutput) -> None:

    assert observed_results is not None
    assert observed_results.workflow_error is None
    assert observed_results.workflow_status == VerifyWorkflowStatus.COMPLETED
    # customize expected results to match those things which vary between runs
    expected_results = expected_results_template.model_copy(deep=True)
    expected_results.workflow_id = observed_results.workflow_id
    expected_results.workflow_run_id = observed_results.workflow_run_id
    expected_results.timestamp = observed_results.timestamp
    if expected_results.workflow_results and observed_results.workflow_results:
        for i in range(len(expected_results.workflow_results.sims_run_info)):
            expected_biosim_sim_run = expected_results.workflow_results.sims_run_info[i].biosim_sim_run
            expected_hdf5_file = expected_results.workflow_results.sims_run_info[i].hdf5_file
            result_biosim_sim_run = observed_results.workflow_results.sims_run_info[i].biosim_sim_run
            result_hdf5_file = observed_results.workflow_results.sims_run_info[i].hdf5_file

            expected_hdf5_file.uri = result_hdf5_file.uri
            expected_hdf5_file.id = result_hdf5_file.id
            expected_biosim_sim_run.id = result_biosim_sim_run.id
            expected_biosim_sim_run.simulator_version = result_biosim_sim_run.simulator_version

    # compare the comparison statistics separately, seems not to be 100% deterministic
    assert expected_results.workflow_results is not None
    assert observed_results.workflow_results is not None
    ds_names = list(expected_results.workflow_results.comparison_statistics.keys())
    num_simulators = len(expected_results.workflow_results.sims_run_info)
    for ds_name in ds_names:
        for i in range(num_simulators):
            for j in range(num_simulators):
                assert observed_results.workflow_results is not None
                observed_compare_i_j: ComparisonStatistics = \
                observed_results.workflow_results.comparison_statistics[ds_name][i][j]
                expected_compare_i_j: ComparisonStatistics = \
                expected_results.workflow_results.comparison_statistics[ds_name][i][j]
                # assert observed_compare_i_j.mse == expected_compare_i_j.mse
                assert observed_compare_i_j.is_close == expected_compare_i_j.is_close
                assert observed_compare_i_j.error_message == expected_compare_i_j.error_message
                assert observed_compare_i_j.simulator_version_i == expected_compare_i_j.simulator_version_i
                assert observed_compare_i_j.simulator_version_j == expected_compare_i_j.simulator_version_j
    expected_results.workflow_results.comparison_statistics = observed_results.workflow_results.comparison_statistics

    # compare everything else which has not been hardwired to match
    assert observed_results == expected_results
