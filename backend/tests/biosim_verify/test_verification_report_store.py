"""Tests for the report codec and the write-once report store.

The codec tests are pure functions over in-memory bytes and pin the properties
the store depends on: determinism, integrity, and lossless handling of the
values a real report contains (NaN, infinities, non-ASCII text, dataset keys
with dots and dollars).

The store tests at the end run against a real MongoDB through the
``verification_report_store_mongo`` fixture. They cover the write-once
contract (a first save succeeds, a second returns ``False`` and leaves the
original readable, two in-flight saves leave exactly one report) and the two
storage paths: small reports inline in the document, large ones in GridFS.
"""

import asyncio
import gzip
import hashlib
import math
import pytest
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from biosim_server.biosim_verify import ComparisonStatistics
from biosim_server.biosim_verify.report_store import (
    GRIDFS_BUCKET,
    ReportCorrupt,
    REPORT_ENCODING,
    VerificationReportStore,
    decode_report,
    encode_report,
)
from biosim_server.biosim_verify.models import (
    CompareSettings,
    GenerateStatisticsActivityOutput,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.config import get_settings


# Fixtures
@pytest.fixture
def verify_workflow_output() -> VerifyWorkflowOutput:
    """Fixture for a VerifyWorkflowOutput object."""
    return VerifyWorkflowOutput(
        workflow_id="test_workflow_id",
        compare_settings=CompareSettings(
            user_description="",
            include_outputs=True,
            rel_tol=1e-6,
            abs_tol_min=1e-9,
            abs_tol_scale=1.0,
        ),
        workflow_status=VerifyWorkflowStatus.COMPLETED,
        timestamp="2026-11-11T00:00:00Z",
    )


# Tests
def test_nan_and_infinity_round_trip(
    verify_workflow_output: VerifyWorkflowOutput,
) -> None:
    """Test that NaN and infinity round trip correctly."""
    score = [float("nan"), float("inf"), float("-inf")]
    stats = ComparisonStatistics(
        dataset_name="dataset",
        simulator_version_i="amici:1.0",
        simulator_version_j="copasi:1.0",
        var_names=["x", "y", "z"],
        score=score,
    )
    verify_workflow_output.workflow_results = GenerateStatisticsActivityOutput(
        sims_run_info=[],
        comparison_statistics={"dataset": [[stats]]},
    )
    compressed, _length, sha256 = encode_report(verify_workflow_output)
    decoded = decode_report(compressed, sha256)
    assert decoded.workflow_results is not None
    back = decoded.workflow_results.comparison_statistics["dataset"][0][0].score
    assert back is not None
    # NaN is never equal to itself, so compare with math rather than ==
    assert math.isnan(back[0])
    assert math.isinf(back[1]) and back[1] > 0
    assert math.isinf(back[2]) and back[2] < 0


def test_encode_report(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that the report is encoded correctly."""
    # non-ASCII forces len(str) != len(bytes); without it the length
    # assertion cannot distinguish the two
    verify_workflow_output.compare_settings.user_description = "café ✓"
    compressed, length, sha256 = encode_report(verify_workflow_output)
    raw = gzip.decompress(compressed)
    assert raw.decode("utf-8").startswith("{")
    assert length == len(raw)
    assert len(sha256) == 64
    assert sha256 == hashlib.sha256(raw).hexdigest()


def test_encode_report_is_repeatable(
    verify_workflow_output: VerifyWorkflowOutput,
) -> None:
    """The same report compresses to the same bytes."""
    (
        first,
        _length,
        _sha256,
    ) = encode_report(verify_workflow_output)
    second, _second_length, _second_sha256 = encode_report(verify_workflow_output)
    # Pins gzip.compress(mtime=0): without it the header carries the clock
    assert first[4:8] == b"\x00\x00\x00\x00"
    assert second[4:8] == b"\x00\x00\x00\x00"
    assert first == second



def test_report_encoding_names_this_format() -> None:
    """The report encoding is named correctly."""
    # Changing this value makes every stored report unreadable, so pin the literal
    assert REPORT_ENCODING == "json-gzip-v1"


def test_decode_report(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that the report is decoded correctly."""
    stats = ComparisonStatistics(
        dataset_name="dataset",
        simulator_version_i="amici:1.0",
        simulator_version_j="copasi:1.0",
        var_names=["x", "y", "z"],
        score=[1.0, 2.0, 3.0],
    )
    verify_workflow_output.workflow_results = GenerateStatisticsActivityOutput(
        sims_run_info=[],
        comparison_statistics={"dataset": [[stats]]},
    )
    compressed, _length, sha256 = encode_report(verify_workflow_output)
    decoded = decode_report(compressed, sha256)
    assert decoded == verify_workflow_output


def test_decode_report_decompression_failure() -> None:
    """Test that the report is rejected if the decompression fails."""
    with pytest.raises(ReportCorrupt, match="Decompression failed"):
        decode_report(b"bad_gzip", "unused-sha256")


def test_decode_report_json_decode_failure() -> None:
    """Test that the report is rejected if the JSON decode fails."""
    raw = b"\xff"
    compressed = gzip.compress(raw)
    # A real digest, so the failure is the decode and not the hash check
    valid_hash = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ReportCorrupt, match="JSON decoding failed"):
        decode_report(compressed, valid_hash)


def test_decode_report_json_validation_failure() -> None:
    """Test that the report is rejected if the JSON validation fails."""
    raw = b"{}"
    compressed = gzip.compress(raw)
    valid_hash = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ReportCorrupt, match="JSON validation failed"):
        decode_report(compressed, valid_hash)


def test_decode_report_rejects_mismatched_hash(
    verify_workflow_output: VerifyWorkflowOutput,
) -> None:
    """Test that the report is rejected if the hash is mismatched."""
    compressed, _length, _sha256 = encode_report(verify_workflow_output)
    with pytest.raises(ReportCorrupt) as excinfo:
        decode_report(compressed, "wrong-hash")
    assert excinfo.value.args[0] == "SHA-256 mismatch"


def test_decode_report_rejects_a_flipped_byte(
    verify_workflow_output: VerifyWorkflowOutput,
) -> None:
    """A corrupted gzip blob is rejected as a decompression failure."""
    compressed, _length, sha256 = encode_report(verify_workflow_output)
    flipped = bytearray(compressed)
    # Middle byte: gzip's CRC and length trailer live at the end
    flipped[len(flipped) // 2] ^= 0xFF
    with pytest.raises(ReportCorrupt, match="Decompression failed"):
        decode_report(bytes(flipped), sha256)


def test_dataset_key_with_dot_and_dollar_round_trips(
    verify_workflow_output: VerifyWorkflowOutput,
) -> None:
    """Dataset keys keep '.' and '$' through round trip."""
    # Real SED-ML dataset names are paths like this; MongoDB would reject them
    # as document keys, which is why the report is stored as an opaque blob.
    dataset_key = "report.sedml/$time"
    stats = ComparisonStatistics(
        dataset_name=dataset_key,
        simulator_version_i="amici:1.0",
        simulator_version_j="copasi:1.0",
        var_names=["x"],
        score=[1.0],
    )
    verify_workflow_output.workflow_results = GenerateStatisticsActivityOutput(
        sims_run_info=[],
        comparison_statistics={dataset_key: [[stats]]},
    )
    compressed, _length, sha256 = encode_report(verify_workflow_output)
    decoded = decode_report(compressed, sha256)
    assert decoded.workflow_results is not None
    recovered = decoded.workflow_results.comparison_statistics[dataset_key][0][0].score
    assert recovered == [1.0]


# ---------------------------------------------------------------------------
# VerificationReportStore (real MongoDB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_then_get_returns_the_same_report(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
) -> None:
    """Test that save then get returns the same report."""
    # save() returns True on success, not the report
    assert await verification_report_store_mongo.save(verify_workflow_output) is True
    loaded = await verification_report_store_mongo.get(
        verify_workflow_output.workflow_id
    )
    assert loaded == verify_workflow_output


@pytest.mark.asyncio
async def test_get_missing_workflow_returns_none(
    verification_report_store_mongo: VerificationReportStore,
) -> None:
    """Test that get on a missing workflow returns None."""
    # A miss is None, not an exception; only a damaged payload raises
    assert await verification_report_store_mongo.get("missing-workflow-id") is None


def _reports_collection(
    mongo_test_client: AsyncIOMotorClient,
) -> AsyncIOMotorCollection:
    # The store's own collection, so a test can tamper with a stored document
    settings = get_settings()
    database = mongo_test_client.get_database(settings.mongodb_database)
    return database.get_collection(settings.mongodb_collection_verify_reports)


@pytest.mark.asyncio
async def test_save_rejects_an_unfinished_report(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
) -> None:
    """An in-progress report is a caller mistake, and nothing is written."""
    verify_workflow_output.workflow_status = VerifyWorkflowStatus.IN_PROGRESS
    with pytest.raises(ValueError) as excinfo:
        await verification_report_store_mongo.save(verify_workflow_output)
    # A caller mistake, not stored damage: ValueError, never ReportCorrupt
    assert not isinstance(excinfo.value, ReportCorrupt)
    assert (
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id)
        is None
    )


@pytest.mark.asyncio
async def test_get_rejects_an_unknown_encoding(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    mongo_test_client: AsyncIOMotorClient,
) -> None:
    """A stored document tagged with a future encoding is not decoded."""
    await verification_report_store_mongo.save(verify_workflow_output)
    # Rewrite the tag behind the store's back to simulate a newer format
    await _reports_collection(mongo_test_client).update_one(
        {"workflow_id": verify_workflow_output.workflow_id},
        {"$set": {"encoding": "xml-v9"}},
    )
    with pytest.raises(ReportCorrupt):
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id)


@pytest.mark.asyncio
async def test_get_rejects_a_replaced_payload(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    mongo_test_client: AsyncIOMotorClient,
) -> None:
    """Replacing the stored bytes fails the integrity check."""
    await verification_report_store_mongo.save(verify_workflow_output)
    # The digest still matches the original, so only the hash check can catch this
    other = verify_workflow_output.model_copy(deep=True)
    other.compare_settings.user_description = "a different finished report"
    other_payload, _length, _sha256 = encode_report(other)
    await _reports_collection(mongo_test_client).update_one(
        {"workflow_id": verify_workflow_output.workflow_id},
        {"$set": {"payload": other_payload}},
    )
    with pytest.raises(ReportCorrupt, match="SHA-256 mismatch"):
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id)


@pytest.mark.asyncio
async def test_second_save_keeps_the_original_report(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
) -> None:
    """A later save of the same workflow_id does not replace the stored report."""
    original = verify_workflow_output.model_copy(deep=True)
    changed = verify_workflow_output.model_copy(deep=True)
    changed.compare_settings.user_description = "changed after the first save"
    assert await verification_report_store_mongo.save(original) is True
    # False, not an exception: losing the write is an expected outcome
    assert await verification_report_store_mongo.save(changed) is False
    assert await verification_report_store_mongo.get(original.workflow_id) == original


@pytest.mark.asyncio
async def test_concurrent_saves_one_wins(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent saves of the same workflow leave exactly one stored report."""
    store = verification_report_store_mongo
    real_find_one = store._collection.find_one
    arrived = 0
    release = asyncio.Event()

    async def wait_until_both_have_arrived(*args: Any, **kwargs: Any) -> Any:
        nonlocal arrived
        found = await real_find_one(*args, **kwargs)
        arrived += 1
        if arrived == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return found

    monkeypatch.setattr(store._collection, "find_one", wait_until_both_have_arrived)
    results = await asyncio.gather(
        store.save(verify_workflow_output),
        store.save(verify_workflow_output),
    )
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_small_report_is_stored_inline(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    mongo_test_client: AsyncIOMotorClient,
) -> None:
    """A small report is stored inline in the document."""
    assert await verification_report_store_mongo.save(verify_workflow_output) is True
    document = await _reports_collection(mongo_test_client).find_one(
        {"workflow_id": verify_workflow_output.workflow_id}
    )
    assert document is not None
    # Under the threshold: bytes in the document, no GridFS file at all
    assert document.get("payload") is not None
    assert document.get("file_id") is None
    assert (
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id) == verify_workflow_output
    )


@pytest.mark.asyncio
async def test_large_report_is_stored_in_gridfs(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    mongo_test_client: AsyncIOMotorClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large report is stored in GridFS."""
    # A zero threshold forces every report down the GridFS path
    monkeypatch.setattr(
        "biosim_server.biosim_verify.report_store.INLINE_PAYLOAD_MAX_BYTES",
        0,
    )
    assert await verification_report_store_mongo.save(verify_workflow_output) is True
    document = await _reports_collection(mongo_test_client).find_one(
        {"workflow_id": verify_workflow_output.workflow_id}
    )
    assert document is not None
    # Over the threshold: no inline bytes, a file_id instead
    assert document.get("payload") is None
    assert document.get("file_id") is not None
    # The reader does not care which path was taken
    assert (
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id) == verify_workflow_output
    )


@pytest.mark.asyncio
async def test_get_rejects_a_document_with_no_bytes(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    mongo_test_client: AsyncIOMotorClient,
) -> None:
    """A document with no payload or file_id is rejected."""
    await verification_report_store_mongo.save(verify_workflow_output)
    # Strip the bytes but leave the row, so the document is damaged, not absent
    await _reports_collection(mongo_test_client).update_one(
        {"workflow_id": verify_workflow_output.workflow_id},
        {"$unset": {"payload": ""}},
    )
    with pytest.raises(ReportCorrupt, match="No payload or file_id in document"):
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id)


@pytest.mark.asyncio
async def test_losing_gridfs_save_deletes_its_upload(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A save that loses the write-once race deletes the GridFS upload."""
    store = verification_report_store_mongo
    assert await store.save(verify_workflow_output) is True

    async def pretend_missing(*args: Any, **kwargs: Any) -> Any:
        # Hide the committed row so the next save takes the slow path
        return None

    with monkeypatch.context() as patched:
        patched.setattr(store._collection, "find_one", pretend_missing)
        patched.setattr(
            "biosim_server.biosim_verify.report_store.INLINE_PAYLOAD_MAX_BYTES",
            0,
        )
        # The insert hits the unique index, so this save loses
        assert await store.save(verify_workflow_output) is False

    # The loser's upload was cleaned up rather than left orphaned
    count = 0
    async for _stored in store._files.find({}):
        count += 1
    assert count == 0
    # And the winner's report is still readable
    assert await store.get(verify_workflow_output.workflow_id) == verify_workflow_output


@pytest.mark.asyncio
async def test_get_rejects_a_gridfs_file_with_no_chunks(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    mongo_test_client: AsyncIOMotorClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file whose chunks are gone is damaged, not missing."""
    # Force the GridFS path so there is a file whose chunks can be removed
    monkeypatch.setattr(
        "biosim_server.biosim_verify.report_store.INLINE_PAYLOAD_MAX_BYTES",
        0,
    )
    assert await verification_report_store_mongo.save(verify_workflow_output) is True
    settings = get_settings()
    chunks = mongo_test_client.get_database(settings.mongodb_database).get_collection(
        f"{GRIDFS_BUCKET}.chunks"
    )
    # The file row survives; only its bytes are gone
    await chunks.delete_many({})
    with pytest.raises(ReportCorrupt, match="Stored file is missing"):
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id)

@pytest.mark.asyncio
async def test_get_rejects_a_missing_gridfs_file(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    mongo_test_client: AsyncIOMotorClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file whose row is gone is damaged, not missing."""
    # Force the GridFS path so the document points at a file
    monkeypatch.setattr(
        "biosim_server.biosim_verify.report_store.INLINE_PAYLOAD_MAX_BYTES",
        0,
    )
    assert await verification_report_store_mongo.save(verify_workflow_output) is True

    document = await _reports_collection(mongo_test_client).find_one(
        {"workflow_id": verify_workflow_output.workflow_id}
    )

    assert document is not None
    # Remove the file row itself, unlike the chunks test above. Await the
    # future: delete starts on a background thread, and get() can otherwise
    # read the file before that thread finishes.
    await verification_report_store_mongo._files.delete(document["file_id"])

    with pytest.raises(ReportCorrupt, match="Stored file is missing"):
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id)

@pytest.mark.asyncio
async def test_get_rejects_a_document_with_no_sha256(
    verification_report_store_mongo: VerificationReportStore,
    verify_workflow_output: VerifyWorkflowOutput,
    mongo_test_client: AsyncIOMotorClient,
) -> None:
    """A document with no sha256 is rejected before decoding."""
    assert await verification_report_store_mongo.save(verify_workflow_output) is True
    # Drop the digest but keep the bytes: without it the payload cannot be
    # integrity-checked, so the read must refuse rather than decode blindly
    await _reports_collection(mongo_test_client).update_one(
        {"workflow_id": verify_workflow_output.workflow_id},
        {"$unset": {"sha256": ""}},
    )
    with pytest.raises(ReportCorrupt, match="no sha256"):
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id)