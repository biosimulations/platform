import logging
from datetime import timedelta
from enum import StrEnum

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_runs.activities import (
    PollBiosimRunActivityInput,
    SubmitBiosimRunActivityInput,
    SubmitBiosimRunActivityOutput,
    poll_biosim_run_activity,
    submit_biosim_run_activity,
)
from biosim_server.biosim_runs.models import BiosimulatorVersion, BiosimSimulationRunStatus, BiosimulatorWorkflowRun


class OmexSimWorkflowInput(BaseModel):
    omex_file: OmexFile
    simulator_version: BiosimulatorVersion
    cache_buster: str
    # Optional link to a SimulationRunRecord. When set, the workflow fires
    # update_run_status_activity right after submit to record the biosimulations
    # run id on the submission ledger -- visible to the listing/status endpoints
    # before the (potentially long) poll phase completes. The verification path
    # leaves this None and the early write is skipped.
    submission_run_id: str | None = None


class OmexSimWorkflowStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OmexSimWorkflowOutput(BaseModel):
    workflow_id: str
    workflow_status: OmexSimWorkflowStatus
    error_message: str | None = None
    # Set after submit (early) -- queryable before the full BiosimulatorWorkflowRun
    # is available. On cache hit this is the cached run's id.
    biosimulations_run_id: str | None = None
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

        # Phase 1: cache-check + submit. Returns the biosimulations run id immediately;
        # on a cache hit it also returns the full saved BiosimulatorWorkflowRun.
        submit_out: SubmitBiosimRunActivityOutput = await workflow.execute_activity(
            submit_biosim_run_activity,
            args=[SubmitBiosimRunActivityInput(
                workflow_id=self.sim_output.workflow_id,
                omex_file=sim_input.omex_file,
                simulator_version=sim_input.simulator_version,
                cache_buster=sim_input.cache_buster,
            )],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        self.sim_output.biosimulations_run_id = submit_out.biosimulations_run_id

        # Optional early DB write: if the caller passed a SimulationRunRecord link
        # (submission_run_id), record the biosimulations_run_id on it now so the
        # listing and status endpoints see it without waiting for the poll phase.
        # Lazy import avoids the biosim_runs <-> simulations module import cycle;
        # the activity is registered on the worker by name regardless of import site.
        if sim_input.submission_run_id is not None:
            from biosim_server.simulations.activities import (  # noqa: PLC0415
                UpdateRunStatusInput,
                update_run_status_activity,
            )
            try:
                await workflow.execute_activity(
                    update_run_status_activity,
                    args=[UpdateRunStatusInput(
                        run_id=sim_input.submission_run_id,
                        biosimulations_run_id=submit_out.biosimulations_run_id,
                    )],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError as e:
                # Non-fatal: failing to record the early run_id must not abort the run.
                workflow.logger.warning(f"Early SimulationRunRecord update failed: {e}")

        # Phase 2: on cache hit the saved record came back from submit; otherwise poll.
        if submit_out.cached_workflow_run is not None:
            saved_biosimulator_workflow_run: BiosimulatorWorkflowRun = submit_out.cached_workflow_run
        else:
            saved_biosimulator_workflow_run = await workflow.execute_activity(
                poll_biosim_run_activity,
                args=[PollBiosimRunActivityInput(
                    workflow_id=self.sim_output.workflow_id,
                    omex_file=sim_input.omex_file,
                    simulator_version=sim_input.simulator_version,
                    cache_buster=sim_input.cache_buster,
                    biosimulations_run_id=submit_out.biosimulations_run_id,
                )],
                start_to_close_timeout=timedelta(minutes=20),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

        if saved_biosimulator_workflow_run.biosim_run is None:
            self.sim_output.workflow_status = OmexSimWorkflowStatus.FAILED
            self.sim_output.error_message = "Saved BiosimulatorWorkflowRun has no biosim_run."
            return self.sim_output

        if saved_biosimulator_workflow_run.biosim_run.status != BiosimSimulationRunStatus.SUCCEEDED:
            self.sim_output.workflow_status = OmexSimWorkflowStatus.FAILED
            self.sim_output.error_message = saved_biosimulator_workflow_run.biosim_run.error_message
            return self.sim_output

        self.sim_output.workflow_status = OmexSimWorkflowStatus.COMPLETED
        self.sim_output.biosimulator_workflow_run = saved_biosimulator_workflow_run
        return self.sim_output
