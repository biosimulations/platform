"""Replay of histories recorded before the save patch.

The save activity is guarded by ``workflow.patched("save-verification-report-v1")``.
That guard only earns its keep if a history recorded *before* it replays under the
current workflow, so these tests record a real history with the pre-patch class and
replay it against the current one.

Three tests, three distinct failure modes:

* a patch-ID mismatch or a missing guard makes replay non-deterministic
  (``test_pre_patch_history_replays_without_the_save``);
* a changed command order makes a genuinely in-flight execution fail to resume
  (``test_in_flight_workflow_finishes_under_the_new_code``);
* deleting the save call outright would make the replay tests pass for the wrong
  reason, so ``test_patch_guard_is_what_skips_the_save`` pins that the current
  workflow does save.
"""

import asyncio
import uuid

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from biosim_server.biosim_runs import BiosimSimulationRunStatus, BiosimulatorVersion
from biosim_server.biosim_runs.activities import GetExistingBiosimSimulationRunActivityInput, \
    GetExistingBiosimSimulationRunActivityOutput
from biosim_server.biosim_verify.models import VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow
from biosim_server.common.temporal import pydantic_data_converter
from tests.biosim_verify.save_report_helpers import (
    compare_settings,
    empty_statistics,
    found_run,
    workflow_run,
)
from tests.fixtures.retention_history.pre_patch_runs_verify import (
    PrePatchRunsVerifyWorkflow,
    PrePatchRunsVerifyWorkflowInput,
)


def _replayer() -> Replayer:
    """A Replayer configured the way the workers are.

    ``workflow_runner`` is what validation and replay both use; the sandboxed
    default cannot import this module on Python 3.13.
    """
    return Replayer(
        workflows=[RunsVerifyWorkflow],
        data_converter=pydantic_data_converter,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )


async def _record_pre_patch_history(
    client: Client, simulator_version: BiosimulatorVersion,
) -> tuple[str, VerifyWorkflowOutput]:
    """Run the pre-patch workflow to completion and return its id and result."""
    async with Worker(client, task_queue="verification_tasks",
                      workflows=[PrePatchRunsVerifyWorkflow],
                      activities=[found_run(simulator_version), empty_statistics()],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        workflow_id = f"pre-patch-{uuid.uuid4()}"
        result: VerifyWorkflowOutput = await client.execute_workflow(
            PrePatchRunsVerifyWorkflow.run,
            args=[PrePatchRunsVerifyWorkflowInput(biosimulations_run_ids=["run1"],
                                                  compare_settings=compare_settings())],
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

    # raise_on_replay_failure defaults to True, so a nondeterminism error fails here.
    result = await _replayer().replay_workflow(history)
    assert result.replay_failure is None
    # The recorded execution completed, so replay must reach the same terminal
    # state. Asserting the completion event is what distinguishes "replayed and
    # finished" from "replayed only as far as the recorded history went".
    assert result.history is not None
    completed = [event for event in result.history.events
                 if event.HasField("workflow_execution_completed_event_attributes")]
    assert len(completed) == 1


@pytest.mark.asyncio
async def test_in_flight_workflow_finishes_under_the_new_code(
    temporal_client: Client, simulator_version_copasi: BiosimulatorVersion,
) -> None:
    """A workflow that started on the old code finishes on the new code.

    This is the deployment case, and it is not what the replay test above covers:
    there the whole history was recorded pre-patch, so replay only has to agree
    with itself. Here the execution is genuinely mid-flight -- started by a worker
    running the pre-patch class and stopped while blocked inside its run-fetch
    activity -- and then resumed by a worker running the *current* class.

    Two things must hold, and they are different in kind:

    * the current code must replay the commands the old code recorded without a
      nondeterminism error, which is what makes the deploy safe for executions
      already in progress;
    * the resumed execution *does* save its report. ``workflow.patched`` returns
      True when the workflow is not replaying -- a resumed execution is running
      new commands for the first time -- so an in-flight verification is caught
      up rather than left unsaved. That is the desired behaviour, and it is why
      no worker drain is needed for this change.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    saved: list[VerifyWorkflowOutput] = []

    @activity.defn(name="get_existing_biosim_simulation_run_activity")
    async def blocking_fetch(
        arg: GetExistingBiosimSimulationRunActivityInput,
    ) -> GetExistingBiosimSimulationRunActivityOutput:
        started.set()
        await release.wait()
        return GetExistingBiosimSimulationRunActivityOutput(
            status=BiosimSimulationRunStatus.SUCCEEDED,
            biosim_workflow_run=workflow_run(arg.biosim_run_id, simulator_version_copasi))

    @activity.defn(name="save_verification_report_activity")
    async def save(output: VerifyWorkflowOutput) -> bool:
        saved.append(output)
        return True

    workflow_id = f"in-flight-{uuid.uuid4()}"

    # Phase 1: the pre-patch code starts the execution and blocks inside its
    # run-fetch activity, so the history stops mid-execution.
    async with Worker(temporal_client, task_queue="verification_tasks",
                      workflows=[PrePatchRunsVerifyWorkflow],
                      activities=[blocking_fetch, empty_statistics()],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        handle = await temporal_client.start_workflow(
            PrePatchRunsVerifyWorkflow.run,
            args=[PrePatchRunsVerifyWorkflowInput(biosimulations_run_ids=["run1"],
                                                  compare_settings=compare_settings())],
            id=workflow_id, task_queue="verification_tasks",
            retry_policy=RetryPolicy(maximum_attempts=1))
        await asyncio.wait_for(started.wait(), timeout=30)

    # Phase 2: that worker is gone, so the current code resumes the execution.
    # A nondeterminism error surfaces here as a workflow failure or a hang, so the
    # timeout is part of the assertion: the resumed execution must finish.
    release.set()
    async with Worker(temporal_client, task_queue="verification_tasks",
                      workflows=[RunsVerifyWorkflow],
                      activities=[blocking_fetch, empty_statistics(), save],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        try:
            result = await asyncio.wait_for(handle.result(), timeout=20)
        except (asyncio.TimeoutError, WorkflowFailureError) as error:
            raise AssertionError(
                "The current code could not resume the pre-patch execution: a recorded "
                "command no longer matches, so replay is non-deterministic."
            ) from error

    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED
    # The resumed execution ran the save for the first time, so the report is
    # caught up rather than lost -- the reason this change needs no worker drain.
    assert len(saved) == 1
    assert saved[0].workflow_status == VerifyWorkflowStatus.COMPLETED


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
                      activities=[found_run(simulator_version_copasi), empty_statistics(), save],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        await temporal_client.execute_workflow(
            RunsVerifyWorkflow.run,
            args=[PrePatchRunsVerifyWorkflowInput(biosimulations_run_ids=["run1"],
                                                  compare_settings=compare_settings())],
            id=f"post-patch-{uuid.uuid4()}", task_queue="verification_tasks",
            retry_policy=RetryPolicy(maximum_attempts=1))

    assert len(saved) == 1
