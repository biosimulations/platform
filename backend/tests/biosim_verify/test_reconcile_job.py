"""The reconciler's exit status reflects job health; row problems are attention (I-5, WP-6)."""
import argparse
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.api.common.v1 import ActivityType
from temporalio.api.workflow.v1 import PendingActivityInfo
from temporalio.client import WorkflowExecutionStatus

from biosim_server.biosim_verify.database import LeaseLost
from biosim_server.biosim_verify.history import PERSIST_ACTIVITY_TYPE
from biosim_server.biosim_verify.models import ReportPersistenceState, VerificationRecord
from biosim_server.biosim_verify.report_recovery import minimum_retry_elapsed, running_state
from scripts import reconcile_verification_reports as job
from tests.biosim_verify.test_report_recovery import _record

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

SCRIPT = "scripts.reconcile_verification_reports"


def _args(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {"limit": 100, "apply": True, "stale_persist_after": 3600.0,
                              "stale_running_after": 86400.0, "include_acknowledged": False}
    values.update(overrides)
    return argparse.Namespace(**values)


def _ledger(rows: list[VerificationRecord]) -> AsyncMock:
    ledger = AsyncMock()
    ledger.list_persistence_candidates.return_value = rows
    ledger.acquire_persistence_scan.return_value = (None, None)
    return ledger


@contextmanager
def _job(ledger: Any, *, outcome: str = "pending", temporal: Any = None,
         init: AsyncMock | None = None, temporal_down: bool = False) -> Iterator[AsyncMock]:
    """Run the job against *ledger*.  Temporal is reachable, with nothing running, unless given or down."""
    process = AsyncMock(return_value=outcome)
    if temporal is None and not temporal_down:
        temporal = _temporal(started=datetime.now(UTC), status=WorkflowExecutionStatus.COMPLETED)
    with patch(f"{SCRIPT}.init_standalone", new=init or AsyncMock()), \
         patch(f"{SCRIPT}.shutdown_standalone", new=AsyncMock()), \
         patch(f"{SCRIPT}.get_verification_database_service", return_value=ledger), \
         patch(f"{SCRIPT}.get_verification_report_store", return_value=MagicMock()), \
         patch(f"{SCRIPT}.get_temporal_client", return_value=None if temporal_down else temporal), \
         patch(f"{SCRIPT}.process_record", new=process):
        yield process


def _events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(record.message) for record in caplog.records if record.name == SCRIPT]


def _blocked() -> VerificationRecord:
    return _record().model_copy(update={
        "persistence_state": ReportPersistenceState.BLOCKED,
        "persistence_error": "Report integrity/encoding failure",
    })


@pytest.mark.asyncio
async def test_a_blocked_row_is_attention_not_a_job_failure(caplog: pytest.LogCaptureFixture) -> None:
    with _job(_ledger([_blocked()]), outcome="integrity_conflict"), caplog.at_level(logging.INFO):
        status = await job.run(_args())
    assert status == 0
    events = _events(caplog)
    assert [event["event"] for event in events] == [
        "verification_reconcile_summary", "verification_persistence_attention",
    ]
    summary, attention = events
    assert summary["blocked"] == 1 and summary["attention"] == 1
    assert attention["blocked"] == 1
    assert [record.levelno for record in caplog.records if record.name == SCRIPT] == [logging.INFO, logging.ERROR]


@pytest.mark.asyncio
async def test_a_healthy_scan_logs_only_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    with _job(_ledger([_record()]), outcome="saved"), caplog.at_level(logging.INFO):
        status = await job.run(_args())
    assert status == 0
    [summary] = _events(caplog)
    assert summary["event"] == "verification_reconcile_summary"
    assert summary["saved"] == 1 and summary["attention"] == 0


@pytest.mark.asyncio
async def test_a_busy_lease_exits_zero_with_a_summary(caplog: pytest.LogCaptureFixture) -> None:
    ledger = _ledger([_record()])
    ledger.acquire_persistence_scan.return_value = None
    with _job(ledger) as process, caplog.at_level(logging.INFO):
        status = await job.run(_args())
    assert status == 0
    [summary] = _events(caplog)
    assert summary["event"] == "verification_reconcile_summary"
    assert summary["skipped"] == "lease_busy" and summary["scanned"] == 0
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lost_lease_fails_the_job(caplog: pytest.LogCaptureFixture) -> None:
    ledger = _ledger([_record()])
    ledger.checkpoint_persistence_scan.side_effect = LeaseLost("Reconciliation lease was lost")
    with _job(ledger), caplog.at_level(logging.INFO):
        status = await job.run(_args())
    assert status == 1
    assert _events(caplog)[-1] == {"event": "verification_reconcile_job_failed", "reason": "lease_lost",
                                   "error": "LeaseLost"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, reason", [
    ("init", "initialization"), ("unconfigured", "initialization"),
    ("lease_unreachable", "initialization"), ("scan", "unexpected"),
])
async def test_initialization_and_unexpected_failures_exit_one(
    caplog: pytest.LogCaptureFixture, failure: str, reason: str,
) -> None:
    ledger: Any = _ledger([_record()])
    init = AsyncMock()
    if failure == "init":
        init.side_effect = OSError("mongodb://user:secret@host unreachable")
    elif failure == "unconfigured":
        ledger = None
    elif failure == "lease_unreachable":
        ledger.acquire_persistence_scan.side_effect = OSError("mongodb://user:secret@host unreachable")
    else:
        ledger.list_persistence_candidates.side_effect = KeyError("boom")
    with _job(ledger, init=init), caplog.at_level(logging.INFO):
        status = await job.run(_args())
    assert status == 1
    failed = _events(caplog)[-1]
    assert failed["event"] == "verification_reconcile_job_failed" and failed["reason"] == reason
    assert "secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [True, False])
async def test_an_unreachable_temporal_fails_the_job_and_touches_no_row(
    caplog: pytest.LogCaptureFixture, apply: bool,
) -> None:
    """init_standalone tolerates a Temporal outage for the API's sake; the job must not look healthy (I-5)."""
    ledger = _ledger([_blocked()])
    with _job(ledger, temporal_down=True) as process, caplog.at_level(logging.INFO):
        status = await job.run(_args(apply=apply))
    assert status == 1
    # No heartbeat either, so the absence policy fires too.
    assert _events(caplog) == [{"event": "verification_reconcile_job_failed", "reason": "temporal_unavailable",
                                "error": "TemporalUnavailable"}]
    process.assert_not_awaited()
    ledger.acquire_persistence_scan.assert_not_awaited()
    ledger.mark_persistence_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_takes_no_lease_and_reports_the_same_events(caplog: pytest.LogCaptureFixture) -> None:
    ledger = _ledger([_blocked()])
    with _job(ledger, outcome="integrity_conflict"), caplog.at_level(logging.INFO):
        status = await job.run(_args(apply=False))
    assert status == 0
    ledger.acquire_persistence_scan.assert_not_awaited()
    ledger.mark_persistence_issue.assert_not_awaited()
    assert [event["event"] for event in _events(caplog)][-1] == "verification_persistence_attention"


def test_cli_defaults_match_the_plan() -> None:
    with patch(f"{SCRIPT}.run", new=AsyncMock(return_value=0)) as run:
        assert job.main(["--apply"]) == 0
    assert run.await_args is not None
    args = run.await_args.args[0]
    assert args.apply and args.limit == 100
    assert args.stale_persist_after == 3600 and args.stale_running_after == 86400
    assert not args.include_acknowledged
    with pytest.raises(SystemExit):
        job.main(["--stale-running-after", "0"])


# --- Precise staleness from pending activities (6c) ---------------------------------------


def _temporal(*, attempt: int | None = None, started: datetime,
              status: WorkflowExecutionStatus = WorkflowExecutionStatus.RUNNING) -> MagicMock:
    pending = []
    if attempt is not None:
        pending.append(PendingActivityInfo(activity_type=ActivityType(name=PERSIST_ACTIVITY_TYPE), attempt=attempt))
    desc = SimpleNamespace(status=status, start_time=started,
                           raw_description=SimpleNamespace(pending_activities=pending))
    temporal = MagicMock()
    temporal.get_workflow_handle.return_value.describe = AsyncMock(return_value=desc)
    return temporal


def test_retry_age_is_the_sum_of_the_policy_intervals() -> None:
    # PERSIST_RETRY: 1s initial, doubling, 5 minute ceiling.
    assert minimum_retry_elapsed(1) == timedelta(0)
    assert minimum_retry_elapsed(3) == timedelta(seconds=1 + 2)
    assert minimum_retry_elapsed(10) == timedelta(seconds=511)
    assert minimum_retry_elapsed(12) == timedelta(seconds=511 + 300 + 300)


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt, kind", [(None, "computing"), (2, "computing"), (3, "persist_retrying")])
async def test_running_executions_are_classified_from_pending_activities(attempt: int | None, kind: str) -> None:
    now = datetime.now(UTC)
    state = await running_state(_temporal(attempt=attempt, started=now - timedelta(hours=2)), _record(), now=now)
    assert state is not None and state.kind == kind
    if kind == "computing":
        assert state.age == timedelta(hours=2)
    assert await running_state(_temporal(started=now, status=WorkflowExecutionStatus.COMPLETED),
                               _record(), now=now) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt, hours_running, stale", [
    (3, 30, None),                     # persist retrying for seconds: no alert, however old the workflow
    (25, 1, "stale_persist"),          # retrying for over an hour: storage is broken
    (None, 23, None),                  # a long comparison is not stale before 24h
    (None, 25, "stale_running"),
])
async def test_staleness_thresholds(caplog: pytest.LogCaptureFixture, attempt: int | None,
                                    hours_running: int, stale: str | None) -> None:
    started = datetime.now(UTC) - timedelta(hours=hours_running)
    temporal = _temporal(attempt=attempt, started=started)
    with _job(_ledger([_record()]), outcome="pending", temporal=temporal), caplog.at_level(logging.INFO):
        assert await job.run(_args()) == 0
    summary = _events(caplog)[0]
    assert summary["pending"] == 1
    assert summary["persist_retrying" if attempt else "computing"] == 1
    assert summary["stale_persist"] == int(stale == "stale_persist")
    assert summary["stale_running"] == int(stale == "stale_running")
    assert summary["attention"] == int(stale is not None)
    assert (_events(caplog)[-1]["event"] == "verification_persistence_attention") is (stale is not None)
