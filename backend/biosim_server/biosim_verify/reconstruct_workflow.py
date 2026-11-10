"""Comparison-only reconstruction.

This workflow loads retained numerical snapshots, verifies their checksums, and
compares them. It has no path that submits a simulator job or downloads a run
from biosimulations.org. A missing or mismatched snapshot fails the activity
with a typed error, and the derived report says which check failed in
``failure_code`` and ``workflow_error``.
"""

import dataclasses
import logging
from datetime import timedelta

from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from biosim_server.biosim_verify.activities import (
    externalize_statistics,
)
from biosim_server.biosim_verify.comparison import compare_loaded_runs
from biosim_server.biosim_verify.models import (
    CompareSettings,
    DerivationKind,
    GenerateStatisticsActivityResult,
    NumericalInputSnapshot,
    ReportPersistenceState,
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
from biosim_server.biosim_verify.workflow_persistence import PERSIST_RETRY, persist_report

# Same backoff as a report write. Checksum mismatch, a missing snapshot, and
# the archival size limit are ApplicationErrors and stay non-retryable.
# Transient storage errors retry without a cap: this activity writes the
# snapshot manifest and any externalized statistics after the caller has
# already been charged, so a short storage outage must not fail the report.
RECONSTRUCT_COMPARE_RETRY: RetryPolicy = dataclasses.replace(
    PERSIST_RETRY, non_retryable_error_types=["ApplicationError"],
)


# The comparison activity's typed failures: failure_code -> the derived report's workflow_error.
SNAPSHOT_FAILURES = {
    "checksum_mismatch": "Retained numerical input checksum mismatch",
    "missing_numerical_inputs": "Retained numerical input is missing",
}


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
        raise ApplicationError(SNAPSHOT_FAILURES["missing_numerical_inputs"], type="missing_numerical_inputs",
                               non_retryable=True) from exc
    except SnapshotMismatch as exc:
        raise ApplicationError(SNAPSHOT_FAILURES["checksum_mismatch"], type="checksum_mismatch",
                               non_retryable=True) from exc
    output = compare_loaded_runs(payload.sim_run_info, datasets, payload.compare_settings)
    await publish_snapshot_manifest(workflow_id, payload.ordered_run_ids, payload.snapshots)
    return await externalize_statistics(output, workflow_id)


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
            derivation_kind=DerivationKind.RECONSTRUCTION,
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
                retry_policy=RECONSTRUCT_COMPARE_RETRY,
            )
            output = self.verify_output.model_copy(update={
                "workflow_status": VerifyWorkflowStatus.COMPLETED,
                "workflow_results": result.output,
                "workflow_results_reference": result.reference,
                "timestamp": str(workflow.now()),
            })
        except Exception as exc:
            cause = exc.cause if isinstance(exc, ActivityError) else None
            code = cause.type if isinstance(cause, ApplicationError) and cause.type in SNAPSHOT_FAILURES else None
            output = self.verify_output.model_copy(update={
                "workflow_status": VerifyWorkflowStatus.FAILED,
                "workflow_error": SNAPSHOT_FAILURES[code] if code else "Retained numerical inputs could not be compared",
                "failure_code": code,
                "timestamp": str(workflow.now()),
            })
        self.verify_output = self.verify_output.model_copy(update={
            "workflow_status": VerifyWorkflowStatus.IN_PROGRESS,
            "workflow_results": None, "workflow_results_reference": None,
            "workflow_error": None, "report_available": False,
            "persistence_state": ReportPersistenceState.PENDING,
        })
        final = await persist_report(output, VerificationProvenance(
            verification_id=output.workflow_id, workflow_run_id=output.workflow_run_id,
            owner_sub=output.owner_sub, created_at=workflow.info().start_time,
            computed_at=workflow.now(), requested_settings=verify_input.compare_settings.model_dump(),
            workflow_input=verify_input.model_dump(mode="json"),
            origin="workflow", source_verification_id=verify_input.source_verification_id,
            derivation_kind=DerivationKind.RECONSTRUCTION, ordered_run_ids=verify_input.ordered_run_ids,
            input_snapshots=verify_input.snapshots, actual_runs=verify_input.sim_run_info,
        ))
        self.verify_output = final
        return final
