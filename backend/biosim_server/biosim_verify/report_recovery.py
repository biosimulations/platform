"""Shared bounded recovery logic for reconciliation and backfill."""

from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from biosim_server.biosim_verify.database import VerificationDatabaseService
from biosim_server.biosim_verify.models import (
    ReportPersistenceState,
    VerificationProvenance,
    VerificationRecord,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
    sanitize_workflow_error,
)
from biosim_server.biosim_verify.report_storage import (
    ReportIntegrityConflict,
    VerificationReportStore,
)


def _output_from_record(record: VerificationRecord, status: VerifyWorkflowStatus, error: str) -> VerifyWorkflowOutput:
    raw_settings: dict[str, Any] = {}
    if record.workflow_input and isinstance(record.workflow_input.get("compare_settings"), dict):
        raw_settings = record.workflow_input["compare_settings"]  # type: ignore[assignment]
    from biosim_server.biosim_verify.models import CompareSettings
    settings = CompareSettings.model_validate(raw_settings)
    return VerifyWorkflowOutput(
        workflow_id=record.workflow_id,
        workflow_run_id=record.workflow_run_id,
        owner_sub=record.owner_sub,
        compare_settings=settings,
        workflow_status=status,
        workflow_error=sanitize_workflow_error(error),
        timestamp=(record.created.replace(tzinfo=UTC) if record.created.tzinfo is None
                   else record.created.astimezone(UTC)).isoformat(),
        persistence_state=ReportPersistenceState.PENDING,
    )


async def process_record(
    *,
    record: VerificationRecord,
    ledger: VerificationDatabaseService,
    store: VerificationReportStore,
    temporal: Client | None,
    apply: bool,
) -> str:
    """Process one candidate without querying workflow state.

    The only Temporal read for a terminal execution is the run-pinned
    ``result()`` call.  ``describe`` is used only to distinguish running,
    failed, and expired executions; no workflow query is issued.
    """
    try:
        saved = await store.get(record.workflow_id)
        if saved is not None:
            if apply:
                await ledger.record_report_persisted(record.workflow_id, saved.report_id or record.workflow_id,
                                                     saved.workflow_status)
            return "already_present"
    except Exception:
        return "dependency_error"
    if temporal is None:
        return "dependency_error"
    try:
        handle = temporal.get_workflow_handle(
            record.workflow_id, run_id=record.workflow_run_id, result_type=VerifyWorkflowOutput,
        )
        desc = await handle.describe(rpc_timeout=timedelta(seconds=15))
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            if apply:
                await ledger.mark_persistence_unavailable(record.workflow_id, "Temporal history is unavailable")
            return "missing_history"
        return "dependency_error"
    except Exception:
        return "dependency_error"

    if not record.workflow_run_id and getattr(desc, "run_id", None):
        record.workflow_run_id = desc.run_id
        if apply:
            await ledger.record_workflow_run_id(record.workflow_id, desc.run_id)
        handle = temporal.get_workflow_handle(
            record.workflow_id, run_id=desc.run_id, result_type=VerifyWorkflowOutput,
        )
    if not record.workflow_input:
        try:
            from biosim_server.api.main import _read_verification_input
            original = await _read_verification_input(temporal, handle, desc.workflow_type)
            if original.owner_sub != record.owner_sub:
                return "integrity_conflict"
            record.workflow_input = original.model_dump(mode="json")
            if apply:
                await ledger.record_workflow_input(record.workflow_id, record.workflow_input)
        except Exception:
            return "dependency_error"
    if desc.status == WorkflowExecutionStatus.RUNNING:
        return "pending"
    if desc.status == WorkflowExecutionStatus.COMPLETED:
        try:
            output = await handle.result(rpc_timeout=timedelta(seconds=30))
            if output.workflow_id != record.workflow_id or output.owner_sub != record.owner_sub:
                return "integrity_conflict"
            if record.workflow_run_id and output.workflow_run_id != record.workflow_run_id:
                return "integrity_conflict"
            if output.workflow_results_reference is not None:
                output = await store.hydrate_activity_reference(output)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                if apply:
                    await ledger.mark_persistence_unavailable(record.workflow_id, "Temporal result is unavailable")
                return "missing_history"
            return "dependency_error"
        except Exception:
            return "dependency_error"
    else:
        if desc.status not in {
            WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.CANCELED,
            WorkflowExecutionStatus.TERMINATED, WorkflowExecutionStatus.TIMED_OUT,
        }:
            return "pending"
        status_name = getattr(desc.status, "name", "UNKNOWN")
        try:
            output = _output_from_record(record, VerifyWorkflowStatus.FAILED,
                                         f"Temporal execution ended with {status_name}")
        except ValueError:
            return "dependency_error"

    output = output.model_copy(update={"workflow_results_reference": None})
    provenance = VerificationProvenance(
        verification_id=record.workflow_id,
        workflow_run_id=output.workflow_run_id or record.workflow_run_id,
        owner_sub=record.owner_sub,
        created_at=record.created,
        computed_at=datetime.now(UTC),
        requested_settings=output.compare_settings.model_dump(),
        workflow_input=record.workflow_input or {},
        origin="temporal_backfill" if desc.status == WorkflowExecutionStatus.COMPLETED else "terminal_execution_failure",
    )
    if apply:
        try:
            envelope = await store.save(output, provenance)
            await ledger.record_report_persisted(record.workflow_id, envelope.report_id, output.workflow_status)
        except ReportIntegrityConflict:
            return "integrity_conflict"
        except Exception:
            return "dependency_error"
    return "saved"


async def inventory_retained_executions(
    *, temporal: Client, ledger: VerificationDatabaseService, store: VerificationReportStore,
    apply: bool, limit: int = 1000, next_page_token: bytes | None = None,
    since: datetime | None = None, until: datetime | None = None,
) -> tuple[dict[str, int], bytes | None]:
    """Recover orphaned ledger rows only from typed, trusted start-event input.

    Repeated inventories are safe; no submission or comparison is started.
    Unknown ownership is quarantined rather than converted to public ownership.
    """
    from biosim_server.api.main import _read_verification_input
    from biosim_server.biosim_verify.models import VerificationType
    counters: dict[str, int] = {}
    query = 'WorkflowType IN ("RunsVerifyWorkflow", "OmexVerifyWorkflow")'
    if since:
        query += f' AND StartTime >= "{since.isoformat()}"'
    if until:
        query += f' AND StartTime < "{until.isoformat()}"'
    executions = temporal.list_workflows(query=query, page_size=limit, next_page_token=next_page_token,
                                        rpc_timeout=timedelta(seconds=15))
    await executions.fetch_next_page()
    for execution in executions.current_page or []:
        try:
            record = await ledger.get_verification(execution.id)
            if record is None:
                handle = temporal.get_workflow_handle(execution.id, run_id=execution.run_id)
                original = await _read_verification_input(temporal, handle, execution.workflow_type)
                # An absent owner field in a legacy input is not evidence of public ownership.
                if 'owner_sub' not in original.model_fields_set:
                    counters['unknown_ownership'] = counters.get('unknown_ownership', 0) + 1
                    continue
                from biosim_server.biosim_verify.report_codec import encode_report
                input_data = original.model_dump(mode='json')
                record = VerificationRecord(workflow_id=execution.id, workflow_run_id=execution.run_id,
                    verify_type=VerificationType.OMEX if execution.workflow_type == 'OmexVerifyWorkflow' else VerificationType.RUNS,
                    owner_sub=original.owner_sub, created=execution.start_time,
                    workflow_input=input_data, request_hash=encode_report(input_data)[3])
                if apply:
                    await ledger.insert_verification(record)
            outcome = await process_record(record=record, ledger=ledger, store=store, temporal=temporal, apply=apply)
        except Exception:
            outcome = 'dependency_error'
        counters[outcome] = counters.get(outcome, 0) + 1
    return counters, executions.next_page_token
