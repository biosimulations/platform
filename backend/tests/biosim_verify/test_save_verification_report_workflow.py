"""The workflow half of report saving: where it is called and what a failure means.

Each test runs a real workflow against stubbed activities, so the assertions are
about the workflow's own behaviour rather than about MongoDB: the report is
offered at every finished exit point, a failed save is swallowed, and a
cancellation is not.
"""

import asyncio
import uuid
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, is_cancelled_exception
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_runs import BiosimSimulationRun, BiosimSimulationRunStatus, BiosimulatorVersion, \
    BiosimulatorWorkflowRun
from biosim_server.biosim_runs.activities import GetExistingBiosimSimulationRunActivityInput, \
    GetExistingBiosimSimulationRunActivityOutput
from biosim_server.biosim_verify import CompareSettings, activities
from biosim_server.biosim_verify.activities import GenerateStatisticsActivityInput, save_verification_report_activity
from biosim_server.biosim_verify.models import GenerateStatisticsActivityOutput, VerifyWorkflowOutput, \
    VerifyWorkflowStatus
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflow, OmexVerifyWorkflowInput
from biosim_server.biosim_verify.report_store import VerificationReportStore
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow, RunsVerifyWorkflowInput
from tests.biosim_verify.test_compatibility import _file


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


async def _execute_runs_workflow(client: Client, activities: list[Any],
                                 run_ids: list[str]) -> VerifyWorkflowOutput:
    async with Worker(client, task_queue="verification_tasks", workflows=[RunsVerifyWorkflow],
                      activities=activities, workflow_runner=UnsandboxedWorkflowRunner()):
        result: VerifyWorkflowOutput = await client.execute_workflow(
            RunsVerifyWorkflow.run,
            args=[RunsVerifyWorkflowInput(biosimulations_run_ids=run_ids,
                                          compare_settings=_compare_settings())],
            id=f"save-report-{uuid.uuid4()}", task_queue="verification_tasks",
            retry_policy=RetryPolicy(maximum_attempts=1))
    return result


@pytest.mark.asyncio
async def test_not_found_exit_point_saves_the_report(temporal_client: Client) -> None:
    """The early ``return`` after a missing run still persists its report."""
    saved: list[VerifyWorkflowOutput] = []

    @activity.defn(name="get_existing_biosim_simulation_run_activity")
    async def not_found(_: GetExistingBiosimSimulationRunActivityInput) -> GetExistingBiosimSimulationRunActivityOutput:
        return GetExistingBiosimSimulationRunActivityOutput(status=BiosimSimulationRunStatus.RUN_ID_NOT_FOUND)

    @activity.defn(name="save_verification_report_activity")
    async def save(output: VerifyWorkflowOutput) -> bool:
        saved.append(output)
        return True

    result = await _execute_runs_workflow(temporal_client, [not_found, save], ["missing"])

    assert result.workflow_status == VerifyWorkflowStatus.RUN_ID_NOT_FOUND
    assert [output.workflow_status for output in saved] == [VerifyWorkflowStatus.RUN_ID_NOT_FOUND]
    assert saved[0].workflow_error == "Simulation run with id missing not found."


@pytest.mark.asyncio
async def test_completed_exit_point_saves_the_report(temporal_client: Client,
                                                     simulator_version_copasi: BiosimulatorVersion) -> None:
    """The successful ``return`` at the end of the run persists its report."""
    saved: list[VerifyWorkflowOutput] = []

    @activity.defn(name="save_verification_report_activity")
    async def save(output: VerifyWorkflowOutput) -> bool:
        saved.append(output)
        return True

    result = await _execute_runs_workflow(
        temporal_client, [_found_run(simulator_version_copasi), _empty_statistics(), save], ["run1"])

    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED
    assert len(saved) == 1
    assert saved[0].workflow_id == result.workflow_id
    assert saved[0].workflow_status == VerifyWorkflowStatus.COMPLETED


@pytest.mark.asyncio
async def test_omex_exit_point_saves_the_report(temporal_client: Client) -> None:
    """``OmexVerifyWorkflow`` has one finished exit point and saves there too."""
    saved: list[VerifyWorkflowOutput] = []

    @activity.defn(name="save_verification_report_activity")
    async def save(output: VerifyWorkflowOutput) -> bool:
        saved.append(output)
        return True

    archive = OmexFile(file_hash_md5="hash", uploaded_filename="m.omex", bucket_name="test",
                       omex_gcs_path="m.omex", file_size=1)
    async with Worker(temporal_client, task_queue="verification_tasks", workflows=[OmexVerifyWorkflow],
                      activities=[_empty_statistics(), save],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        result: VerifyWorkflowOutput = await temporal_client.execute_workflow(
            OmexVerifyWorkflow.run,
            # No requested simulators: the workflow still reaches its single
            # finished exit point, without starting child simulation workflows.
            args=[OmexVerifyWorkflowInput(omex_file=archive, requested_simulators=[],
                                          compare_settings=_compare_settings(), cache_buster="0")],
            id=f"save-report-{uuid.uuid4()}", task_queue="verification_tasks",
            retry_policy=RetryPolicy(maximum_attempts=1))

    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED
    assert len(saved) == 1
    assert saved[0].workflow_id == result.workflow_id


@pytest.mark.asyncio
async def test_failed_save_does_not_fail_the_verification(temporal_client: Client,
                                                          simulator_version_copasi: BiosimulatorVersion) -> None:
    """A storage outage leaves the verification COMPLETED; the save is best-effort."""
    attempts = 0

    @activity.defn(name="save_verification_report_activity")
    async def save(_: VerifyWorkflowOutput) -> bool:
        nonlocal attempts
        attempts += 1
        raise ApplicationError("storage down", non_retryable=True)

    result = await _execute_runs_workflow(
        temporal_client, [_found_run(simulator_version_copasi), _empty_statistics(), save], ["run1"])

    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED
    # non_retryable means exactly one attempt, not five
    assert attempts == 1


@pytest.mark.asyncio
async def test_transient_save_failure_is_retried(temporal_client: Client,
                                                 simulator_version_copasi: BiosimulatorVersion) -> None:
    """A retryable failure is retried under the activity's retry policy."""
    attempts = 0

    @activity.defn(name="save_verification_report_activity")
    async def save(_: VerifyWorkflowOutput) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient database error")
        return True

    result = await _execute_runs_workflow(
        temporal_client, [_found_run(simulator_version_copasi), _empty_statistics(), save], ["run1"])

    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED
    assert attempts == 2


@pytest.mark.asyncio
async def test_cancellation_during_save_cancels_the_workflow(temporal_client: Client,
                                                             simulator_version_copasi: BiosimulatorVersion) -> None:
    """Cancellation is re-raised, so a cancelled workflow does not return a result."""
    started = asyncio.Event()

    @activity.defn(name="save_verification_report_activity")
    async def save(_: VerifyWorkflowOutput) -> bool:
        started.set()
        await asyncio.Event().wait()  # block until the activity is cancelled
        return True

    async with Worker(temporal_client, task_queue="verification_tasks", workflows=[RunsVerifyWorkflow],
                      activities=[_found_run(simulator_version_copasi), _empty_statistics(), save],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        handle = await temporal_client.start_workflow(
            RunsVerifyWorkflow.run,
            args=[RunsVerifyWorkflowInput(biosimulations_run_ids=["run1"],
                                          compare_settings=_compare_settings())],
            id=f"save-report-{uuid.uuid4()}", task_queue="verification_tasks",
            retry_policy=RetryPolicy(maximum_attempts=1))
        await asyncio.wait_for(started.wait(), timeout=30)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as excinfo:
            await asyncio.wait_for(handle.result(), timeout=30)

    assert is_cancelled_exception(excinfo.value.cause)


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_real_activity_stores_the_finished_report(
        temporal_client: Client, verification_report_store_mongo: VerificationReportStore,
        simulator_version_copasi: BiosimulatorVersion, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real activity, store and workflow together: the report is readable back."""
    monkeypatch.setattr(activities, "get_verification_report_store",
                        lambda: verification_report_store_mongo)

    result = await _execute_runs_workflow(
        temporal_client,
        [_found_run(simulator_version_copasi), _empty_statistics(), save_verification_report_activity],
        ["run1"])

    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED
    assert await verification_report_store_mongo.get(result.workflow_id) == result
