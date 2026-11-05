"""Acknowledge a verification persistence problem without touching its report.

An acknowledgement silences reconciliation attention for one ledger row while
the row keeps the persistence state and error it acknowledged; a different
failure brings attention back.  It never deletes or changes a report.

Dry-run by default: the command prints the row's persistence fields and the
change it would make.  ``--apply`` writes it.  It refuses stored rows, and rows
that are not in ``--expect-state`` or that change between the read and the write.

    python -m scripts.ack_verification_persistence --workflow-id WF \\
        --expect-state blocked --by alice --reason "upstream fix in 0.11.1" [--apply]
    python -m scripts.ack_verification_persistence --workflow-id WF \\
        --expect-state blocked --by alice --clear [--apply]
"""
import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from biosim_server.biosim_verify.database import VerificationDatabaseService, VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.models import PersistenceAck, ReportPersistenceState, VerificationRecord
from biosim_server.config import get_settings

_ACKNOWLEDGEABLE = [state.value for state in ReportPersistenceState if state != ReportPersistenceState.STORED]


def _print(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def _view(row: VerificationRecord) -> dict[str, Any]:
    # Persistence bookkeeping only: never the owner or the saved request.
    return {
        "workflow_id": row.workflow_id, "verify_type": row.verify_type, "created": row.created,
        "persistence_state": row.persistence_state, "persistence_error": row.persistence_error,
        "persistence_attempted_at": row.persistence_attempted_at,
        "persistence_ack": row.persistence_ack.model_dump() if row.persistence_ack else None,
    }


async def acknowledge(ledger: VerificationDatabaseService, *, workflow_id: str,
                      expect_state: ReportPersistenceState, by: str, reason: str | None = None,
                      clear: bool = False, apply: bool = False) -> int:
    """Acknowledge (or clear) one row's persistence problem; return the exit status."""
    row = await ledger.get_verification(workflow_id)
    if row is None:
        _print("refused", reason="no such verification", workflow_id=workflow_id)
        return 1
    _print("row", **_view(row))
    if row.persistence_state == ReportPersistenceState.STORED:
        _print("refused", reason="the report is stored; there is nothing to acknowledge")
        return 1
    if row.persistence_state != expect_state:
        _print("refused", reason=f"the row is {row.persistence_state}, not {expect_state}")
        return 1
    if clear:
        _print("clear" if apply else "would_clear", workflow_id=workflow_id, by=by)
        if apply and not await ledger.clear_persistence_ack(workflow_id, expect_state):
            _print("refused", reason="the row changed since it was read")
            return 1
        return 0
    if not reason:
        _print("refused", reason="--reason is required to acknowledge")
        return 1
    ack = PersistenceAck(state=row.persistence_state, error=row.persistence_error, reason=reason,
                         acknowledged_by=by, acknowledged_at=datetime.now(UTC))
    _print("acknowledge" if apply else "would_acknowledge", workflow_id=workflow_id, **ack.model_dump())
    if apply and not await ledger.acknowledge_persistence(workflow_id, ack):
        _print("refused", reason="the row changed since it was read")
        return 1
    return 0


async def run(args: argparse.Namespace) -> int:
    client: AsyncIOMotorClient = AsyncIOMotorClient(get_settings().mongodb_uri)
    try:
        return await acknowledge(
            VerificationDatabaseServiceMongo(client), workflow_id=args.workflow_id,
            expect_state=ReportPersistenceState(args.expect_state), by=args.by, reason=args.reason,
            clear=args.clear, apply=args.apply,
        )
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--expect-state", required=True, choices=_ACKNOWLEDGEABLE,
                        help="refuse unless the row is still in this persistence state")
    parser.add_argument("--by", required=True, help="who is acknowledging (recorded for audit)")
    parser.add_argument("--reason", help="why the problem is acknowledged (required unless --clear)")
    parser.add_argument("--clear", action="store_true", help="remove the acknowledgement instead")
    parser.add_argument("--apply", action="store_true", help="write the change (dry-run is the default)")
    args = parser.parse_args(argv)
    if not args.clear and not args.reason:
        parser.error("--reason is required unless --clear is given")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
