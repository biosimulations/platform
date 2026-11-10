"""Bounded verification report reconciler.

The command is intentionally dry-run by default.  Production scheduling can
invoke it repeatedly; saved reports are immutable and therefore safe to retry.

The exit status reflects the job's health, never the rows it found (I-5):

* a scan that ran, complete or budget-limited, exits 0 whatever its rows need.
  It always logs ``verification_reconcile_summary`` (INFO), the heartbeat, and
  logs ``verification_persistence_attention`` (ERROR) when a row needs an
  operator;
* a lease held by another job exits 0 with ``skipped="lease_busy"``; that lease
  expires by itself within five minutes;
* initialization failure, an unreachable Temporal, a lost lease or an
  unexpected exception exits 1 with ``verification_reconcile_job_failed``.  The
  next scheduled run is the retry.

An operator can acknowledge a row's problem with
``scripts.ack_verification_persistence``.  Acknowledged ``blocked`` rows are
skipped and counted as ``acknowledged``; ``--include-acknowledged`` retries them
after a fix release.  Other acknowledged rows are still processed, so they are
saved when they can be, but they are not attention.  An acknowledgement lapses
when the row's state or error changes.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from biosim_server.biosim_verify.database import LeaseLost, persistence_issue_after
from biosim_server.biosim_verify.models import ReportPersistenceState
from biosim_server.biosim_verify.report_recovery import RunningState, process_record, running_state
from biosim_server.config import get_settings
from biosim_server.dependencies import (
    get_temporal_client, get_verification_database_service, get_verification_report_store,
    init_standalone, shutdown_standalone,
)

logger = logging.getLogger(__name__)

# Outcomes that are a row problem for an operator, not a job failure.
_ATTENTION_OUTCOMES = frozenset({"dependency_error", "integrity_conflict", "permanent_failure"})
# The ones retrying cannot fix: the row is marked BLOCKED, as the persist activity would mark it.
_BLOCKING_OUTCOMES = frozenset({"integrity_conflict", "permanent_failure"})
_ROW_BUDGET_SECONDS = 45


class ReconcileSetupError(RuntimeError):
    """The job could not start: persistence is unavailable or misconfigured."""


class TemporalUnavailable(ReconcileSetupError):
    """Temporal could not be reached.

    ``init_standalone`` leaves the client unset instead of failing, so that the
    API can serve saved reports while Temporal is down.  The reconciler cannot
    do its work without Temporal: scanning anyway would mark every candidate as
    a dependency error and still report a healthy run.
    """


def _log(level: int, event: str, fields: dict[str, Any]) -> None:
    logger.log(level, json.dumps({"event": event, **fields}, sort_keys=True))


async def reconcile(limit: int, apply: bool, *, max_seconds: float = 150, max_records: int = 10000,
                    stale_persist_after: timedelta = timedelta(hours=1),
                    stale_running_after: timedelta = timedelta(hours=24),
                    include_acknowledged: bool = False) -> dict[str, Any]:
    """Scan persistence candidates once and return the summary it logged."""
    ledger = get_verification_database_service()
    store = get_verification_report_store()
    if ledger is None or store is None:
        raise ReconcileSetupError("verification persistence is unavailable")
    if not 1 <= limit <= 1000 or max_records < 1 or max_seconds <= 0:
        raise ReconcileSetupError("invalid reconciliation budget")
    counters: dict[str, Any] = {
        "scanned": 0, "saved": 0, "already_present": 0, "pending": 0, "missing_history": 0,
        "integrity_conflict": 0, "permanent_failure": 0, "dependency_error": 0, "blocked": 0, "acknowledged": 0,
        "persist_retrying": 0, "computing": 0, "stale_persist": 0, "stale_running": 0,
        "attention": 0, "scan_partial": 0, "apply": int(apply), "limit": limit,
    }
    temporal = get_temporal_client()
    if temporal is None:
        raise TemporalUnavailable("Temporal is unavailable")
    settings = get_settings()
    key = settings.temporal_namespace + ":" + hashlib.sha256(settings.temporal_service_url.encode()).hexdigest()
    owner = uuid4().hex
    after_created = None
    after_id = None
    if apply:
        try:
            cursor = await ledger.acquire_persistence_scan(key, owner)
        except Exception as exc:
            raise ReconcileSetupError("could not reach the reconciliation lease") from exc
        if cursor is None:
            counters["skipped"] = "lease_busy"
            _log(logging.INFO, "verification_reconcile_summary", counters)
            return counters
        after_created, after_id = cursor

    async def examine(row: Any) -> tuple[str, RunningState | None]:
        outcome = await process_record(record=row, ledger=ledger, store=store, temporal=temporal, apply=apply)
        state = None
        if outcome == "pending" and temporal is not None:
            try:
                state = await running_state(temporal, row, now=datetime.now(UTC))
            except Exception:
                state = None
        return outcome, state

    deadline = asyncio.get_running_loop().time() + max_seconds
    try:
        while True:
            candidates = await ledger.list_persistence_candidates(limit, after_created, after_id)
            for row in candidates:
                if counters["scanned"] >= max_records or asyncio.get_running_loop().time() >= deadline:
                    counters["scan_partial"] = 1
                    break
                if (row.persistence_state == ReportPersistenceState.BLOCKED and row.persistence_acknowledged
                        and not include_acknowledged):
                    counters["acknowledged"] += 1
                    counters["scanned"] += 1
                    after_created, after_id = row.created, row.workflow_id
                    if apply:
                        await ledger.checkpoint_persistence_scan(key, owner, after_created, after_id)
                    continue
                try:
                    async with asyncio.timeout(_ROW_BUDGET_SECONDS):
                        outcome, state = await examine(row)
                except TimeoutError:
                    outcome, state = "dependency_error", None
                counters[outcome] += 1
                needs_attention = outcome in _ATTENTION_OUTCOMES
                if needs_attention and apply:
                    await ledger.mark_persistence_issue(row.workflow_id, outcome in _BLOCKING_OUTCOMES)
                if state is not None:
                    counters[state.kind] += 1
                    threshold = stale_persist_after if state.kind == "persist_retrying" else stale_running_after
                    if state.age >= threshold:
                        counters["stale_persist" if state.kind == "persist_retrying" else "stale_running"] += 1
                        needs_attention = True
                if row.persistence_state == ReportPersistenceState.BLOCKED and outcome not in {"saved", "already_present"}:
                    counters["blocked"] += 1
                    needs_attention = True
                if needs_attention and row.persistence_ack is not None:
                    # The state and error the row has after this run, as mark_persistence_issue records them.
                    after_state, after_error = (
                        persistence_issue_after(row.persistence_state, row.persistence_error,
                                                outcome in _BLOCKING_OUTCOMES)
                        if outcome in _ATTENTION_OUTCOMES else (row.persistence_state, row.persistence_error))
                    if row.persistence_ack.covers(after_state, after_error):
                        counters["acknowledged"] += 1
                        needs_attention = False
                counters["attention"] += int(needs_attention)
                counters["scanned"] += 1
                after_created, after_id = row.created, row.workflow_id
                if apply:
                    await ledger.checkpoint_persistence_scan(key, owner, after_created, after_id)
            if counters["scan_partial"]:
                break
            if len(candidates) < limit:
                after_created = after_id = None
                break
    finally:
        if apply:
            await ledger.checkpoint_persistence_scan(key, owner, after_created, after_id, release=True)
    _log(logging.INFO, "verification_reconcile_summary", counters)
    if counters["attention"]:
        _log(logging.ERROR, "verification_persistence_attention", counters)
    return counters


def _job_failed(reason: str, exc: BaseException) -> int:
    # Only the exception type: messages can carry connection strings or row data.
    _log(logging.ERROR, "verification_reconcile_job_failed", {"reason": reason, "error": type(exc).__name__})
    return 1


async def run(args: argparse.Namespace) -> int:
    """Run one reconciliation and return the process exit status."""
    try:
        await init_standalone()
    except Exception as exc:
        return _job_failed("initialization", exc)
    try:
        await reconcile(args.limit, args.apply,
                        stale_persist_after=timedelta(seconds=args.stale_persist_after),
                        stale_running_after=timedelta(seconds=args.stale_running_after),
                        include_acknowledged=args.include_acknowledged)
        return 0
    except TemporalUnavailable as exc:
        return _job_failed("temporal_unavailable", exc)
    except ReconcileSetupError as exc:
        return _job_failed("initialization", exc)
    except LeaseLost as exc:
        return _job_failed("lease_lost", exc)
    except Exception as exc:
        return _job_failed("unexpected", exc)
    finally:
        try:
            await shutdown_standalone()
        except Exception:
            logger.warning("verification reconcile shutdown did not complete cleanly")


def _positive_seconds(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="publish repairs (dry-run is the default)")
    parser.add_argument("--stale-persist-after", type=_positive_seconds, default=3600, metavar="SECONDS",
                        help="attention when report persistence has been retrying this long (default 1h)")
    parser.add_argument("--stale-running-after", type=_positive_seconds, default=86400, metavar="SECONDS",
                        help="attention when a verification has been computing this long (default 24h)")
    parser.add_argument("--include-acknowledged", action="store_true",
                        help="retry acknowledged blocked rows too, for example after a fix release")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
