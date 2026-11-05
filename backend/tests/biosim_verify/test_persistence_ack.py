"""Row problems can be acknowledged without deleting or changing a report (I-5, WP-6b)."""
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from biosim_server.biosim_verify.database import (
    BLOCKED_PERSISTENCE_ERROR,
    VerificationDatabaseServiceMongo,
    persistence_issue_after,
)
from biosim_server.biosim_verify.models import ReportPersistenceState, VerificationRecord
from scripts import reconcile_verification_reports as job
from scripts.ack_verification_persistence import acknowledge, main
from tests.biosim_verify.test_reconcile_job import _args, _events, _job, _temporal
from tests.biosim_verify.test_report_recovery import _record

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


@pytest_asyncio.fixture
async def ledger(mongo_test_client: Any) -> AsyncIterator[VerificationDatabaseServiceMongo]:
    service = VerificationDatabaseServiceMongo(mongo_test_client)
    await service.ensure_indexes()
    await service.delete_all_verifications()
    yield service
    await service.delete_all_verifications()


async def _insert(ledger: VerificationDatabaseServiceMongo, *, state: ReportPersistenceState,
                  error: str | None, workflow_id: str = "wf") -> VerificationRecord:
    row = _record().model_copy(update={"workflow_id": workflow_id, "persistence_state": state,
                                       "persistence_error": error})
    await ledger.insert_verification(row)
    return row


async def _ack(ledger: VerificationDatabaseServiceMongo, state: ReportPersistenceState = ReportPersistenceState.BLOCKED,
               **kwargs: Any) -> int:
    return await acknowledge(ledger, workflow_id="wf", expect_state=state, by="operator",
                             reason="known upstream encoding bug", apply=True, **kwargs)


async def _reconcile(ledger: Any, caplog: pytest.LogCaptureFixture, *, outcome: str = "integrity_conflict",
                     temporal: Any = None, **args: Any) -> tuple[dict[str, Any], list[str], AsyncMock]:
    caplog.clear()
    with _job(ledger, outcome=outcome, temporal=temporal) as process, caplog.at_level(logging.INFO):
        assert await job.run(_args(**args)) == 0
    events = _events(caplog)
    return events[0], [event["event"] for event in events], process


@pytest.mark.asyncio
async def test_a_blocked_row_alerts_until_it_is_acknowledged(
    ledger: VerificationDatabaseServiceMongo, caplog: pytest.LogCaptureFixture,
) -> None:
    await _insert(ledger, state=ReportPersistenceState.BLOCKED, error=BLOCKED_PERSISTENCE_ERROR)
    for _ in range(2):  # one attention event per run, every run
        summary, events, _ = await _reconcile(ledger, caplog)
        assert events == ["verification_reconcile_summary", "verification_persistence_attention"]
        assert summary["blocked"] == 1 and summary["attention"] == 1

    assert await _ack(ledger) == 0
    summary, events, process = await _reconcile(ledger, caplog)
    assert events == ["verification_reconcile_summary"]
    assert summary["acknowledged"] == 1 and summary["attention"] == 0 and summary["scanned"] == 1
    process.assert_not_awaited()  # skipped, not retried


@pytest.mark.asyncio
async def test_a_changed_error_invalidates_the_acknowledgement(
    ledger: VerificationDatabaseServiceMongo, caplog: pytest.LogCaptureFixture,
) -> None:
    await _insert(ledger, state=ReportPersistenceState.BLOCKED, error=BLOCKED_PERSISTENCE_ERROR)
    assert await _ack(ledger) == 0
    await ledger._collection.update_one({"workflow_id": "wf"}, {"$set": {"persistence_error": "A new failure"}})
    row = await ledger.get_verification("wf")
    assert row is not None and row.persistence_ack is not None and not row.persistence_acknowledged
    summary, events, process = await _reconcile(ledger, caplog, outcome="dependency_error")
    assert events[-1] == "verification_persistence_attention"
    assert summary["acknowledged"] == 0 and summary["attention"] == 1
    process.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["saved", "integrity_conflict", "permanent_failure", "dependency_error"])
async def test_include_acknowledged_retries_the_row(
    ledger: VerificationDatabaseServiceMongo, caplog: pytest.LogCaptureFixture, outcome: str,
) -> None:
    """After a fix release the row is retried.

    Failing again, permanently or for a dependency, leaves the acknowledged problem as it was: the
    row stays BLOCKED with the same error, so it stays acknowledged and silent.
    """
    await _insert(ledger, state=ReportPersistenceState.BLOCKED, error=BLOCKED_PERSISTENCE_ERROR)
    assert await _ack(ledger) == 0
    summary, _, process = await _reconcile(ledger, caplog, outcome=outcome, include_acknowledged=True)
    process.assert_awaited_once()
    assert summary["attention"] == 0
    assert summary["acknowledged"] == int(outcome != "saved")
    row = await ledger.get_verification("wf")
    assert row is not None and row.persistence_state == ReportPersistenceState.BLOCKED
    assert row.persistence_error == BLOCKED_PERSISTENCE_ERROR and row.persistence_acknowledged


@pytest.mark.asyncio
async def test_a_dependency_error_never_demotes_a_blocked_row(
    ledger: VerificationDatabaseServiceMongo, caplog: pytest.LogCaptureFixture,
) -> None:
    """A timeout or an outage while retrying a blocked row says nothing new about it."""
    await _insert(ledger, state=ReportPersistenceState.BLOCKED, error=BLOCKED_PERSISTENCE_ERROR)
    summary, events, _ = await _reconcile(ledger, caplog, outcome="dependency_error")
    assert events[-1] == "verification_persistence_attention"
    assert summary["blocked"] == 1 and summary["dependency_error"] == 1
    row = await ledger.get_verification("wf")
    assert row is not None and row.persistence_state == ReportPersistenceState.BLOCKED
    assert row.persistence_error == BLOCKED_PERSISTENCE_ERROR
    assert await _ack(ledger) == 0  # the runbook's --expect-state blocked still applies


@pytest.mark.asyncio
async def test_a_permanent_failure_found_by_the_reconciler_blocks_the_row(
    ledger: VerificationDatabaseServiceMongo, caplog: pytest.LogCaptureFixture,
) -> None:
    await _insert(ledger, state=ReportPersistenceState.PENDING, error=None)
    summary, events, _ = await _reconcile(ledger, caplog, outcome="permanent_failure")
    assert events[-1] == "verification_persistence_attention"
    assert summary["permanent_failure"] == 1 and summary["attention"] == 1
    row = await ledger.get_verification("wf")
    assert row is not None and row.persistence_state == ReportPersistenceState.BLOCKED
    assert row.persistence_error == BLOCKED_PERSISTENCE_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("state, blocked, expected", [
    (ReportPersistenceState.PENDING, False, ReportPersistenceState.PENDING),
    (ReportPersistenceState.PENDING, True, ReportPersistenceState.BLOCKED),
    (ReportPersistenceState.BLOCKED, False, ReportPersistenceState.BLOCKED),
    (ReportPersistenceState.BLOCKED, True, ReportPersistenceState.BLOCKED),
    (ReportPersistenceState.STORED, False, ReportPersistenceState.STORED),
    (ReportPersistenceState.STORED, True, ReportPersistenceState.STORED),
    (ReportPersistenceState.UNAVAILABLE, False, ReportPersistenceState.PENDING),
], ids=str)
async def test_marking_an_issue_matches_its_prediction(
    ledger: VerificationDatabaseServiceMongo, state: ReportPersistenceState, blocked: bool,
    expected: ReportPersistenceState,
) -> None:
    """The ledger write and persistence_issue_after, which the reconciler's acknowledgement check uses, agree."""
    await _insert(ledger, state=state, error="before")
    await ledger.mark_persistence_issue("wf", blocked)
    row = await ledger.get_verification("wf")
    assert row is not None
    assert (row.persistence_state, row.persistence_error) == persistence_issue_after(state, "before", blocked)
    assert row.persistence_state == expected


@pytest.mark.asyncio
async def test_an_acknowledged_stale_row_is_still_processed_but_silent(
    ledger: VerificationDatabaseServiceMongo, caplog: pytest.LogCaptureFixture,
) -> None:
    await _insert(ledger, state=ReportPersistenceState.PENDING, error=None)
    stale = _temporal(started=datetime.now(UTC) - timedelta(hours=30))
    summary, events, _ = await _reconcile(ledger, caplog, outcome="pending", temporal=stale)
    assert summary["stale_running"] == 1 and events[-1] == "verification_persistence_attention"

    assert await _ack(ledger, ReportPersistenceState.PENDING) == 0
    summary, events, process = await _reconcile(ledger, caplog, outcome="pending", temporal=stale)
    process.assert_awaited_once()  # still processed, so it is saved when it finishes
    assert summary["stale_running"] == 1 and summary["acknowledged"] == 1 and summary["attention"] == 0
    assert events == ["verification_reconcile_summary"]


# --- The acknowledgement script ------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_prints_the_row_and_writes_nothing(
    ledger: VerificationDatabaseServiceMongo, capsys: pytest.CaptureFixture[str],
) -> None:
    await _insert(ledger, state=ReportPersistenceState.BLOCKED, error=BLOCKED_PERSISTENCE_ERROR)
    before = await ledger._collection.find_one({"workflow_id": "wf"}, projection={"_id": 0})
    assert await acknowledge(ledger, workflow_id="wf", expect_state=ReportPersistenceState.BLOCKED,
                             by="operator", reason="investigating") == 0
    assert await ledger._collection.find_one({"workflow_id": "wf"}, projection={"_id": 0}) == before
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["event"] for line in lines] == ["row", "would_acknowledge"]
    assert lines[0]["persistence_error"] == BLOCKED_PERSISTENCE_ERROR
    assert "owner" not in json.dumps(lines) and "workflow_input" not in json.dumps(lines)


@pytest.mark.asyncio
async def test_acknowledging_changes_only_the_acknowledgement(ledger: VerificationDatabaseServiceMongo) -> None:
    await _insert(ledger, state=ReportPersistenceState.BLOCKED, error=BLOCKED_PERSISTENCE_ERROR)
    before = await ledger._collection.find_one({"workflow_id": "wf"}, projection={"_id": 0})
    assert await _ack(ledger) == 0
    after = await ledger._collection.find_one({"workflow_id": "wf"}, projection={"_id": 0})
    assert after is not None and before is not None
    ack = after["persistence_ack"]
    assert {k: v for k, v in after.items() if k != "persistence_ack"} == {
        k: v for k, v in before.items() if k != "persistence_ack"}
    assert ack["state"] == "blocked" and ack["error"] == BLOCKED_PERSISTENCE_ERROR
    assert ack["reason"] == "known upstream encoding bug" and ack["acknowledged_by"] == "operator"
    row = await ledger.get_verification("wf")
    assert row is not None and row.persistence_acknowledged

    assert await acknowledge(ledger, workflow_id="wf", expect_state=ReportPersistenceState.BLOCKED,
                             by="operator", clear=True) == 0  # dry-run
    assert (await ledger.get_verification("wf")).persistence_acknowledged  # type: ignore[union-attr]
    assert await acknowledge(ledger, workflow_id="wf", expect_state=ReportPersistenceState.BLOCKED,
                             by="operator", clear=True, apply=True) == 0
    row = await ledger.get_verification("wf")
    assert row is not None and row.persistence_ack is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state, expect, message", [
    (ReportPersistenceState.STORED, ReportPersistenceState.BLOCKED, "the report is stored"),
    (ReportPersistenceState.PENDING, ReportPersistenceState.BLOCKED, "the row is pending, not blocked"),
    (None, ReportPersistenceState.BLOCKED, "no such verification"),
])
async def test_the_script_refuses_rows_it_must_not_touch(
    ledger: VerificationDatabaseServiceMongo, capsys: pytest.CaptureFixture[str],
    state: ReportPersistenceState | None, expect: ReportPersistenceState, message: str,
) -> None:
    if state is not None:
        await _insert(ledger, state=state, error=None)
    before = await ledger._collection.find_one({"workflow_id": "wf"}, projection={"_id": 0})
    assert await acknowledge(ledger, workflow_id="wf", expect_state=expect, by="operator",
                             reason="x", apply=True) == 1
    assert await ledger._collection.find_one({"workflow_id": "wf"}, projection={"_id": 0}) == before
    refusal = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert refusal["event"] == "refused" and refusal["reason"].startswith(message)


@pytest.mark.asyncio
async def test_a_row_that_changes_before_the_write_is_refused(
    ledger: VerificationDatabaseServiceMongo, capsys: pytest.CaptureFixture[str],
) -> None:
    stale = await _insert(ledger, state=ReportPersistenceState.BLOCKED, error=BLOCKED_PERSISTENCE_ERROR)
    await ledger._collection.update_one({"workflow_id": "wf"}, {"$set": {"persistence_error": "A new failure"}})
    with patch.object(ledger, "get_verification", new=AsyncMock(return_value=stale)):
        assert await _ack(ledger) == 1
    row = await ledger.get_verification("wf")
    assert row is not None and row.persistence_ack is None
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["reason"] == "the row changed since it was read"


def test_reason_is_required_to_acknowledge() -> None:
    with pytest.raises(SystemExit):
        main(["--workflow-id", "wf", "--expect-state", "blocked", "--by", "operator"])
    with pytest.raises(SystemExit):
        main(["--workflow-id", "wf", "--expect-state", "stored", "--by", "operator", "--clear"])
