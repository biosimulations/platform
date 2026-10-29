"""Comparison-only reconstruction.

This workflow loads retained numerical snapshots, verifies their checksums, and
compares them. It has no path that submits a simulator job or downloads a run
from biosimulations.org. A missing or mismatched snapshot fails the activity.
"""

import logging
from datetime import timedelta

from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from biosim_server.biosim_verify.activities import (
    PersistVerificationReportActivityInput,
    externalize_statistics,
    persist_verification_report_activity,
)
from biosim_server.biosim_verify.comparison import compare_loaded_runs
from biosim_server.biosim_verify.models import (
    CompareSettings,
    GenerateStatisticsActivityResult,
    NumericalInputSnapshot,
    SimulationRunInfo,
    VerificationProvenance,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.biosim_verify.snapshots import (
    SnapshotMismatch,
    SnapshotMissing,
    load_verified_snapshots,
    publish_snapshot_manifest,
)


class ReconstructComparisonWorkflowInput(BaseModel):
    source_verification_id: str
    compare_settings: CompareSettings
    snapshots: list[NumericalInputSnapshot]
    sim_run_info: list[SimulationRunInfo]
    ordered_run_ids: list[str]
    owner_sub: str | None = None


class CompareRetainedSnapshotsInput(BaseModel):
    snapshots: list[NumericalInputSnapshot]
    sim_run_info: list[SimulationRunInfo]
    compare_settings: CompareSettings
    ordered_run_ids: list[str]


async def compare_retained_snapshots(payload: CompareRetainedSnapshotsInput, workflow_id: str) -> GenerateStatisticsActivityResult:
    """Verify snapshot checksums and compare them. Does not contact a simulator."""
    try:
        datasets = await load_verified_snapshots(payload.snapshots)
    except SnapshotMissing as exc:
        raise ApplicationError("Retained numerical input is missing", non_retryable=True) from exc
    except SnapshotMismatch as exc:
        raise ApplicationError("Numerical input checksum mismatch", non_retryable=True) from exc
    output = compare_loaded_runs(payload.sim_run_info, datasets, payload.compare_settings)
    await publish_snapshot_manifest(workflow_id, payload.ordered_run_ids, payload.snapshots)
    return await externalize_statistics(output)


@activity.defn
async def compare_retained_snapshots_activity(
    payload: CompareRetainedSnapshotsInput,
) -> GenerateStatisticsActivityResult:
    return await compare_retained_snapshots(payload, activity.info().workflow_id or "")


@workflow.defn
class ReconstructComparisonWorkflow:
    verify_input: ReconstructComparisonWorkflowInput
    verify_output: VerifyWorkflowOutput

    @workflow.init
    def __init__(self, verify_input: ReconstructComparisonWorkflowInput) -> None:
        self.verify_input = verify_input
        self.verify_output = VerifyWorkflowOutput(
            workflow_id=workflow.info().workflow_id,
            compare_settings=verify_input.compare_settings,
            workflow_run_id=workflow.info().run_id,
            workflow_status=VerifyWorkflowStatus.IN_PROGRESS,
            timestamp=str(workflow.now()),
            owner_sub=verify_input.owner_sub,
            source_verification_id=verify_input.source_verification_id,
            derivation_kind="reconstruction",
            calculation="new_calculation",
        )

    @workflow.query(name="get_output")
    def get_output(self) -> VerifyWorkflowOutput:
        return self.verify_output

    @workflow.run
    async def run(self, verify_input: ReconstructComparisonWorkflowInput) -> VerifyWorkflowOutput:
        workflow.logger.setLevel(level=logging.INFO)
        try:
            result = await workflow.execute_activity(
                compare_retained_snapshots_activity,
                CompareRetainedSnapshotsInput(
                    snapshots=verify_input.snapshots,
                    sim_run_info=verify_input.sim_run_info,
                    compare_settings=verify_input.compare_settings,
                    ordered_run_ids=verify_input.ordered_run_ids,
                ),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=5, non_retryable_error_types=[
                    "ApplicationError",
                ]),
            )
            output = self.verify_output.model_copy(update={
                "workflow_status": VerifyWorkflowStatus.COMPLETED,
                "workflow_results": result.output,
                "workflow_results_reference": result.reference,
                "timestamp": str(workflow.now()),
            })
        except Exception:
            output = self.verify_output.model_copy(update={
                "workflow_status": VerifyWorkflowStatus.FAILED,
                "workflow_error": "Retained numerical inputs could not be compared",
                "timestamp": str(workflow.now()),
            })
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
                origin="workflow", source_verification_id=verify_input.source_verification_id,
                derivation_kind="reconstruction", ordered_run_ids=verify_input.ordered_run_ids,
                input_snapshots=verify_input.snapshots, actual_runs=verify_input.sim_run_info,
            )),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=20, maximum_interval=timedelta(minutes=1),
                                     non_retryable_error_types=["ReportIntegrityConflict", "ValueError"]),
        )
        self.verify_output = final
        return final
