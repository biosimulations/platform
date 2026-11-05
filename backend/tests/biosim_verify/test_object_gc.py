"""Garbage collection deletes only stale, unreferenced objects; never what a report references (I-6, WP-7d)."""
import argparse
import json
import os
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from biosim_server.biosim_verify.object_names import (
    LEGACY_RESTORE_PREFIX,
    REPORT_PREFIX,
    SNAPSHOT_DATA_PREFIX,
    SNAPSHOT_MANIFEST_PREFIX,
    snapshot_index_path,
)
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.snapshots import capture_input_snapshots, load_verified_snapshots
from biosim_server.common.storage.file_service_local import FileServiceLocal
from scripts import gc_verification_objects as gc
from tests.biosim_verify.test_linked_recovery import _runs
from tests.biosim_verify.test_object_storage import _objects, files, store  # noqa: F401
from tests.biosim_verify.test_transient_objects import Persist, _computed, _data, persist  # noqa: F401
from tests.biosim_verify.test_report_storage import _output

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

GRACE = timedelta(days=7)


def _age(objects: FileServiceLocal, key: str, days: float) -> None:
    moment = time.time() - days * 86400
    os.utime(objects.base_dir / key, (moment, moment))


def _age_everything(objects: FileServiceLocal, days: float) -> None:
    for key in _objects(objects.base_dir):
        _age(objects, key, days)


async def _world(files: FileServiceLocal, persist: Persist) -> dict[str, Any]:  # noqa: F811
    """A stored report, an in-flight verification, and orphans of every kind, all old unless noted."""
    report = _output().workflow_id
    payload = await _computed(report)
    await persist(payload)  # stored: references its report object, snapshots and manifest
    assert await capture_input_snapshots("in-flight", _runs()[:1], _data()[:1]) is not None  # live pointer
    orphans = [
        f"{SNAPSHOT_DATA_PREFIX}{'a' * 64}.json.gz.{'0' * 32}",       # a UUID-suffixed attempt
        f"{SNAPSHOT_MANIFEST_PREFIX}{'b' * 64}.json.gz",               # a manifest whose pointer is gone
        f"{REPORT_PREFIX}lost/{'c' * 64}.json.gz.{'1' * 32}",          # a losing report attempt
        f"{LEGACY_RESTORE_PREFIX}{'d' * 64}.{'2' * 32}",               # an old restore
    ]
    for key in orphans:
        await files.upload_bytes(b"orphan", key)
    young = f"{SNAPSHOT_DATA_PREFIX}{'e' * 64}.json.gz"
    _age_everything(files, days=30)
    await files.upload_bytes(b"fresh", young)  # unreferenced, but inside the grace period
    referenced = [key for key in _objects(files.base_dir) if key not in orphans and key != young]
    return {"report": report, "orphans": orphans, "young": young, "referenced": referenced}


async def _collect(store: VerificationReportStore, files: FileServiceLocal, **kwargs: Any) -> dict[str, Any]:  # noqa: F811
    options: dict[str, Any] = {"grace": GRACE, "apply": False, "max_deletes": 1000, "now": datetime.now(UTC)}
    options.update(kwargs)
    return await gc.collect(store, files, **options)


@pytest.mark.asyncio
async def test_dry_run_lists_only_stale_unreferenced_objects(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
) -> None:
    world = await _world(files, persist)
    before = _objects(files.base_dir)
    report = await _collect(store, files)
    assert sorted(candidate["key"] for candidate in report["candidates"]) == sorted(world["orphans"])
    assert report["deleted"] == [] and _objects(files.base_dir) == before
    assert report["live_pointers"] == 1 and report["unresolved_pointers"] == []
    totals = report["totals"]
    assert totals[SNAPSHOT_DATA_PREFIX]["unreferenced_objects"] == 2  # the old orphan and the young object
    assert totals[SNAPSHOT_DATA_PREFIX]["candidates"] == 1
    assert sum(total["candidates"] for total in totals.values()) == 4
    assert all(total["objects"] >= total["unreferenced_objects"] >= total["candidates"] for total in totals.values())


@pytest.mark.asyncio
async def test_apply_deletes_exactly_the_candidates_and_never_a_reference(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
) -> None:
    world = await _world(files, persist)
    report = await _collect(store, files, apply=True)
    assert sorted(report["deleted"]) == sorted(world["orphans"])
    remaining = _objects(files.base_dir)
    assert all(key in remaining for key in world["referenced"] + [world["young"]])
    assert not any(key in remaining for key in world["orphans"])
    # The stored report and its retained inputs are still whole.
    assert await store.get(world["report"]) is not None
    envelope = await store.find_envelope(world["report"])
    assert envelope is not None and envelope.provenance.input_snapshots
    await load_verified_snapshots(envelope.provenance.input_snapshots)
    # A second pass finds nothing more to do.
    again = await _collect(store, files, apply=True)
    assert again["candidates"] == [] and again["deleted"] == []


@pytest.mark.asyncio
async def test_a_referenced_object_is_never_a_candidate_however_old(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
) -> None:
    world = await _world(files, persist)
    report = await _collect(store, files, grace=timedelta(0), now=datetime.now(UTC) + timedelta(days=3650))
    candidates = {candidate["key"] for candidate in report["candidates"]}
    assert not candidates & set(world["referenced"])


@pytest.mark.asyncio
async def test_deletes_are_capped_oldest_first(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
) -> None:
    world = await _world(files, persist)
    oldest = world["orphans"][2]
    _age(files, oldest, days=90)
    report = await _collect(store, files, apply=True, max_deletes=1)
    assert report["deleted"] == [oldest]
    assert len(report["candidates"]) == 4


@pytest.mark.asyncio
async def test_an_unresolvable_live_pointer_refuses_apply(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
) -> None:
    world = await _world(files, persist)
    await files.upload_bytes(json.dumps({"reference": "verification-snapshots/manifests/missing.json.gz",
                                         "encoding": "verification-json-gzip-v2", "sha256": "0" * 64}).encode(),
                             snapshot_index_path("broken"))
    report = await _collect(store, files, apply=True)
    assert report["unresolved_pointers"] == [snapshot_index_path("broken")]
    assert report["apply_refused"] and report["deleted"] == []
    assert all(key in _objects(files.base_dir) for key in world["orphans"])


@pytest.mark.asyncio
async def test_a_reference_made_during_the_listing_protects_its_object(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
) -> None:
    """A verification can reuse an old content-addressed object while collection runs."""
    world = await _world(files, persist)
    reused = world["orphans"][0]
    real = gc.reference_set
    calls = 0

    async def references(*args: Any) -> gc.References:
        nonlocal calls
        calls += 1
        found = await real(*args)
        if calls > 1:
            found.objects.add(reused)
        return found

    with patch.object(gc, "reference_set", new=references):
        report = await _collect(store, files, apply=True)
    assert reused in {candidate["key"] for candidate in report["candidates"]}
    assert reused not in report["deleted"] and reused in _objects(files.base_dir)
    assert sorted(report["deleted"]) == sorted(world["orphans"][1:])


# --- The command --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_the_command_writes_a_private_report(
    tmp_path: Path, files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
    capsys: pytest.CaptureFixture[str], apply: bool,
) -> None:
    world = await _world(files, persist)
    report_path = tmp_path / "gc.json"
    args = argparse.Namespace(grace=7.0, apply=apply, max_deletes=1000, report=str(report_path))
    with patch.object(gc, "init_standalone", new=AsyncMock()), patch.object(gc, "shutdown_standalone", new=AsyncMock()), \
         patch.object(gc, "get_verification_report_store", return_value=store), \
         patch.object(gc, "get_file_service", return_value=files):
        assert await gc.run(args) == 0
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    written = json.loads(report_path.read_text())
    assert written["apply"] is apply and len(written["deleted"]) == (4 if apply else 0)
    printed = json.loads(capsys.readouterr().out)
    assert printed["totals"] == written["totals"] and printed["report"] == str(report_path)
    with pytest.raises(FileExistsError):  # an earlier report is never replaced
        gc.write_report(report_path, written)
    assert world["orphans"]


def test_cli_defaults_are_dry_run_with_a_week_of_grace() -> None:
    with patch.object(gc, "run", new=AsyncMock(return_value=0)) as run:
        assert gc.main([]) == 0
    assert run.await_args is not None
    args = run.await_args.args[0]
    assert not args.apply and args.grace == 7 and args.max_deletes == 1000
    with pytest.raises(SystemExit):
        gc.main(["--max-deletes", "0"])
