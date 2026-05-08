import pytest

from biosim_server.biosim_runs import BiosimulatorVersion, BiosimulatorWorkflowRun, DatabaseServiceMongo
from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_verify.models import VerifyWorkflowOutput


@pytest.mark.asyncio
async def test_biosimulator_workflow_run(database_service_mongo: DatabaseServiceMongo,
                                        omex_verify_workflow_output: VerifyWorkflowOutput) -> None:
    workflow_id = "workflow_id"
    cache_buster = "1234"
    omex_file = OmexFile(file_hash_md5="1234", bucket_name="test_bucket",
                         uploaded_filename="BIOMD0000000010_tellurium_Negative_feedback_and_ultrasen.omex",
                         omex_gcs_path="path/to/omex", file_size=100000)
    simulator_version = BiosimulatorVersion(
        id="tellurium", name="tellurium", version="2.2.10", image_url="ghcr.io/biosimulators/tellurium:2.2.10",
        image_digest="0c22827b4682273810d48ea606ef50c7163e5f5289740951c00c64c669409eae",
        created="2024-10-10T22:00:50.110Z", updated="2024-10-10T22:00:50.110Z")
    assert omex_verify_workflow_output.workflow_results is not None
    biosim_run = omex_verify_workflow_output.workflow_results.sims_run_info[0].biosim_sim_run
    hdf5_file = omex_verify_workflow_output.workflow_results.sims_run_info[0].hdf5_file
    sim_output = BiosimulatorWorkflowRun(workflow_id=workflow_id, file_hash_md5=omex_file.file_hash_md5,
                                         image_digest=simulator_version.image_digest, cache_buster=cache_buster,
                                         omex_file=omex_file, simulator_version=simulator_version,
                                         biosim_run=biosim_run, hdf5_file=hdf5_file)

    inserted_sim_output1 = await database_service_mongo.insert_biosimulator_workflow_run(sim_workflow_run=sim_output)
    assert inserted_sim_output1.database_id is not None
    sim_outputs: list[BiosimulatorWorkflowRun] = await database_service_mongo.get_biosimulator_workflow_runs(
                                                             file_hash_md5=omex_file.file_hash_md5,
                                                             image_digest=simulator_version.image_digest,
                                                             cache_buster=cache_buster)
    assert sim_outputs == [inserted_sim_output1]

    inserted_sim_output2 = await database_service_mongo.insert_biosimulator_workflow_run(sim_workflow_run=sim_output)
    assert inserted_sim_output2.database_id is not None
    sim_outputs = await database_service_mongo.get_biosimulator_workflow_runs(file_hash_md5=omex_file.file_hash_md5,
                                                                              image_digest=simulator_version.image_digest,
                                                                              cache_buster=cache_buster)
    assert sim_outputs == [inserted_sim_output1, inserted_sim_output2]

    await database_service_mongo.delete_biosimulator_workflow_run(database_id=inserted_sim_output1.database_id)
    await database_service_mongo.delete_biosimulator_workflow_run(database_id=inserted_sim_output2.database_id)

    with pytest.raises(Exception):
        await database_service_mongo.delete_biosimulator_workflow_run(database_id=inserted_sim_output1.database_id)


