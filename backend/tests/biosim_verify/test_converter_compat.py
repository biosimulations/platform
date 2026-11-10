"""0.10.3 payloads, written with the SDK default converter, decode unchanged under pydantic_data_converter.

The API and worker moved from ``DataConverter.default`` to ``pydantic_data_converter``.
Backfill reads 0.10.3 start inputs and results, and an in-flight 0.10.3 workflow
replays its activity results, so every verification payload type must decode to
the same value it was encoded from.
"""
import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.converter import DataConverter
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from biosim_server.biosim_omex.models import OmexFile
from biosim_server.biosim_runs.activities import (
    GetExistingBiosimSimulationRunActivityInput,
    GetExistingBiosimSimulationRunActivityOutput,
    PollBiosimRunActivityInput,
    SubmitBiosimRunActivityInput,
    SubmitBiosimRunActivityOutput,
)
from biosim_server.biosim_runs.models import (
    BiosimSimulationRun,
    BiosimSimulationRunStatus,
    BiosimulatorVersion,
    BiosimulatorWorkflowRun,
    HDF5Attribute,
    HDF5Dataset,
    HDF5File,
    HDF5Group,
    Hdf5DataValues,
)
from biosim_server.biosim_runs.workflows import OmexSimWorkflowInput, OmexSimWorkflowOutput, OmexSimWorkflowStatus
from biosim_server.biosim_verify.activities import GenerateStatisticsActivityInput, generate_statistics_activity
from biosim_server.biosim_verify.history import read_start_input
from biosim_server.biosim_verify.models import (
    CompareSettings,
    ComparisonStatistics,
    GenerateStatisticsActivityOutput,
    RunData,
    SimulationRunInfo,
    SimulatorFailure,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflowInput
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow, RunsVerifyWorkflowInput
from biosim_server.common.temporal import pydantic_data_converter
from tests.biosim_verify.test_persistence_failures import World, succeeded_run, world  # noqa: F401
from tests.fixtures.retention_history.legacy_runs import RunsVerifyWorkflow as LegacyRuns
from tests.fixtures.retention_history.legacy_runs import RunsVerifyWorkflowInput as LegacyRunsInput

pytestmark = [
    pytest.mark.filterwarnings("error::UserWarning"),
    # The default converter itself warns that it is not the Pydantic v2 converter; that is the point here.
    pytest.mark.filterwarnings("ignore:If you're using Pydantic v2:UserWarning"),
]

SETTINGS = CompareSettings(user_description="converter", include_outputs=True, rel_tol=1e-4,
                           abs_tol_min=1e-3, abs_tol_scale=1e-5, observables=["x"])
SIMULATOR = BiosimulatorVersion(id="copasi", name="copasi", version="4.45", image_url="ghcr.io/copasi",
                                image_digest="sha256:abc", created="2025-01-10T19:51:11.934Z",
                                updated="2025-01-10T19:51:41.807Z")
OMEX = OmexFile(file_hash_md5="hash", uploaded_filename="model.omex", bucket_name="bucket",
                omex_gcs_path="omex/model.omex", file_size=1024, database_id="db", owner="auth0|owner",
                visibility="private")
HDF5 = HDF5File(filename="reports.h5", id="run1", uri="uri/run1", groups=[HDF5Group(
    name="group", attributes=[HDF5Attribute(key="flag", value=True), HDF5Attribute(key="count", value=3)],
    datasets=[HDF5Dataset(name="ds", shape=[2, 3], attributes=[
        HDF5Attribute(key="sedmlDataSetLabels", value=["t", "x"]),
        HDF5Attribute(key="scale", value=1.5),
        HDF5Attribute(key="weights", value=[0.5, 1.0]),
        HDF5Attribute(key="units", value="s"),
    ])],
)])
RUN = BiosimSimulationRun(id="run1", name="run", simulator_version=SIMULATOR, status=BiosimSimulationRunStatus.SUCCEEDED,
                          cpus=1, memory=8, max_time=20, env_vars=["OMP_NUM_THREADS=1"], purpose="academic",
                          submitted="2025-01-10T19:51:11.934Z", runtime=1200)
WORKFLOW_RUN = BiosimulatorWorkflowRun(workflow_id="sim-wf", file_hash_md5="hash", image_digest="sha256:abc",
                                       cache_buster="0", omex_file=OMEX, simulator_version=SIMULATOR,
                                       biosim_run=RUN, hdf5_file=HDF5, database_id="db")
STATISTICS = GenerateStatisticsActivityOutput(
    sims_run_info=[SimulationRunInfo(biosim_sim_run=RUN, hdf5_file=HDF5)],
    comparison_statistics={"ds": [[ComparisonStatistics(
        dataset_name="ds", simulator_version_i="copasi:4.45", simulator_version_j="tellurium:2.2",
        var_names=["x"], score=[0.0, float("nan"), float("inf"), float("-inf")],
        is_close=[True, False, False, False], error_message=None,
    )]]},
    sim_run_data=[RunData(run_id="run1", dataset_name="ds", var_names=["t", "x"],
                          data=Hdf5DataValues(shape=[2, 3], values=[0.0, 1.5, float("nan"), 3.0, 4.0, 5.0]))],
)

# Every verification workflow input, output, child output, and activity argument and result
# that a 0.10.3 history can hold, populated with every field 0.10.3 could write.
SAMPLES: list[BaseModel] = [
    RunsVerifyWorkflowInput(biosimulations_run_ids=["run1", "run2"], compare_settings=SETTINGS, owner_sub="auth0|owner"),
    OmexVerifyWorkflowInput(omex_file=OMEX, requested_simulators=[SIMULATOR], cache_buster="1",
                            compare_settings=SETTINGS, owner_sub=None),
    VerifyWorkflowOutput(workflow_id="wf", compare_settings=SETTINGS, workflow_status=VerifyWorkflowStatus.COMPLETED,
                         timestamp="2025-01-10 19:51:11.934000+00:00", workflow_run_id="run-id",
                         failed_simulators=[SimulatorFailure(simulator_version="vcell:7", error_message="failed")],
                         workflow_results=STATISTICS, owner_sub="auth0|owner"),
    VerifyWorkflowOutput(workflow_id="wf", compare_settings=SETTINGS, workflow_status=VerifyWorkflowStatus.RUN_ID_NOT_FOUND,
                         timestamp="2025-01-10 19:51:11.934000+00:00", workflow_error="Simulation run with id x not found."),
    OmexSimWorkflowInput(omex_file=OMEX, simulator_version=SIMULATOR, cache_buster="0"),
    OmexSimWorkflowOutput(workflow_id="sim-wf", workflow_status=OmexSimWorkflowStatus.COMPLETED,
                          biosimulations_run_id="run1", biosimulator_workflow_run=WORKFLOW_RUN),
    OmexSimWorkflowOutput(workflow_id="sim-wf", workflow_status=OmexSimWorkflowStatus.FAILED, error_message="boom"),
    GetExistingBiosimSimulationRunActivityInput(workflow_id="wf", biosim_run_id="run1", abort_on_not_found=True),
    GetExistingBiosimSimulationRunActivityOutput(status=BiosimSimulationRunStatus.SUCCEEDED, biosim_workflow_run=WORKFLOW_RUN),
    GetExistingBiosimSimulationRunActivityOutput(status=BiosimSimulationRunStatus.RUN_ID_NOT_FOUND, error_message="gone"),
    SubmitBiosimRunActivityInput(workflow_id="wf", omex_file=OMEX, simulator_version=SIMULATOR, cache_buster="0"),
    SubmitBiosimRunActivityOutput(biosimulations_run_id="run1", cached_workflow_run=WORKFLOW_RUN),
    PollBiosimRunActivityInput(workflow_id="wf", omex_file=OMEX, simulator_version=SIMULATOR, cache_buster="0",
                               biosimulations_run_id="run1"),
    WORKFLOW_RUN,
    GenerateStatisticsActivityInput(sim_run_info_list=[SimulationRunInfo(biosim_sim_run=RUN, hdf5_file=HDF5)],
                                    compare_settings=SETTINGS),
    STATISTICS,
]


def _text(value: BaseModel) -> str:
    """A NaN-aware canonical rendering: NaN, +inf and -inf stay distinct from each other and from null."""
    return json.dumps(value.model_dump(), sort_keys=True, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", SAMPLES, ids=lambda value: type(value).__name__)
async def test_default_encoded_payload_decodes_to_the_same_value(value: BaseModel) -> None:
    payloads = await DataConverter.default.encode([value])
    [decoded] = await pydantic_data_converter.decode(payloads, [type(value)])
    assert type(decoded) is type(value)
    assert _text(decoded) == _text(value)


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_a_legacy_history_written_with_the_default_converter_is_readable(world: World) -> None:  # noqa: F811
    """Backfill and replay read a 0.10.3 history through the new converter."""
    legacy_client = Client(world.client.service_client, namespace=world.client.namespace,
                           data_converter=DataConverter.default)
    queue, workflow_id = "legacy-" + uuid4().hex, "legacy-" + uuid4().hex
    original = LegacyRunsInput(biosimulations_run_ids=["run1"], owner_sub="auth0|owner",
                               compare_settings=SETTINGS.model_copy(update={"observables": None}))
    async with Worker(legacy_client, task_queue=queue, workflows=[LegacyRuns],
                      activities=[succeeded_run, generate_statistics_activity],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        legacy = await legacy_client.start_workflow(LegacyRuns.run, original, id=workflow_id, task_queue=queue)
        written = await asyncio.wait_for(legacy.result(), 30)
    assert written.workflow_results is not None  # the result carries statistics, as 0.10.3 results did

    handle = world.client.get_workflow_handle(workflow_id, result_type=VerifyWorkflowOutput)
    start = await read_start_input(world.client, handle, "RunsVerifyWorkflow")
    assert start == RunsVerifyWorkflowInput.model_validate(original.model_dump())
    result = await handle.result()
    assert _text(result) == _text(written)
    await Replayer(workflows=[RunsVerifyWorkflow], data_converter=pydantic_data_converter,
                   workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(await handle.fetch_history())


def test_every_sample_is_a_distinct_payload_type_or_variant() -> None:
    """Guard the sample list itself: each 0.10.3 payload type appears at least once."""
    expected: set[type[Any]] = {
        RunsVerifyWorkflowInput, OmexVerifyWorkflowInput, VerifyWorkflowOutput, OmexSimWorkflowInput,
        OmexSimWorkflowOutput, GetExistingBiosimSimulationRunActivityInput, GetExistingBiosimSimulationRunActivityOutput,
        SubmitBiosimRunActivityInput, SubmitBiosimRunActivityOutput, PollBiosimRunActivityInput,
        BiosimulatorWorkflowRun, GenerateStatisticsActivityInput, GenerateStatisticsActivityOutput,
    }
    assert {type(value) for value in SAMPLES} == expected
