import logging
from datetime import timedelta
from enum import StrEnum

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_runs.activities import submit_biosim_simulation_run_activity, \
    SubmitBiosimSimulationRunActivityInput
from biosim_server.biosim_runs.models import BiosimulatorVersion, BiosimSimulationRunStatus, BiosimulatorWorkflowRun


class OmexSimWorkflowInput(BaseModel):
    omex_file: OmexFile
    simulator_version: BiosimulatorVersion
    cache_buster: str


class OmexSimWorkflowStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OmexSimWorkflowOutput(BaseModel):
    workflow_id: str
    workflow_status: OmexSimWorkflowStatus
    error_message: str | None = None
    biosimulator_workflow_run: BiosimulatorWorkflowRun | None = None


@workflow.defn
class OmexSimWorkflow:
    sim_input: OmexSimWorkflowInput
    sim_output: OmexSimWorkflowOutput

    @workflow.init
    def __init__(self, sim_input: OmexSimWorkflowInput) -> None:
        self.sim_input = sim_input
        self.sim_output = OmexSimWorkflowOutput(workflow_id=workflow.info().workflow_id,
                                                workflow_status=OmexSimWorkflowStatus.IN_PROGRESS)

    @workflow.query
    def get_omex_sim_workflow_run(self) -> OmexSimWorkflowOutput:
        return self.sim_output

    @workflow.run
    async def run(self, sim_input: OmexSimWorkflowInput) -> OmexSimWorkflowOutput:
        self.sim_output.workflow_id = workflow.info().workflow_id
        workflow.logger.setLevel(level=logging.DEBUG)
        workflow.logger.info(f"Child workflow started for "
                             f"{sim_input.simulator_version.id}:{sim_input.simulator_version.version}.")

        saved_biosimulator_workflow_run: BiosimulatorWorkflowRun = await submit_biosim_simulation_run(
            workflow_id=self.sim_output.workflow_id, omex_file=self.sim_input.omex_file,
            simulator_version=self.sim_input.simulator_version, cache_buster=self.sim_input.cache_buster)

        if saved_biosimulator_workflow_run is None or saved_biosimulator_workflow_run.biosim_run is None:
            self.sim_output.workflow_status = OmexSimWorkflowStatus.FAILED
            self.sim_output.error_message = "Failed to submit biosim simulation run - activity returned None."
            return self.sim_output

        if saved_biosimulator_workflow_run.biosim_run.status != BiosimSimulationRunStatus.SUCCEEDED:
            self.sim_output.workflow_status = OmexSimWorkflowStatus.FAILED
            self.sim_output.error_message = saved_biosimulator_workflow_run.biosim_run.error_message
            return self.sim_output

        self.sim_output.workflow_status = OmexSimWorkflowStatus.COMPLETED
        self.sim_output.biosimulator_workflow_run = saved_biosimulator_workflow_run
        return self.sim_output


async def submit_biosim_simulation_run(workflow_id: str,
                                       omex_file: OmexFile,
                                       simulator_version: BiosimulatorVersion,
                                       cache_buster: str) -> BiosimulatorWorkflowRun:
    try:
        return await workflow.execute_activity(
            submit_biosim_simulation_run_activity,
            args=[SubmitBiosimSimulationRunActivityInput(workflow_id=workflow_id,
                                                         omex_file=omex_file,
                                                         simulator_version=simulator_version,
                                                         cache_buster=cache_buster)],
            start_to_close_timeout=timedelta(seconds=60*20),  # Activity timeout
            retry_policy=RetryPolicy(maximum_attempts=1), )
    except ActivityError as e:
        workflow.logger.exception(f"Failed to submit biosim simulation run: {str(e)}", exc_info=e)
        raise e