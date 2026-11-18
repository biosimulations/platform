"""Replay of histories recorded before the save patch.

The save activity is guarded by ``workflow.patched("save-verification-report-v1")``.
That guard only earns its keep if a history recorded *before* it replays under the
current workflow, so these tests record a real history with the pre-patch class and
replay it against the current one.

Two failure modes, two tests:

* a patch-ID mismatch or a missing guard shows up as a replay nondeterminism error
  (``test_pre_patch_history_replays_without_the_save``);
* a change in command order shows up as the workflow not completing
  (``test_in_flight_workflow_finishes_under_the_new_code``).
"""

import uuid
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_runs import BiosimSimulationRun, BiosimSimulationRunStatus, BiosimulatorVersion, \
    BiosimulatorWorkflowRun
from biosim_server.biosim_runs.activities import GetExistingBiosimSimulationRunActivityInput, \
    GetExistingBiosimSimulationRunActivityOutput
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.activities import GenerateStatisticsActivityInput
from biosim_server.biosim_verify.models import GenerateStatisticsActivityOutput, VerifyWorkflowOutput, \
    VerifyWorkflowStatus
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow
from biosim_server.common.temporal import pydantic_data_converter
from tests.biosim_verify.test_compatibility import _file
from tests.fixtures.retention_history.pre_patch_runs_verify import (
    PrePatchRunsVerifyWorkflow,
    PrePatchRunsVerifyWorkflowInput,
)


def _compare_settings() -> CompareSettings:
    return CompareSettings(user_description="", include_outputs=False,
                           rel_tol=1e-6, abs_tol_min=1e-9, abs_tol_scale=1.0)


def _workflow_run(run_id: str, simulator_version: BiosimulatorVersion) -> BiosimulatorWorkflowRun:
    run = BiosimSimulationRun(id=run_id, name="test", simulator_version=simulator_version,
                              status=BiosimSimulationRunStatus.SUCCEEDED)
    archive = OmexFile(file_hash_md5="hash", uploaded_filename="m.omex", bucket_name="test",
                       omex_gcs_path="m.omex", file_size=1)
    return BiosimulatorWorkflowRun(workflow_id="wf", file_hash_md5="hash", image_digest="digest",
                                   cache_buster="0", omex_file=archive,
                                   simulator_version=simulator_version, biosim_run=run,
                                   hdf5_file=_file(run_id, {"shared": ["t", "x"]}))


def _found_run(simulator_version: BiosimulatorVersion) -> Any:
    @activity.defn(name="get_existing_biosim_simulation_run_activity")
    async def found(arg: GetExistingBiosimSimulationRunActivityInput) -> GetExistingBiosimSimulationRunActivityOutput:
        return GetExistingBiosimSimulationRunActivityOutput(
            status=BiosimSimulationRunStatus.SUCCEEDED,
            biosim_workflow_run=_workflow_run(arg.biosim_run_id, simulator_version))

    return found


def _empty_statistics() -> Any:
    @activity.defn(name="generate_statistics_activity")
    async def statistics(_: GenerateStatisticsActivityInput) -> GenerateStatisticsActivityOutput:
        return GenerateStatisticsActivityOutput(sims_run_info=[], comparison_statistics={})

    return statistics


async def _record_pre_patch_history(
    client: Client, simulator_version: BiosimulatorVersion,
) -> tuple[str, VerifyWorkflowOutput]:
    """Run the pre-patch workflow to completion and return its id and result."""
    async with Worker(client, task_queue="verification_tasks",
                      workflows=[PrePatchRunsVerifyWorkflow],
                      activities=[_found_run(simulator_version), _empty_statistics()],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        workflow_id = f"pre-patch-{uuid.uuid4()}"
        result: VerifyWorkflowOutput = await client.execute_workflow(
            PrePatchRunsVerifyWorkflow.run,
            args=[PrePatchRunsVerifyWorkflowInput(biosimulations_run_ids=["run1"],
                                                  compare_settings=_compare_settings())],
            id=workflow_id, task_queue="verification_tasks",
            retry_policy=RetryPolicy(maximum_attempts=1))
    return workflow_id, result


@pytest.mark.asyncio
async def test_pre_patch_history_replays_without_the_save(
    temporal_client: Client, simulator_version_copasi: BiosimulatorVersion,
) -> None:
    """A history recorded before the patch replays under the current workflow.

    The recorded history has no patch marker, so ``workflow.patched`` returns
    False on replay and the save activity is never scheduled. If the guard were
    missing, or the patch ID had changed, replay would raise a nondeterminism
    error instead of completing.
    """
    workflow_id, recorded = await _record_pre_patch_history(temporal_client, simulator_version_copasi)
    assert recorded.workflow_status == VerifyWorkflowStatus.COMPLETED

    history = await temporal_client.get_workflow_handle(workflow_id).fetch_history()

    replayer = Replayer(
        workflows=[RunsVerifyWorkflow],
        data_converter=pydantic_data_converter,
        # workflow_runner is what validation and replay both use; the sandboxed
        # default cannot import this module on Python 3.13.
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    # raise_on_replay_failure defaults to True, so a nondeterminism error fails here.
    result = await replayer.replay_workflow(history)
    assert result.replay_failure is None


@pytest.mark.asyncio
async def test_in_flight_workflow_finishes_under_the_new_code(
    temporal_client: Client, simulator_version_copasi: BiosimulatorVersion,
) -> None:
    """A workflow started on the old code, finished on the new code, still completes.

    Same mechanism as the replay test, framed as the deployment case: the
    execution began before the deploy, so its history carries no patch marker,
    and it must still reach a result rather than failing to finish.
    """
    workflow_id, recorded = await _record_pre_patch_history(temporal_client, simulator_version_copasi)

    history = await temporal_client.get_workflow_handle(workflow_id).fetch_history()
    replayer = Replayer(
        workflows=[RunsVerifyWorkflow],
        data_converter=pydantic_data_converter,
        # workflow_runner is what validation and replay both use; the sandboxed
        # default cannot import this module on Python 3.13.
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    result = await replayer.replay_workflow(history)

    # The replayed execution produced the same terminal output, and it did so
    # without ever scheduling the save activity.
    assert result.replay_failure is None
    replayed = result.history
    assert replayed is not None
    completed = [event for event in replayed.events
                 if event.HasField("workflow_execution_completed_event_attributes")]
    assert len(completed) == 1
    scheduled_saves = [event for event in replayed.events
                       if event.HasField("activity_task_scheduled_event_attributes")
                       and event.activity_task_scheduled_event_attributes.activity_type.name
                       == "save_verification_report_activity"]
    assert scheduled_saves == []


@pytest.mark.asyncio
async def test_patch_guard_is_what_skips_the_save(
    temporal_client: Client, simulator_version_copasi: BiosimulatorVersion,
) -> None:
    """The current workflow *does* save, so the skip above is the guard's doing.

    Without this, the replay test would pass just as well if the save call had
    been deleted outright.
    """
    saved: list[VerifyWorkflowOutput] = []

    @activity.defn(name="save_verification_report_activity")
    async def save(output: VerifyWorkflowOutput) -> bool:
        saved.append(output)
        return True

    async with Worker(temporal_client, task_queue="verification_tasks",
                      workflows=[RunsVerifyWorkflow],
                      activities=[_found_run(simulator_version_copasi), _empty_statistics(), save],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        await temporal_client.execute_workflow(
            RunsVerifyWorkflow.run,
            args=[PrePatchRunsVerifyWorkflowInput(biosimulations_run_ids=["run1"],
                                                  compare_settings=_compare_settings())],
            id=f"post-patch-{uuid.uuid4()}", task_queue="verification_tasks",
            retry_policy=RetryPolicy(maximum_attempts=1))

    assert len(saved) == 1
