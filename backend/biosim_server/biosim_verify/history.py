"""Recovery facts read from a verification workflow's own history.

This module is the only place that knows how to decode a verification
workflow's history.  It has no dependency on the HTTP layer, so operational
scripts (reconciler, backfill) can recover reports without importing FastAPI.

Two rules hold here:

* A computed result is never degraded.  When the workflow scheduled the
  persist activity, its input still holds the computed output, so that is the
  output a writer must store -- never a synthesized FAILED.
* Every writer builds the same terminal failure from the same immutable
  facts, so two writers produce byte-identical report content.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Union

from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle

from biosim_server.biosim_verify.activities import PersistVerificationReportActivityInput
from biosim_server.biosim_verify.models import VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflowInput
from biosim_server.biosim_verify.reconstruct_workflow import ReconstructComparisonWorkflowInput
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput

# The three verification workflow kinds. Any other type is not a verification.
VERIFY_WORKFLOW_TYPES: frozenset[str] = frozenset({
    "OmexVerifyWorkflow", "RunsVerifyWorkflow", "ReconstructComparisonWorkflow",
})

_START_INPUT_TYPES: dict[str, type[Any]] = {
    "OmexVerifyWorkflow": OmexVerifyWorkflowInput,
    "RunsVerifyWorkflow": RunsVerifyWorkflowInput,
    "ReconstructComparisonWorkflow": ReconstructComparisonWorkflowInput,
}

StartInput = Union[OmexVerifyWorkflowInput, RunsVerifyWorkflowInput, ReconstructComparisonWorkflowInput]

# The activity that publishes a computed report. Its scheduled input is the
# last copy of the computed output that Temporal holds.
PERSIST_ACTIVITY_TYPE = "persist_verification_report_activity"

# Bounds for the history scan. Recovery is not a hot path, but it must not
# read an unbounded history either.
HISTORY_PAGE_SIZE = 200
HISTORY_RPC_TIMEOUT = timedelta(seconds=15)
HISTORY_MAX_EVENTS = 20_000
START_INPUT_RPC_TIMEOUT = timedelta(seconds=10)


class HistoryTooLarge(RuntimeError):
    """The history holds more events than the recovery scan may read."""


class HistoryIdentityMismatch(RuntimeError):
    """The history holds an output that does not belong to this execution."""


async def read_start_input(
    client: Client,
    handle: WorkflowHandle[Any, VerifyWorkflowOutput],
    workflow_type: str,
) -> StartInput:
    """Read only the first history page, using the configured payload converter."""
    input_type = _START_INPUT_TYPES.get(workflow_type)
    if input_type is None:
        raise ValueError("Not a verification workflow")
    events = handle.fetch_history_events(page_size=1, rpc_timeout=START_INPUT_RPC_TIMEOUT)
    event = await anext(events)
    if not event.HasField("workflow_execution_started_event_attributes"):
        raise ValueError("Missing workflow start event")
    attrs = event.workflow_execution_started_event_attributes
    if attrs.workflow_type.name != workflow_type or len(attrs.input.payloads) != 1:
        raise ValueError("Unexpected workflow start input")
    decoded = await client.data_converter.decode(attrs.input.payloads, [input_type])
    if len(decoded) != 1 or not isinstance(decoded[0], input_type):
        raise ValueError("Invalid workflow start input")
    start: StartInput = decoded[0]
    return start


async def computed_output_from_history(
    client: Client,
    handle: WorkflowHandle[Any, VerifyWorkflowOutput],
    start_input: StartInput,
    *,
    workflow_id: str,
    run_id: str | None,
) -> PersistVerificationReportActivityInput | None:
    """The computed output the workflow tried to persist, or None.

    Returns None when no persist activity was ever scheduled, which means the
    workflow never produced an output that could have been stored.  A history
    that holds a persist activity for a different execution, or for a
    different owner, raises :class:`HistoryIdentityMismatch` instead.
    """
    payload = await _last_persist_payload(handle)
    if payload is None:
        return None
    decoded = await client.data_converter.decode([payload], [PersistVerificationReportActivityInput])
    if len(decoded) != 1 or not isinstance(decoded[0], PersistVerificationReportActivityInput):
        raise HistoryIdentityMismatch("persisted output could not be decoded")
    computed = decoded[0]
    output = computed.output
    if output.workflow_id != workflow_id:
        raise HistoryIdentityMismatch("persisted output belongs to another workflow")
    if run_id is not None and output.workflow_run_id != run_id:
        raise HistoryIdentityMismatch("persisted output belongs to another run")
    if output.owner_sub != start_input.owner_sub:
        raise HistoryIdentityMismatch("persisted output belongs to another owner")
    return computed


async def _last_persist_payload(handle: WorkflowHandle[Any, VerifyWorkflowOutput]) -> Any | None:
    """The payload of the last scheduled persist activity, or None."""
    events = handle.fetch_history_events(page_size=HISTORY_PAGE_SIZE, rpc_timeout=HISTORY_RPC_TIMEOUT)
    found: Any | None = None
    seen = 0
    async for event in events:
        seen += 1
        if seen > HISTORY_MAX_EVENTS:
            raise HistoryTooLarge("workflow history is larger than the recovery scan allows")
        if not event.HasField("activity_task_scheduled_event_attributes"):
            continue
        attrs = event.activity_task_scheduled_event_attributes
        if attrs.activity_type.name != PERSIST_ACTIVITY_TYPE:
            continue
        if len(attrs.input.payloads) != 1:
            raise HistoryIdentityMismatch("persist activity input is not a single payload")
        found = attrs.input.payloads[0]
    return found


def terminal_failure_output(
    start_input: StartInput,
    workflow_id: str,
    run_id: str | None,
    status: WorkflowExecutionStatus,
    close_time: datetime,
) -> VerifyWorkflowOutput:
    """The one deterministic FAILED report for a terminal execution.

    The content comes only from immutable history facts: the decoded start
    input and the execution's close time.  Provenance stays outside the
    content hash, so a different ``computed_at`` can never cause a conflict.
    """
    status_name = getattr(status, "name", str(status))
    return VerifyWorkflowOutput(
        workflow_id=workflow_id,
        workflow_run_id=run_id,
        owner_sub=start_input.owner_sub,
        compare_settings=start_input.compare_settings,
        workflow_status=VerifyWorkflowStatus.FAILED,
        workflow_error=f"Verification workflow ended with status {status_name}",
        timestamp=utc_iso(close_time),
    )


def utc_iso(value: datetime) -> str:
    """One ISO-8601 UTC rendering, so two writers agree byte for byte."""
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()
