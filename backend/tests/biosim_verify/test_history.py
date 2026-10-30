"""Workflow-history recovery helpers: layering, the persist-input scan, and one terminal failure."""
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import WorkflowExecutionStatus

from biosim_server.biosim_verify import history
from biosim_server.biosim_verify.activities import PersistVerificationReportActivityInput
from biosim_server.biosim_verify.history import (
    HistoryIdentityMismatch,
    HistoryTooLarge,
    computed_output_from_history,
    terminal_failure_output,
)
from biosim_server.biosim_verify.models import VerificationProvenance, VerifyWorkflowOutput
from biosim_server.biosim_verify.report_codec import encode_report
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput
from biosim_server.common.temporal import pydantic_data_converter
from tests.biosim_verify.test_report_storage import _output

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

BACKEND = Path(__file__).resolve().parents[2]


def _start(owner: str | None = "owner") -> RunsVerifyWorkflowInput:
    return RunsVerifyWorkflowInput(biosimulations_run_ids=["run1"], owner_sub=owner,
                                   compare_settings=_output().compare_settings)


def _computed(workflow_id: str = "wf", run_id: str = "run", owner: str | None = "owner",
              error: str | None = None) -> PersistVerificationReportActivityInput:
    output = _output(owner=owner).model_copy(update={
        "workflow_id": workflow_id, "workflow_run_id": run_id, "workflow_error": error,
    })
    return PersistVerificationReportActivityInput(output=output, provenance=VerificationProvenance(
        verification_id=workflow_id, workflow_run_id=run_id, owner_sub=owner))


async def _scheduled(activity_type: str, payload: Any) -> HistoryEvent:
    event = HistoryEvent(event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED)
    attrs = event.activity_task_scheduled_event_attributes
    attrs.activity_type.name = activity_type
    attrs.input.payloads.extend(await pydantic_data_converter.encode([payload]))
    return event


def _handle(events: list[HistoryEvent]) -> MagicMock:
    async def fetch(**_: Any) -> AsyncIterator[HistoryEvent]:
        for event in events:
            yield event
    handle = MagicMock()
    handle.fetch_history_events = fetch
    return handle


CLIENT = SimpleNamespace(data_converter=pydantic_data_converter)


def test_reconciler_and_backfill_do_not_load_the_api() -> None:
    """The CronJob image runs these scripts; they must not import FastAPI's application module."""
    probe = ("import sys, scripts.reconcile_verification_reports, scripts.backfill_verification_reports; "
             "assert 'biosim_server.api.main' not in sys.modules, 'api.main was imported'")
    result = subprocess.run([sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_the_last_persist_input_is_the_computed_output() -> None:
    first, last = _computed(error="first attempt"), _computed(error=None)
    events = [HistoryEvent(event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED),
              await _scheduled("generate_statistics_activity_v2", {"ignored": True}),
              await _scheduled(history.PERSIST_ACTIVITY_TYPE, first),
              await _scheduled(history.PERSIST_ACTIVITY_TYPE, last)]
    recovered = await computed_output_from_history(CLIENT, _handle(events), _start(),  # type: ignore[arg-type]
                                                   workflow_id="wf", run_id="run")
    assert recovered == last


@pytest.mark.asyncio
async def test_no_persist_input_means_nothing_was_computed() -> None:
    events = [await _scheduled("get_existing_biosim_simulation_run_activity", {"ignored": True})]
    assert await computed_output_from_history(CLIENT, _handle(events), _start(),  # type: ignore[arg-type]
                                              workflow_id="wf", run_id="run") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("computed", [_computed(workflow_id="other"), _computed(run_id="other-run"),
                                      _computed(owner="someone-else")], ids=["workflow", "run", "owner"])
async def test_a_persist_input_for_another_execution_is_rejected(computed: PersistVerificationReportActivityInput) -> None:
    events = [await _scheduled(history.PERSIST_ACTIVITY_TYPE, computed)]
    with pytest.raises(HistoryIdentityMismatch):
        await computed_output_from_history(CLIENT, _handle(events), _start(),  # type: ignore[arg-type]
                                           workflow_id="wf", run_id="run")


@pytest.mark.asyncio
async def test_history_scan_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(history, "HISTORY_MAX_EVENTS", 3)
    events = [HistoryEvent(event_type=EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED) for _ in range(4)]
    with pytest.raises(HistoryTooLarge):
        await computed_output_from_history(CLIENT, _handle(events), _start(),  # type: ignore[arg-type]
                                           workflow_id="wf", run_id="run")


def _content_digest(output: VerifyWorkflowOutput) -> str:
    return encode_report(output.model_dump(mode="json"))[3]


def test_terminal_failure_depends_only_on_history_facts() -> None:
    closed = datetime(2026, 10, 30, 12, tzinfo=UTC)
    one = terminal_failure_output(_start(), "wf", "run", WorkflowExecutionStatus.TERMINATED, closed)
    # Same instant in another zone: the rendering is UTC, so the content is identical.
    two = terminal_failure_output(_start(), "wf", "run", WorkflowExecutionStatus.TERMINATED,
                                  closed.astimezone(tz=None))
    assert _content_digest(one) == _content_digest(two)
    assert one.workflow_error == "Verification workflow ended with status TERMINATED"
    assert one.timestamp == "2026-10-30T12:00:00+00:00"
    assert one.owner_sub == "owner" and one.compare_settings == _start().compare_settings
    later = terminal_failure_output(_start(), "wf", "run", WorkflowExecutionStatus.TERMINATED,
                                    closed + timedelta(seconds=1))
    assert _content_digest(later) != _content_digest(one)


@pytest.mark.asyncio
async def test_provenance_is_outside_the_content_hash(mongo_test_client: Any) -> None:
    """Two writers may describe themselves differently; only the report content must agree."""
    store = VerificationReportStore(mongo_test_client)
    await store.ensure_indexes()
    output = terminal_failure_output(_start(owner=None), "wf-provenance", "run",
                                     WorkflowExecutionStatus.CANCELED, datetime(2026, 10, 30, tzinfo=UTC))
    try:
        first = await store.save(output, VerificationProvenance(
            verification_id="wf-provenance", workflow_run_id="run", owner_sub=None,
            origin="terminal_execution_failure", computed_at=datetime(2026, 10, 30, tzinfo=UTC)))
        second = await store.save(output, VerificationProvenance(
            verification_id="wf-provenance", workflow_run_id="run", owner_sub=None,
            origin="temporal_backfill", computed_at=datetime(2026, 11, 1, tzinfo=UTC), comparator_version="other"))
        assert second.content_sha256 == first.content_sha256
        assert second.provenance.origin == "terminal_execution_failure"  # first writer wins
    finally:
        await store.delete_all()
