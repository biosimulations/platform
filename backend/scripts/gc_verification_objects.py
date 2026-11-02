"""Reachability garbage collection for long-lived verification objects.

Dry-run by default.  It lists the objects under the collected prefixes
(snapshot data and manifests, externalized reports, and legacy restores) and
marks each one referenced or not:

* referenced: named by a stored report (its payload, snapshots or manifest),
  or reachable from a live snapshot index pointer (a verification still
  persisting);
* a candidate: unreferenced and older than ``--grace`` days (default 7).  That
  includes the UUID-suffixed orphans that earlier attempts left behind.

``--apply`` deletes candidates, oldest first, at most ``--max-deletes``.  It
rebuilds the reference set immediately before deleting, so an object that
became referenced during the listing is kept, and it refuses to delete
anything while a live pointer cannot be resolved.  Nothing a report
references is ever deleted.

Every run writes a 0600 JSON report (``--report``) and prints per-prefix
totals: objects, bytes, unreferenced objects and bytes, and candidates.
Transient prefixes are not collected here; the persist activity and the
storage lifecycle rule remove those.
"""
import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from biosim_server.biosim_verify.object_names import COLLECTED_PREFIXES, SNAPSHOT_INDEX_PREFIX
from biosim_server.biosim_verify.report_codec import checksum, decode_report
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.common.storage.file_service import FileService, ListingItem
from biosim_server.dependencies import (
    get_file_service, get_verification_report_store, init_standalone, shutdown_standalone,
)

_REPORT_LIST_LIMIT = 10_000


@dataclass
class References:
    objects: set[str] = field(default_factory=set)
    reports: int = 0
    live_pointers: int = 0
    unresolved_pointers: list[str] = field(default_factory=list)


async def _resolve_pointer(file_service: FileService, key: str, into: set[str]) -> bool:
    """Add what one live index pointer reaches.  False when it cannot be resolved."""
    raw = await file_service.get_file_contents(key)
    if raw is None:
        return True  # removed since it was listed: it protects nothing now
    try:
        pointer = json.loads(raw)
        if isinstance(pointer, dict) and isinstance(pointer.get("skipped"), str):
            return True
        reference, encoding, digest = pointer["reference"], pointer["encoding"], pointer["sha256"]
        into.add(reference)
        payload = await file_service.get_file_contents(reference)
        if payload is None or checksum(payload, encoding=encoding) != digest:
            return False
        manifest = decode_report(payload, encoding=encoding)
        for snapshot in manifest["snapshots"]:
            into.add(snapshot["artifact_reference"])
        return True
    except Exception:
        return False


async def reference_set(store: VerificationReportStore, file_service: FileService) -> References:
    references = References()
    async for name in store.referenced_objects():
        references.objects.add(name)
        references.reports += 1
    async for item in file_service.iter_objects(SNAPSHOT_INDEX_PREFIX):
        references.live_pointers += 1
        if not await _resolve_pointer(file_service, item.Key, references.objects):
            references.unresolved_pointers.append(item.Key)
    return references


def _aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


async def collect(store: VerificationReportStore, file_service: FileService, *, grace: timedelta, apply: bool,
                  max_deletes: int, now: datetime) -> dict[str, Any]:
    """One collection pass; returns the report it would write."""
    references = await reference_set(store, file_service)
    totals: dict[str, dict[str, int]] = {}
    candidates: list[ListingItem] = []
    for prefix in COLLECTED_PREFIXES:
        total = totals[prefix] = {"objects": 0, "bytes": 0, "unreferenced_objects": 0, "unreferenced_bytes": 0,
                                  "candidates": 0, "candidate_bytes": 0, "deleted": 0}
        async for item in file_service.iter_objects(prefix):
            total["objects"] += 1
            total["bytes"] += item.Size
            if item.Key in references.objects:
                continue
            total["unreferenced_objects"] += 1
            total["unreferenced_bytes"] += item.Size
            if now - _aware(item.LastModified) >= grace:
                total["candidates"] += 1
                total["candidate_bytes"] += item.Size
                candidates.append(item)
    candidates.sort(key=lambda item: (_aware(item.LastModified), item.Key))
    deleted: list[str] = []
    refused = None
    if apply and references.unresolved_pointers:
        refused = "a live snapshot pointer could not be resolved, so its objects are unknown"
    elif apply:
        # References made while listing (a new report, or a verification reusing an old
        # content-addressed object) must protect their objects.
        current = await reference_set(store, file_service)
        if current.unresolved_pointers:
            refused = "a live snapshot pointer could not be resolved, so its objects are unknown"
        else:
            for item in candidates:
                if len(deleted) >= max_deletes:
                    break
                if item.Key in current.objects:
                    continue
                await file_service.delete_file(item.Key)
                deleted.append(item.Key)
                totals[next(p for p in COLLECTED_PREFIXES if item.Key.startswith(p))]["deleted"] += 1
    return {
        "event": "verification_object_gc",
        "generated_at": now.isoformat(), "apply": apply, "grace_days": grace / timedelta(days=1),
        "max_deletes": max_deletes, "totals": totals,
        "referencing_reports": references.reports, "referenced_objects": len(references.objects),
        "live_pointers": references.live_pointers, "unresolved_pointers": references.unresolved_pointers,
        "apply_refused": refused,
        "candidates": [{"key": item.Key, "bytes": item.Size, "last_modified": _aware(item.LastModified).isoformat()}
                       for item in candidates[:_REPORT_LIST_LIMIT]],
        "candidates_truncated": len(candidates) > _REPORT_LIST_LIMIT,
        "deleted": deleted,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Write the report readable by its owner only, never replacing an earlier one."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)


async def run(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    report_path = Path(args.report or f"verification-gc-{now.strftime('%Y%m%dT%H%M%SZ')}.json")
    await init_standalone()
    try:
        store, file_service = get_verification_report_store(), get_file_service()
        if store is None or file_service is None:
            print(json.dumps({"event": "verification_object_gc_failed", "reason": "storage is unavailable"}))
            return 1
        report = await collect(store, file_service, grace=timedelta(days=args.grace), apply=args.apply,
                               max_deletes=args.max_deletes, now=now)
    finally:
        await shutdown_standalone()
    write_report(report_path, report)
    print(json.dumps({key: report[key] for key in ("event", "apply", "totals", "referenced_objects",
                                                   "live_pointers", "unresolved_pointers", "apply_refused")}
                     | {"deleted": len(report["deleted"]), "report": str(report_path)}, sort_keys=True))
    return 1 if report["apply_refused"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grace", type=float, default=7, metavar="DAYS",
                        help="only collect objects older than this (default 7 days)")
    parser.add_argument("--apply", action="store_true", help="delete candidates (dry-run is the default)")
    parser.add_argument("--max-deletes", type=int, default=1000, help="delete at most this many (default 1000)")
    parser.add_argument("--report", help="where to write the 0600 JSON report (default ./verification-gc-<time>.json)")
    args = parser.parse_args(argv)
    if args.grace < 0 or args.max_deletes < 1:
        parser.error("--grace must be >= 0 and --max-deletes >= 1")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
