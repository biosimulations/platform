"""Tests for the report codec and the write-once report store.

The codec tests are pure functions over in-memory bytes and pin the properties
the store depends on: determinism, integrity, and lossless handling of the
values a real report contains (NaN, infinities, non-ASCII text, dataset keys
with dots and dollars).

The store tests at the end run against a real MongoDB through the
``verification_report_store_mongo`` fixture. They cover the write-once
contract: a first save succeeds, a second returns ``False`` and leaves the
original readable, and two in-flight saves leave exactly one report.
"""

import asyncio
import gzip
import hashlib
import math

import pytest
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from biosim_server.biosim_verify import ComparisonStatistics
from biosim_server.biosim_verify.report_store import (
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
    await _reports_collection(mongo_test_client).update_one(
        {"workflow_id": verify_workflow_output.workflow_id},
        {"$set": {"payload": b"not-the-stored-bytes"}},
    )
    with pytest.raises(ReportCorrupt):
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
) -> None:
    """Two in-flight saves of one workflow leave exactly one stored report."""
    # Both may pass the find_one check; the unique index decides the winner
    results = await asyncio.gather(
        verification_report_store_mongo.save(verify_workflow_output),
        verification_report_store_mongo.save(verify_workflow_output),
    )
    assert sorted(results) == [False, True]
    assert (
        await verification_report_store_mongo.get(verify_workflow_output.workflow_id)
        == verify_workflow_output
    )
