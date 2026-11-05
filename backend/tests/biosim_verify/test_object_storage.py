"""Storage is O(distinct content): deterministic names, create-only writes, a repair path (I-6, WP-7)."""
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

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
from biosim_server.biosim_verify.models import (
    GenerateStatisticsActivityOutput,
    ReportPersistenceState,
    RunData,
    VerifyWorkflowOutput,
)
from biosim_server.biosim_verify.object_names import (
    REPORT_PREFIX,
    SNAPSHOT_DATA_PREFIX,
    SNAPSHOT_MANIFEST_PREFIX,
    holds_content,
    report_artifact_path,
)
from biosim_server.biosim_verify.report_backup import export_bundle, restore_bundle
from biosim_server.biosim_verify.report_codec import encode_report
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.snapshots import capture_input_snapshots
from biosim_server.common.storage.content_addressed import publish_content_addressed
from biosim_server.common.storage.file_service_local import FileServiceLocal
from biosim_server.config import get_settings
from tests.biosim_verify.test_linked_recovery import _runs
from tests.biosim_verify.test_report_recovery import _record
from tests.biosim_verify.test_report_storage import _output, _provenance

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


def _objects(root: Path, prefix: str = "") -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")
                  if path.is_file() and str(path.relative_to(root)).startswith(prefix))


def _statistics() -> GenerateStatisticsActivityOutput:
    return GenerateStatisticsActivityOutput(sims_run_info=[], comparison_statistics={"ds": []})


def _report_digest(output: VerifyWorkflowOutput) -> str:
    """The content digest the store computes: persistence fields are normalized out."""
    normalized = output.model_copy(update={
        "persistence_state": ReportPersistenceState.PENDING, "report_id": None, "report_available": None,
    })
    return encode_report(normalized.model_dump(mode="json"))[3]


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FileServiceLocal:
    objects = FileServiceLocal(tmp_path / "objects")
    for module in ("activities", "snapshots"):
        monkeypatch.setattr(f"biosim_server.biosim_verify.{module}.get_file_service", lambda: objects)
    return objects


@pytest_asyncio.fixture
async def store(mongo_test_client: Any, files: FileServiceLocal) -> AsyncIterator[VerificationReportStore]:
    report_store = VerificationReportStore(mongo_test_client, files)
    await report_store.ensure_indexes()
    await report_store.delete_all()
    report_store._settings.verification_report_inline_max_bytes = 1  # every report is externalized
    yield report_store
    await report_store.delete_all()


# --- publish_content_addressed --------------------------------------------------------------


@pytest.mark.asyncio
async def test_publication_creates_then_reuses(tmp_path: Path) -> None:
    files = FileServiceLocal(tmp_path)
    compressed, encoding, _, digest = encode_report({"value": 1})
    for _ in range(3):
        assert await publish_content_addressed(files, compressed, "c/x.json.gz", holds_content(digest, encoding)) \
            == "c/x.json.gz"
    assert _objects(tmp_path) == ["c/x.json.gz"]


@pytest.mark.asyncio
async def test_a_corrupt_object_at_a_content_address_takes_the_repair_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    files = FileServiceLocal(tmp_path)
    compressed, encoding, _, digest = encode_report({"value": 1})
    await files.upload_bytes(b"not gzip", "c/x.json.gz")
    with caplog.at_level(logging.ERROR):
        reference = await publish_content_addressed(files, compressed, "c/x.json.gz", holds_content(digest, encoding))
    assert reference.startswith("c/x.json.gz.repair-")
    assert await files.get_file_contents(reference) == compressed
    assert await files.get_file_contents("c/x.json.gz") == b"not gzip"  # never overwritten
    assert "verification_object_integrity_conflict" in caplog.text


@pytest.mark.asyncio
async def test_an_object_removed_after_the_create_attempt_is_created_again() -> None:
    files = SimpleNamespace(put_if_absent=AsyncMock(side_effect=[False, True]),
                            get_file_contents=AsyncMock(return_value=None))
    assert await publish_content_addressed(files, b"x", "c/x", lambda _: True) == "c/x"  # type: ignore[arg-type]


# --- I-6: retries and repeated data create no new objects -----------------------------------


@pytest.mark.asyncio
async def test_two_verifications_of_the_same_data_share_one_data_object(files: FileServiceLocal) -> None:
    runs = _runs()
    data = [RunData(run_id=info.biosim_sim_run.id, dataset_name="ds", var_names=["t", "x"],
                    data=Hdf5DataValues(shape=[2, 3], values=[0, 1, 2, 3, 4, 5])) for info in runs]
    first = await capture_input_snapshots("verification-a", runs, data)
    second = await capture_input_snapshots("verification-b", runs, data)
    assert first is not None and second is not None
    assert [s.artifact_reference for s in first] == [s.artifact_reference for s in second]
    assert len(_objects(files.base_dir, SNAPSHOT_DATA_PREFIX)) == len({s.content_sha256 for s in first})
    # A manifest names its workflow, so each verification has its own.
    assert len(_objects(files.base_dir, SNAPSHOT_MANIFEST_PREFIX)) == 2


@pytest.mark.asyncio
async def test_ten_persist_retries_create_no_new_objects(
    files: FileServiceLocal, store: VerificationReportStore, mongo_test_client: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await ledger.ensure_indexes()
    await ledger.delete_all_verifications()
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_verification_report_store", lambda: store)
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_verification_database_service", lambda: ledger)
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_mongo_client", lambda: mongo_test_client)
    monkeypatch.setattr(get_settings(), "verification_activity_inline_max_bytes", 1)  # externalize statistics
    output = _output()
    await ledger.insert_verification(_record().model_copy(update={"workflow_id": output.workflow_id}))
    statistics = await externalize_statistics(_statistics(), output.workflow_id)
    assert statistics.reference is not None
    payload = PersistVerificationReportActivityInput(
        output=output.model_copy(update={"workflow_results_reference": statistics.reference}),
        provenance=_provenance(output),
    )
    first = await ActivityEnvironment().run(persist_verification_report_activity, payload)
    assert first.persistence_state == ReportPersistenceState.STORED
    after_first = _objects(files.base_dir)
    assert _objects(files.base_dir, REPORT_PREFIX) == [
        report_artifact_path(output.workflow_id, _report_digest(await _stored(store, output.workflow_id)))]
    for _ in range(9):
        await externalize_statistics(_statistics(), output.workflow_id)  # the statistics activity retried too
        retried = await ActivityEnvironment().run(persist_verification_report_activity, payload)
        assert retried.report_id == first.report_id
    assert _objects(files.base_dir) == after_first
    await ledger.delete_all_verifications()


async def _stored(store: VerificationReportStore, workflow_id: str) -> VerifyWorkflowOutput:
    saved = await store.get(workflow_id)
    assert saved is not None
    return saved


@pytest.mark.asyncio
async def test_a_corrupt_report_object_is_repaired_and_the_report_stays_readable(
    files: FileServiceLocal, store: VerificationReportStore, caplog: pytest.LogCaptureFixture,
) -> None:
    output = _output()
    await files.upload_bytes(b"corrupt", report_artifact_path(output.workflow_id, _report_digest(output)))
    with caplog.at_level(logging.ERROR):
        envelope = await store.save(output, _provenance(output))
    assert envelope.artifact_reference is not None and ".repair-" in envelope.artifact_reference
    assert "verification_object_integrity_conflict" in caplog.text
    assert (await _stored(store, output.workflow_id)).workflow_id == output.workflow_id


@pytest.mark.asyncio
async def test_repeated_restores_reuse_one_object(
    tmp_path: Path, files: FileServiceLocal, store: VerificationReportStore, mongo_test_client: Any,
) -> None:
    output = _output()
    await store.save(output, _provenance(output))
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await ledger.ensure_indexes()
    bundle = tmp_path / "bundle"
    await export_bundle(bundle, store, ledger)
    destination_files = FileServiceLocal(tmp_path / "restored")
    destination = VerificationReportStore(mongo_test_client, destination_files)
    await store.delete_all()
    for _ in range(2):
        # A restore interrupted after its upload, before its insert, is simply run again.
        await restore_bundle(bundle, destination, ledger, apply=True)
        await destination.delete_all()
    assert _objects(destination_files.base_dir) == [report_artifact_path(output.workflow_id, _report_digest(output))]
