"""Checkpointed ledger and retained-history backfill; dry-run unless --apply."""
import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from biosim_server.dependencies import (
    init_standalone, shutdown_standalone, get_temporal_client,
    get_verification_database_service, get_verification_report_store,
)
from biosim_server.config import get_settings
from biosim_server.biosim_verify.report_recovery import process_record, inventory_retained_executions


def aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("time bounds must include a timezone")
    return parsed.astimezone(UTC)


async def run(*, checkpoint: Path, apply: bool, limit: int, inventory: bool = False,
              since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    settings = get_settings()
    mode = "apply" if apply else "dry-run"
    start = since or datetime(1970, 1, 1, tzinfo=UTC)
    target = {"temporal": settings.temporal_service_url, "database": settings.mongodb_database,
              "mongo_fingerprint": hashlib.sha256(settings.mongodb_uri.encode()).hexdigest(),
              "namespace": settings.temporal_namespace}
    state: dict[str, Any] = {}
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if state.get("mode") != mode:
            raise ValueError("Use a separate checkpoint for dry-run and apply")
        if state.get("target") != target or state.get("inventory_enabled") != inventory:
            raise ValueError("Checkpoint belongs to a different environment/namespace or inventory mode")
        if state.get("since") != start.isoformat() or state.get("requested_until") != (until.isoformat() if until else None):
            raise ValueError("Checkpoint has different time bounds")
    if not state or state.get("complete"):
        end = until or datetime.now(UTC)
        if start >= end:
            raise ValueError("since must precede until")
        state = {"version": 2, "mode": mode, "target": target, "inventory_enabled": inventory,
                 "since": start.isoformat(), "until": end.isoformat(),
                 "requested_until": until.isoformat() if until else None,
                 "last_created": None, "last_workflow_id": None, "ledger_done": False,
                 "inventory_done": not inventory, "inventory_token": None, "complete": False,
                 "scanned": 0, "outcomes": {}}
    ledger, store, temporal = get_verification_database_service(), get_verification_report_store(), get_temporal_client()
    if ledger is None or store is None or temporal is None:
        raise RuntimeError("Required verification persistence/Temporal dependency unavailable")
    if not state["ledger_done"]:
        after = aware(state["last_created"]) if state["last_created"] else None
        rows = await ledger.list_persistence_candidates(limit, after, state["last_workflow_id"])
        for record in rows:
            created = record.created.replace(tzinfo=UTC) if record.created.tzinfo is None else record.created
            if start <= created < aware(state["until"]):
                outcome = await process_record(record=record, ledger=ledger, store=store, temporal=temporal, apply=apply)
                state["outcomes"][outcome] = state["outcomes"].get(outcome, 0) + 1
            state["scanned"] += 1
            state["last_created"], state["last_workflow_id"] = created.isoformat(), record.workflow_id
        state["ledger_done"] = len(rows) < limit
    if not state["inventory_done"]:
        assert temporal is not None
        counts, token = await inventory_retained_executions(
            temporal=temporal, ledger=ledger, store=store, apply=apply, limit=limit,
            next_page_token=bytes.fromhex(state["inventory_token"]) if state["inventory_token"] else None,
            since=start, until=aware(state["until"]))
        for key, value in counts.items():
            state["outcomes"][key] = state["outcomes"].get(key, 0) + value
        state["inventory_token"] = token.hex() if token else None
        state["inventory_done"] = token is None
    state["complete"] = state["ledger_done"] and state["inventory_done"]
    if state["complete"]:
        state["last_created"] = state["last_workflow_id"] = None
    descriptor, name = tempfile.mkstemp(prefix=checkpoint.name + ".", dir=checkpoint.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(checkpoint)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps(state, sort_keys=True))
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--since", type=aware)
    parser.add_argument("--until", type=aware)
    parser.add_argument("--max-pages", type=int, default=1, help="bounded pages this invocation; resume with the same checkpoint")
    args = parser.parse_args()
    if args.max_pages < 1:
        parser.error("max-pages must be positive")

    async def wrapped() -> None:
        await init_standalone()
        try:
            for _ in range(args.max_pages):
                state = await run(checkpoint=args.checkpoint, apply=args.apply, limit=args.limit,
                                  inventory=args.inventory, since=args.since, until=args.until)
                if state["complete"]:
                    if any(state["outcomes"].get(k) for k in ("dependency_error", "integrity_conflict", "unknown_ownership", "pending")):
                        raise RuntimeError("Backfill completed with unresolved entries; inspect checkpoint before migration")
                    return
            raise RuntimeError("Backfill paused at page budget; resume before migration")
        finally:
            await shutdown_standalone()
    asyncio.run(wrapped())


if __name__ == "__main__":
    main()
