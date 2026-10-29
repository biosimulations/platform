"""Bounded verification report reconciler.

The command is intentionally dry-run by default.  Production scheduling can
invoke it repeatedly; saved reports are immutable and therefore safe to retry.
"""
import argparse
import asyncio
import json
import logging
import hashlib
from uuid import uuid4
from datetime import UTC, datetime

from biosim_server.dependencies import (
    get_temporal_client, get_verification_database_service, get_verification_report_store,
    init_standalone, shutdown_standalone,
)
from biosim_server.biosim_verify.report_recovery import process_record
from biosim_server.config import get_settings


async def reconcile(limit: int, apply: bool, *, max_seconds: float = 150,
                    max_records: int = 10000) -> dict[str, int]:
    ledger = get_verification_database_service()
    store = get_verification_report_store()
    if ledger is None or store is None:
        raise RuntimeError("verification persistence is unavailable")
    if not 1 <= limit <= 1000 or max_records < 1 or max_seconds <= 0:
        raise ValueError("invalid reconciliation budget")
    counters = {"scanned": 0, "saved": 0, "already_present": 0, "pending": 0,
                "missing_history": 0, "integrity_conflict": 0, "dependency_error": 0,
                "stale_pending": 0, "blocked": 0, "scan_partial": 0}
    temporal = get_temporal_client()
    settings = get_settings()
    key = settings.temporal_namespace + ":" + hashlib.sha256(settings.temporal_service_url.encode()).hexdigest()
    owner = uuid4().hex
    after_created = None
    after_id = None
    if apply:
        cursor = await ledger.acquire_persistence_scan(key, owner)
        if cursor is None:
            raise RuntimeError("Another reconciliation holds the lease")
        after_created, after_id = cursor
    deadline = asyncio.get_running_loop().time() + max_seconds
    try:
        while True:
            candidates = await ledger.list_persistence_candidates(limit, after_created, after_id)
            for row in candidates:
                if counters["scanned"] >= max_records or asyncio.get_running_loop().time() >= deadline:
                    counters["scan_partial"] = 1
                    break
                try:
                    async with asyncio.timeout(45):
                        outcome = await process_record(record=row, ledger=ledger, store=store, temporal=temporal, apply=apply)
                except TimeoutError:
                    outcome = "dependency_error"
                counters[outcome] += 1
                if outcome in {"dependency_error", "integrity_conflict"} and apply:
                    await ledger.mark_persistence_issue(row.workflow_id, outcome == "integrity_conflict")
                if outcome == "pending":
                    created = row.created.replace(tzinfo=UTC) if row.created.tzinfo is None else row.created
                    if (datetime.now(UTC) - created).total_seconds() >= 3600:
                        counters["stale_pending"] += 1
                if row.persistence_state == "blocked" and outcome not in {"saved", "already_present"}:
                    counters["blocked"] += 1
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
    counters.update({"apply": int(apply), "limit": limit})
    attention = any(counters[k] for k in ("dependency_error", "integrity_conflict", "stale_pending", "blocked"))
    event = {"event": "verification_persistence_attention" if attention else "verification_reconcile_success", **counters}
    logging.getLogger(__name__).log(logging.ERROR if attention else logging.INFO, json.dumps(event, sort_keys=True))
    return counters


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="publish repairs (dry-run is the default)")
    args = parser.parse_args()
    async def run() -> None:
        await init_standalone()
        try:
            counts = await reconcile(args.limit, args.apply)
            print(counts)
            if any(counts[k] for k in ("dependency_error", "integrity_conflict", "stale_pending", "blocked")):
                raise RuntimeError("Reconciliation requires operator attention")
        finally:
            await shutdown_standalone()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
