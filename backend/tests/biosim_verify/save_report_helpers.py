"""Shared fixtures for the report-saving workflow tests.

Both the workflow tests and the replay tests need the same stubbed activities and
the same sample run, so they live here rather than being copied into each module.
"""

from typing import Any

from temporalio import activity

from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_runs import BiosimSimulationRun, BiosimSimulationRunStatus, \
    BiosimulatorVersion, BiosimulatorWorkflowRun
from biosim_server.biosim_runs.activities import GetExistingBiosimSimulationRunActivityInput, \
    GetExistingBiosimSimulationRunActivityOutput
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.activities import GenerateStatisticsActivityInput
from biosim_server.biosim_verify.models import GenerateStatisticsActivityOutput
from tests.biosim_verify.test_compatibility import _file


def compare_settings() -> CompareSettings:
    """The comparison settings every test in these modules submits."""
    return CompareSettings(user_description="", include_outputs=False,
                           rel_tol=1e-6, abs_tol_min=1e-9, abs_tol_scale=1.0)


def workflow_run(run_id: str, simulator_version: BiosimulatorVersion) -> BiosimulatorWorkflowRun:
    """One succeeded biosimulations run, with the HDF5 metadata the comparison needs."""
    run = BiosimSimulationRun(id=run_id, name="test", simulator_version=simulator_version,
                              status=BiosimSimulationRunStatus.SUCCEEDED)
    archive = OmexFile(file_hash_md5="hash", uploaded_filename="m.omex", bucket_name="test",
                       omex_gcs_path="m.omex", file_size=1)
    return BiosimulatorWorkflowRun(workflow_id="wf", file_hash_md5="hash", image_digest="digest",
                                   cache_buster="0", omex_file=archive,
                                   simulator_version=simulator_version, biosim_run=run,
                                   hdf5_file=_file(run_id, {"shared": ["t", "x"]}))


def found_run(simulator_version: BiosimulatorVersion) -> Any:
    """A stub for the run-fetch activity that always finds a succeeded run."""
    @activity.defn(name="get_existing_biosim_simulation_run_activity")
    async def found(arg: GetExistingBiosimSimulationRunActivityInput) -> GetExistingBiosimSimulationRunActivityOutput:
        return GetExistingBiosimSimulationRunActivityOutput(
            status=BiosimSimulationRunStatus.SUCCEEDED,
            biosim_workflow_run=workflow_run(arg.biosim_run_id, simulator_version))

    return found


def empty_statistics() -> Any:
    """A stub for the statistics activity that compares nothing."""
    @activity.defn(name="generate_statistics_activity")
    async def statistics(_: GenerateStatisticsActivityInput) -> GenerateStatisticsActivityOutput:
        return GenerateStatisticsActivityOutput(sims_run_info=[], comparison_statistics={})

    return statistics
