"""RunsVerifyWorkflow as of e9e46db, before persistence retried without a cap.

Only ``run`` changed: it called the persist activity directly, with a
20-attempt policy and its own non-retryable list. A history recorded with this
class must replay under the current ``RunsVerifyWorkflow``.
"""
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from biosim_server.biosim_verify.activities import (
    PersistVerificationReportActivityInput,
    persist_verification_report_activity,
)
from biosim_server.biosim_verify.models import VerificationProvenance, VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow as CurrentRunsVerifyWorkflow
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput


@workflow.defn(name="RunsVerifyWorkflow")
class RunsVerifyWorkflow(CurrentRunsVerifyWorkflow):
    @workflow.run
    async def run(self, verify_input: RunsVerifyWorkflowInput) -> VerifyWorkflowOutput:
        if not workflow.patched("durable-verification-report-v1"):
            return await self._verify(verify_input)
        try:
            output = await self._verify(verify_input)
        except Exception:
            # Do not publish provider exception text, credentials, or internal paths.
            output = self.verify_output.model_copy(update={
                "workflow_status": VerifyWorkflowStatus.FAILED,
                "workflow_error": "Verification computation failed",
            })
        # Queries keep reporting progress until immutable publication acknowledges.
        self.verify_output = self.verify_output.model_copy(update={
            "workflow_status": VerifyWorkflowStatus.IN_PROGRESS,
            "workflow_results": None, "workflow_results_reference": None,
            "workflow_error": None, "report_available": False,
        })
        final = await workflow.execute_activity(
            persist_verification_report_activity,
            PersistVerificationReportActivityInput(output=output, provenance=VerificationProvenance(
                verification_id=output.workflow_id, workflow_run_id=output.workflow_run_id,
                owner_sub=output.owner_sub, created_at=workflow.info().start_time,
                computed_at=workflow.now(), requested_settings=verify_input.compare_settings.model_dump(),
                workflow_input=verify_input.model_dump(mode="json"),
            )), start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=20, maximum_interval=timedelta(minutes=1),
                                     non_retryable_error_types=["ReportIntegrityConflict", "ValueError"]),
        )
        self.verify_output = final
        return final
