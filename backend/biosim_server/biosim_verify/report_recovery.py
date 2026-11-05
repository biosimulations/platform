"""Shared bounded recovery logic for reconciliation and backfill."""

from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import RetryPolicy
from temporalio.service import RPCError, RPCStatusCode

from biosim_server.biosim_verify.activities import PERMANENT_ERRORS, prepare_report
from biosim_server.biosim_verify.database import VerificationDatabaseService
from biosim_server.biosim_verify.history import (
    PERSIST_ACTIVITY_TYPE,
    HistoryIdentityMismatch,
    StartInput,
    computed_output_from_history,
    read_start_input,
    terminal_failure_output,
)
from biosim_server.biosim_verify.workflow_persistence import PERSIST_RETRY
from biosim_server.biosim_verify.models import (
    ReportPersistenceState,
    VerificationProvenance,
    VerificationRecord,
    VerifyWorkflowOutput,
)
from biosim_server.biosim_verify.report_storage import (
    ReportIntegrityConflict,
    VerificationReportStore,
)

_TERMINAL_STATUSES = frozenset({
    WorkflowExecutionStatus.FAILED,
    WorkflowExecutionStatus.CANCELED,
    WorkflowExecutionStatus.TERMINATED,
    WorkflowExecutionStatus.TIMED_OUT,
})


#: A persist activity on this attempt or later is failing, not just starting.
PERSIST_RETRYING_ATTEMPT = 3


class RunningState(NamedTuple):
    """Why a running execution has no report yet, and for at least how long."""

    kind: str  # "persist_retrying" or "computing"
    age: timedelta


def minimum_retry_elapsed(attempt: int, policy: RetryPolicy = PERSIST_RETRY) -> timedelta:
    """The least time that must have passed before *attempt* could start.

    It is the sum of the retry intervals before that attempt.  Temporal does
    not report when the first attempt was scheduled, so this lower bound is
    the age of a retrying activity: an alert based on it is never early.
    """
    interval = policy.initial_interval.total_seconds()
    ceiling = policy.maximum_interval.total_seconds() if policy.maximum_interval else interval
    elapsed = 0.0
    for _ in range(max(attempt - 1, 0)):
        elapsed += min(interval, ceiling)
        interval *= policy.backoff_coefficient
    return timedelta(seconds=elapsed)


async def running_state(temporal: Client, record: VerificationRecord, *, now: datetime) -> RunningState | None:
    """Classify a running execution from its pending activities, or None if it is not running.

    A persist activity pending at attempt 3 or later is ``persist_retrying``:
    storage is failing for a computed result.  Anything else is ``computing``,
    whose age is the execution's.
    """
    handle = temporal.get_workflow_handle(record.workflow_id, run_id=record.workflow_run_id)
    desc = await handle.describe(rpc_timeout=timedelta(seconds=15))
    if desc.status != WorkflowExecutionStatus.RUNNING:
        return None
    for pending in desc.raw_description.pending_activities:
        if pending.activity_type.name == PERSIST_ACTIVITY_TYPE and pending.attempt >= PERSIST_RETRYING_ATTEMPT:
            return RunningState("persist_retrying", minimum_retry_elapsed(pending.attempt))
    started = desc.start_time or record.created
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return RunningState("computing", now - started)


class _DependencyError(Exception):
    """A dependency failed. The row is retried, never degraded."""


class _IntegrityConflict(Exception):
    """The workflow history does not match the ledger row."""


class _PermanentFailure(Exception):
    """Retrying cannot store this report, as the persist activity classifies it (PERMANENT_ERRORS)."""


async def _recover_computed_output(
    *,
    temporal: Client,
    handle: Any,
    start_input: StartInput,
    record: VerificationRecord,
    run_id: str | None,
    store: VerificationReportStore,
) -> tuple[VerifyWorkflowOutput, VerificationProvenance] | None:
    """The report the workflow tried to persist, with its provenance, or None.

    None means the workflow never scheduled the persist activity, so it never
    computed an output that could have been stored.  A dependency error raises
    instead: it must never fall through to a synthesized failure.
    """
    try:
        computed = await computed_output_from_history(
            temporal, handle, start_input, workflow_id=record.workflow_id, run_id=run_id,
        )
    except HistoryIdentityMismatch as exc:
        raise _IntegrityConflict() from exc
    except Exception as exc:
        raise _DependencyError() from exc
    if computed is None:
        return None
    try:
        output, provenance = await prepare_report(computed, store=store, row=record)
    except PERMANENT_ERRORS as exc:
        raise _PermanentFailure() from exc
    except Exception as exc:
        raise _DependencyError() from exc
    return output, provenance.model_copy(update={"origin": "recovered_from_history"})


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

    The written report is never a degraded version of a computed result: the
    computed output is recovered from the workflow's own history when the
    stored report is missing, and a terminal failure is synthesized only when
    the history proves that no output was ever computed.
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

    cached_start: StartInput | None = None

    async def start_input() -> StartInput | None:
        """Read the start input from history, once."""
        nonlocal cached_start
        if cached_start is None:
            try:
                cached_start = await read_start_input(temporal, handle, desc.workflow_type)
            except Exception:
                return None
        return cached_start

    if not record.workflow_input:
        original = await start_input()
        if original is None:
            return "dependency_error"
        if original.owner_sub != record.owner_sub:
            return "integrity_conflict"
        record.workflow_input = original.model_dump(mode="json")
        if apply:
            await ledger.record_workflow_input(record.workflow_id, record.workflow_input)

    if desc.status == WorkflowExecutionStatus.RUNNING:
        return "pending"
    recovered_provenance: VerificationProvenance | None = None
    if desc.status == WorkflowExecutionStatus.COMPLETED:
        try:
            output = await handle.result(rpc_timeout=timedelta(seconds=30))
            if output.workflow_id != record.workflow_id or output.owner_sub != record.owner_sub:
                return "integrity_conflict"
            if record.workflow_run_id and output.workflow_run_id != record.workflow_run_id:
                return "integrity_conflict"
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                if apply:
                    await ledger.mark_persistence_unavailable(record.workflow_id, "Temporal result is unavailable")
                return "missing_history"
            return "dependency_error"
        except Exception:
            return "dependency_error"
        origin = "temporal_backfill"
        if output.persistence_state == ReportPersistenceState.STORED:
            # The workflow says it stored a report that is not here. The
            # computed output is still in the history; recover it rather than
            # writing a report without statistics.
            original = await start_input()
            if original is None:
                return "dependency_error"
            try:
                recovered = await _recover_computed_output(
                    temporal=temporal, handle=handle, start_input=original,
                    record=record, run_id=desc.run_id, store=store,
                )
            except _IntegrityConflict:
                return "integrity_conflict"
            except _PermanentFailure:
                return "permanent_failure"
            except _DependencyError:
                return "dependency_error"
            if recovered is None:
                return "dependency_error"
            output, recovered_provenance = recovered
        elif output.workflow_results_reference is not None:
            try:
                output = await store.hydrate_activity_reference(output)
            except PERMANENT_ERRORS:
                return "permanent_failure"
            except Exception:
                return "dependency_error"
    else:
        if desc.status not in _TERMINAL_STATUSES:
            return "pending"
        original = await start_input()
        if original is None:
            return "dependency_error"
        if original.owner_sub != record.owner_sub:
            return "integrity_conflict"
        try:
            recovered = await _recover_computed_output(
                temporal=temporal, handle=handle, start_input=original,
                record=record, run_id=desc.run_id, store=store,
            )
        except _IntegrityConflict:
            return "integrity_conflict"
        except _PermanentFailure:
            return "permanent_failure"
        except _DependencyError:
            return "dependency_error"
        if recovered is not None:
            output, recovered_provenance = recovered
        else:
            close_time = getattr(desc, "close_time", None) or getattr(desc, "start_time", None)
            if close_time is None:
                return "dependency_error"
            output = terminal_failure_output(
                original, record.workflow_id, desc.run_id, desc.status, close_time,
            )
            origin = "terminal_execution_failure"

    output = output.model_copy(update={"workflow_results_reference": None})
    if recovered_provenance is not None:
        # A report recovered from history keeps the provenance the workflow
        # recorded: snapshots, run order and derivation link.
        provenance = recovered_provenance
    else:
        provenance = VerificationProvenance(
            verification_id=record.workflow_id,
            workflow_run_id=output.workflow_run_id or record.workflow_run_id,
            owner_sub=record.owner_sub,
            created_at=record.created,
            computed_at=datetime.now(UTC),
            requested_settings=output.compare_settings.model_dump(),
            workflow_input=record.workflow_input or {},
            origin=origin,
        )
    if apply:
        try:
            envelope = await store.save(output, provenance)
            await ledger.record_report_persisted(record.workflow_id, envelope.report_id, output.workflow_status)
        except ReportIntegrityConflict:
            return "integrity_conflict"
        except PERMANENT_ERRORS:
            # The persist activity would block this report; retrying cannot store it.
            return "permanent_failure"
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
                original = await read_start_input(temporal, handle, execution.workflow_type)
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
