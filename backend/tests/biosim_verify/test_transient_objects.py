"""Transient objects are removed after persistence; snapshot capture is bounded (I-6, WP-7c/7e)."""
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from temporalio.testing import ActivityEnvironment

from biosim_server.biosim_runs import Hdf5DataValues
from biosim_server.biosim_verify.activities import (
    PersistVerificationReportActivityInput,
    externalize_statistics,
    persist_verification_report_activity,
)
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.linked_recovery import reconstruction_offer
from biosim_server.biosim_verify.models import ReportPersistenceState, RunData, VerifyWorkflowOutput
from biosim_server.biosim_verify.object_names import (
    ACTIVITY_PREFIX,
    SNAPSHOT_DATA_PREFIX,
    SNAPSHOT_MANIFEST_PREFIX,
    TRANSIENT_PREFIXES,
    snapshot_index_path,
)
from biosim_server.biosim_verify.report_codec import encode_report
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.snapshots import capture_input_snapshots
from biosim_server.common.storage.file_service_local import FileServiceLocal
from biosim_server.config import get_settings
from tests.biosim_verify.test_linked_recovery import _runs
from tests.biosim_verify.test_object_storage import _objects, _statistics, files, store  # noqa: F401
from tests.biosim_verify.test_report_recovery import _record
from tests.biosim_verify.test_report_storage import _output, _provenance

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

ROOT = Path(__file__).resolve().parents[3]

Persist = Callable[[PersistVerificationReportActivityInput], Awaitable[VerifyWorkflowOutput]]


def _data() -> list[RunData]:
    return [RunData(run_id=info.biosim_sim_run.id, dataset_name="ds", var_names=["t", "x"],
                    data=Hdf5DataValues(shape=[2, 3], values=[0, 1, 2, 3, 4, 5])) for info in _runs()]


@pytest_asyncio.fixture
async def persist(
    files: FileServiceLocal, store: VerificationReportStore, mongo_test_client: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Persist]:
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await ledger.ensure_indexes()
    await ledger.delete_all_verifications()
    await ledger.insert_verification(_record().model_copy(update={"workflow_id": _output().workflow_id}))
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_verification_report_store", lambda: store)
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_verification_database_service", lambda: ledger)
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_mongo_client", lambda: mongo_test_client)
    monkeypatch.setattr(get_settings(), "verification_activity_inline_max_bytes", 1)

    async def run(payload: PersistVerificationReportActivityInput) -> VerifyWorkflowOutput:
        result: VerifyWorkflowOutput = await ActivityEnvironment().run(persist_verification_report_activity, payload)
        return result

    yield run
    await ledger.delete_all_verifications()


async def _computed(workflow_id: str) -> PersistVerificationReportActivityInput:
    """What a workflow hands the persist activity: snapshots captured, statistics externalized."""
    assert await capture_input_snapshots(workflow_id, _runs(), _data()) is not None
    statistics = await externalize_statistics(_statistics(), workflow_id)
    assert statistics.reference is not None
    output = _output()
    return PersistVerificationReportActivityInput(
        output=output.model_copy(update={"workflow_results_reference": statistics.reference}),
        provenance=_provenance(output),
    )


@pytest.mark.asyncio
async def test_transient_objects_are_gone_and_every_reference_survives(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
) -> None:
    payload = await _computed(_output().workflow_id)
    assert payload.output.workflow_results_reference is not None
    statistics = payload.output.workflow_results_reference.artifact_reference
    assert await files.exists(statistics) and await files.exists(snapshot_index_path(payload.output.workflow_id))

    result = await persist(payload)
    assert result.persistence_state == ReportPersistenceState.STORED
    assert not await files.exists(statistics)
    assert not await files.exists(snapshot_index_path(payload.output.workflow_id))
    assert _objects(files.base_dir, ACTIVITY_PREFIX) == []

    envelope = await store.find_envelope(payload.output.workflow_id)
    assert envelope is not None
    references = [snapshot.artifact_reference for snapshot in envelope.provenance.input_snapshots]
    references.append(envelope.provenance.snapshot_manifest_reference or "")
    references.append(envelope.artifact_reference or "")
    assert envelope.provenance.snapshot_manifest_reference in _objects(files.base_dir, SNAPSHOT_MANIFEST_PREFIX)
    for reference in references:
        assert await files.exists(reference), reference


@pytest.mark.asyncio
async def test_a_retry_after_cleanup_acknowledges_the_stored_report(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
) -> None:
    """The worker can die after removing transient objects; the retry must not block the report."""
    payload = await _computed(_output().workflow_id)
    first = await persist(payload)
    retried = await persist(payload)
    assert retried.persistence_state == ReportPersistenceState.STORED
    assert retried.report_id == first.report_id
    assert retried.workflow_results is None and retried.workflow_results_reference is None
    saved = await store.get(payload.output.workflow_id)
    assert saved is not None and saved.persistence_state == ReportPersistenceState.STORED


@pytest.mark.asyncio
async def test_a_failed_delete_is_logged_and_not_fatal(
    files: FileServiceLocal, persist: Persist, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = await _computed(_output().workflow_id)

    async def refuse(_: str) -> None:
        raise PermissionError("storage.objects.delete denied")

    monkeypatch.setattr(files, "delete_file", refuse)
    with caplog.at_level(logging.WARNING):
        result = await persist(payload)
    assert result.persistence_state == ReportPersistenceState.STORED
    assert caplog.text.count("verification_transient_cleanup_failed") == 2


# --- Snapshot budget (7e) ------------------------------------------------------------------


def _snapshot_bytes() -> int:
    """The uncompressed size capture_input_snapshots counts for _data()."""
    from biosim_server.biosim_verify.comparison import simulator_label
    from biosim_server.biosim_verify.snapshots import _snapshot_document
    labels = {info.biosim_sim_run.id: simulator_label(info) for info in _runs()}
    return sum(encode_report(_snapshot_document(item, labels[item.run_id]))[2] for item in _data())


@pytest.mark.asyncio
@pytest.mark.parametrize("headroom, captured", [(0, True), (-1, False)])
async def test_the_snapshot_budget_is_enforced(
    files: FileServiceLocal, store: VerificationReportStore, persist: Persist,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, headroom: int, captured: bool,
) -> None:
    monkeypatch.setattr(get_settings(), "verification_snapshot_max_bytes", _snapshot_bytes() + headroom)
    workflow_id = _output().workflow_id
    with caplog.at_level(logging.WARNING):
        snapshots = await capture_input_snapshots(workflow_id, _runs(), _data())
    assert (snapshots is not None) is captured
    assert bool(_objects(files.base_dir, SNAPSHOT_DATA_PREFIX)) is captured  # nothing uploaded over budget
    assert ("verification_snapshot_capture_skipped" in caplog.text) is not captured
    if not captured:
        pointer = await files.get_file_contents(snapshot_index_path(workflow_id))
        assert pointer is not None and json.loads(pointer) == {"skipped": "work_limit"}

    output = _output()
    await persist(PersistVerificationReportActivityInput(output=output, provenance=_provenance(output)))
    stored = await store.find_envelope(workflow_id)
    assert stored is not None
    provenance = stored.provenance
    offer = reconstruction_offer(provenance)
    if captured:
        assert provenance.input_snapshots and provenance.input_snapshots_skipped is None
    else:
        assert provenance.input_snapshots == [] and provenance.input_snapshots_skipped == "work_limit"
        assert not offer.eligible and offer.reason == "work_limit"


def test_the_lifecycle_template_covers_exactly_the_transient_prefixes() -> None:
    lifecycle = json.loads((ROOT / "ops/storage/verification-lifecycle.json").read_text())
    [rule] = lifecycle["rule"]
    assert rule["action"] == {"type": "Delete"}
    assert rule["condition"]["age"] == 30
    assert sorted(rule["condition"]["matchesPrefix"]) == sorted(TRANSIENT_PREFIXES)
