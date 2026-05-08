import logging
from datetime import timedelta

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from biosim_server.biosim_runs import BiosimSimulationRunStatus, BiosimulatorWorkflowRun, \
    GetExistingBiosimSimulationRunActivityInput, get_existing_biosim_simulation_run_activity
from biosim_server.biosim_runs.activities import GetExistingBiosimSimulationRunActivityOutput
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.activities import generate_statistics_activity, GenerateStatisticsActivityInput
from biosim_server.biosim_verify.models import GenerateStatisticsActivityOutput, SimulationRunInfo, \
    VerifyWorkflowOutput, VerifyWorkflowStatus


class RunsVerifyWorkflowInput(BaseModel):
    biosimulations_run_ids: list[str]
    compare_settings: CompareSettings


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
            workflow_status=VerifyWorkflowStatus.IN_PROGRESS, timestamp=str(workflow.now()))

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
                    workflow_error=error_message)
                return self.verify_output
            else:
                simulator_workflow_runs.append(output.biosim_workflow_run)
                workflow.logger.info(f"verified access to completed run ids {verify_input.biosimulations_run_ids}.")

        # Generate comparison report
        stats = await generate_statistics(sim_workflow_runs=simulator_workflow_runs, compare_settings=self.verify_input.compare_settings)
        self.verify_output.workflow_results = stats
        self.verify_output.workflow_status = VerifyWorkflowStatus.COMPLETED
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
