import logging
from datetime import timedelta

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

from biosim_server.biosim_runs import BiosimSimulationRunStatus, BiosimulatorWorkflowRun, \
    GetExistingBiosimSimulationRunActivityInput, get_existing_biosim_simulation_run_activity
from biosim_server.biosim_runs.activities import GetExistingBiosimSimulationRunActivityOutput
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.activities import generate_statistics_activity, GenerateStatisticsActivityInput, \
    save_verification_report_activity
from biosim_server.biosim_verify.models import GenerateStatisticsActivityOutput, SimulationRunInfo, \
    VerifyWorkflowOutput, VerifyWorkflowStatus


# Identifies the history change that added report saving. Both verifying workflows
# mark their histories with it, so a run recorded before the change replays without
# the activity and a run recorded after it replays with it.
SAVE_REPORT_PATCH_ID = "save-verification-report-v1"

# One minute per attempt. Motor sets no socket timeout, so this is what ends a
# stuck write. Timeout bounds one attempt.
# An insert or a GridFS upload of a report finishes well inside one minute.
SAVE_REPORT_START_TO_CLOSE_TIMEOUT = timedelta(minutes=1)

# Retry transient failures without a limit. The store is write-once, so repeated
# saves converge rather than conflict. A cap here would close the workflow during
# a storage outage and start the retention clock with no stored report -- turning
# a recoverable outage into permanent loss, which is what this feature prevents.
# A workflow that keeps retrying also keeps its history, so the result stays
# recoverable.
SAVE_REPORT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    # No maximum_attempts: 0 means unlimited.
    maximum_attempts=0,
)


class RunsVerifyWorkflowInput(BaseModel):
    biosimulations_run_ids: list[str]
    compare_settings: CompareSettings
    owner_sub: str | None = None


@workflow.defn
class RunsVerifyWorkflow:
    verify_input: RunsVerifyWorkflowInput
    verify_output: VerifyWorkflowOutput

    @workflow.init
    def __init__(self, verify_input: RunsVerifyWorkflowInput) -> None:
        self.verify_input = verify_input
        # assert verify_input.workflow_id == workflow.info().workflow_id
        self.verify_output = VerifyWorkflowOutput(workflow_id=workflow.info().workflow_id,
            compare_settings=verify_input.compare_settings, workflow_run_id=workflow.info().run_id,
            workflow_status=VerifyWorkflowStatus.IN_PROGRESS, timestamp=str(workflow.now()),
            owner_sub=verify_input.owner_sub)

    @workflow.query(name="get_output")
    def get_runs_sim_workflow_output(self) -> VerifyWorkflowOutput:
        return self.verify_output

    @workflow.run
    async def run(self, verify_input: RunsVerifyWorkflowInput) -> VerifyWorkflowOutput:
        workflow.logger.setLevel(level=logging.INFO)
        workflow.logger.info("Main workflow started.")

        # get simulator workflow runs from biosimulations.org or database cache
        simulator_workflow_runs: list[BiosimulatorWorkflowRun] = []
        for biosimulation_run_id in verify_input.biosimulations_run_ids:

            output = await get_biosim_simulation_run(workflow_id=workflow.info().workflow_id,
                                                     biosim_run_id=biosimulation_run_id)

            if output.status != BiosimSimulationRunStatus.SUCCEEDED or output.biosim_workflow_run is None or output.biosim_workflow_run.biosim_run is None:

                error_message = f"Failed to retrieve simulation run with id {biosimulation_run_id}."
                status = VerifyWorkflowStatus.FAILED
                if output.status == BiosimSimulationRunStatus.RUN_ID_NOT_FOUND:
                    error_message = f"Simulation run with id {biosimulation_run_id} not found."
                    status = VerifyWorkflowStatus.RUN_ID_NOT_FOUND
                elif output.status == BiosimSimulationRunStatus.FAILED:
                    if output.biosim_workflow_run is not None and output.biosim_workflow_run.biosim_run is not None and output.biosim_workflow_run.biosim_run.error_message is not None:
                        error_message = output.biosim_workflow_run.biosim_run.error_message

                self.verify_output = VerifyWorkflowOutput(workflow_id=workflow.info().workflow_id,
                    compare_settings=verify_input.compare_settings, workflow_run_id=workflow.info().run_id,
                    workflow_status=status, timestamp=str(workflow.now()),
                    workflow_error=error_message, owner_sub=verify_input.owner_sub)
                await save_verification_report(self.verify_output)
                return self.verify_output
            else:
                simulator_workflow_runs.append(output.biosim_workflow_run)
                workflow.logger.info(f"verified access to completed run ids {verify_input.biosimulations_run_ids}.")

        # Generate comparison report
        stats = await generate_statistics(sim_workflow_runs=simulator_workflow_runs, compare_settings=self.verify_input.compare_settings)
        self.verify_output.workflow_results = stats
        self.verify_output.workflow_status = VerifyWorkflowStatus.COMPLETED
        await save_verification_report(self.verify_output)
        return self.verify_output


async def get_biosim_simulation_run(workflow_id: str, biosim_run_id: str) -> GetExistingBiosimSimulationRunActivityOutput:
    try:
        output = await workflow.execute_activity(
            get_existing_biosim_simulation_run_activity,
            args=[GetExistingBiosimSimulationRunActivityInput(workflow_id=workflow_id,biosim_run_id=biosim_run_id,
                                                              abort_on_not_found=True)],
            start_to_close_timeout=timedelta(seconds=60),retry_policy=RetryPolicy(maximum_attempts=30))
        return output
    except ActivityError as e:
        workflow.logger.exception(f"Failed to get biosim simulation run with id {biosim_run_id}.", exc_info=e)
        raise e


async def generate_statistics(sim_workflow_runs: list[BiosimulatorWorkflowRun], compare_settings: CompareSettings) -> GenerateStatisticsActivityOutput:
    try:
        run_data = [SimulationRunInfo(biosim_sim_run=a.biosim_run, hdf5_file=a.hdf5_file)
                    for a in sim_workflow_runs if a.biosim_run is not None and a.hdf5_file is not None]
        generate_statistics_input = GenerateStatisticsActivityInput(sim_run_info_list=run_data, compare_settings=compare_settings)
        # Generate comparison report
        generate_statistics_output: GenerateStatisticsActivityOutput = await workflow.execute_activity(
            generate_statistics_activity,
            arg=generate_statistics_input,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=100, backoff_coefficient=2.0,
                                     maximum_interval=timedelta(seconds=10)))
        return generate_statistics_output
    except ActivityError as e:
        workflow.logger.exception(f"Failed to generate statistics in workflow_id {workflow.info().workflow_id}.", exc_info=e)
        raise e


async def save_verification_report(output: VerifyWorkflowOutput) -> None:
    """Best-effort save of a finished report, called at a workflow's exit points.

    Three rules live here so both workflows cannot drift apart:

    * The patch guard is inside, so a history recorded before this step replays
      without the activity instead of failing non-determinism.
    * A save failure is logged and swallowed. The verification's own result is
      what the caller asked for, and the report is still in Temporal history
      until retention expires; one storage outage must not turn a COMPLETED
      verification into a failed workflow.
    * Cancellation is re-raised. ``is_cancelled_exception`` covers Temporal's
      own ``CancelledError`` and an ``ActivityError`` wrapping one; plain
      ``asyncio.CancelledError`` is a ``BaseException`` and already bypasses the
      ``except Exception`` below.
    """
    if not workflow.patched(SAVE_REPORT_PATCH_ID):
        return
    try:
        await workflow.execute_activity(
            save_verification_report_activity,
            arg=output,
            start_to_close_timeout=SAVE_REPORT_START_TO_CLOSE_TIMEOUT,
            retry_policy=SAVE_REPORT_RETRY_POLICY,
        )
    except Exception as e:
        if is_cancelled_exception(e):
            raise
        workflow.logger.exception(
            f"Failed to save verification report for workflow {output.workflow_id}; "
            f"the verification result is unaffected.", exc_info=e)
