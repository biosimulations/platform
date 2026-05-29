import asyncio
import logging
from datetime import timedelta
from typing import Any, Coroutine

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ChildWorkflowHandle

from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_runs import (
    BiosimulatorVersion,
    OmexSimWorkflow,
    OmexSimWorkflowInput,
    OmexSimWorkflowOutput,
    OmexSimWorkflowStatus,
)
from biosim_server.simulations.activities import UpdateRunStatusInput, update_run_status_activity
from biosim_server.simulations.models import SimulationJobStatus, ConglomerateStatus, job_status_to_display


class SimulationRunWorkflowInput(BaseModel):
    omex_file: OmexFile
    simulators: list[BiosimulatorVersion]
    job_ids: list[str]
    cache_buster: str


@workflow.defn
class SimulationRunWorkflow:
    workflow_input: SimulationRunWorkflowInput
    job_statuses: dict[str, SimulationJobStatus]

    @workflow.init
    def __init__(self, workflow_input: SimulationRunWorkflowInput) -> None:
        self.workflow_input = workflow_input
        self.job_statuses = {}
        for job_id, sim in zip(workflow_input.job_ids, workflow_input.simulators):
            self.job_statuses[job_id] = SimulationJobStatus(
                job_id=job_id,
                simulator_id=sim.id,
                version=sim.version,
                status="processing",
            )

    @workflow.query(name="get_status")
    def get_status(self) -> ConglomerateStatus:
        return ConglomerateStatus(
            processing_id=workflow.info().workflow_id,
            jobs=list(self.job_statuses.values()),
        )

    @workflow.run
    async def run(self, workflow_input: SimulationRunWorkflowInput) -> ConglomerateStatus:
        workflow.logger.setLevel(level=logging.INFO)
        workflow.logger.info("SimulationRunWorkflow started.")

        # Run each simulator via a child OmexSimWorkflow — the same per-run unit the
        # verification path uses. Each child submits to biosimulations.org, polls to
        # completion, and persists a BiosimulatorWorkflowRun (shared result cache).
        child_workflows: list[
            Coroutine[Any, Any, ChildWorkflowHandle[OmexSimWorkflowInput, OmexSimWorkflowOutput]]] = []
        for sim in workflow_input.simulators:
            child_workflows.append(
                workflow.start_child_workflow(
                    OmexSimWorkflow.run,  # type: ignore
                    args=[OmexSimWorkflowInput(
                        omex_file=workflow_input.omex_file,
                        simulator_version=sim,
                        cache_buster=workflow_input.cache_buster,
                    )],
                    result_type=OmexSimWorkflowOutput,
                    task_queue="verification_tasks",
                    execution_timeout=timedelta(minutes=30),
                )
            )

        child_handles: list[ChildWorkflowHandle[OmexSimWorkflowInput, OmexSimWorkflowOutput]] = \
            await asyncio.gather(*child_workflows)

        # Await each child and map its outcome onto the per-job status. A child that
        # raises (e.g. submit failed) must not abort the others, hence return_exceptions.
        child_outputs = await asyncio.gather(*child_handles, return_exceptions=True)

        for job_id, output in zip(workflow_input.job_ids, child_outputs):
            job = self.job_statuses[job_id]
            if isinstance(output, BaseException):
                job.status = "failure"
                job.error = str(output)
                continue
            run_record = output.biosimulator_workflow_run
            if run_record is not None and run_record.biosim_run is not None:
                job.biosimulations_run_id = run_record.biosim_run.id
            if output.workflow_status == OmexSimWorkflowStatus.COMPLETED:
                job.status = "success"
            else:
                job.status = "failure"
                job.error = output.error_message or "Simulation did not complete successfully"

        # Persist each job's terminal status to the queryable runs collection. Records
        # were created (status CREATED) by the API at submission time; this maps
        # run_id -> SimulationRunRecord.run_id. Failures here must not fail the run.
        update_tasks = []
        for job_id, job in self.job_statuses.items():
            update_tasks.append(
                workflow.execute_activity(
                    update_run_status_activity,
                    args=[UpdateRunStatusInput(
                        run_id=job_id,
                        status=job_status_to_display(job.status),
                        biosimulations_run_id=job.biosimulations_run_id,
                    )],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            )
        if update_tasks:
            await asyncio.gather(*update_tasks, return_exceptions=True)

        return self.get_status()
