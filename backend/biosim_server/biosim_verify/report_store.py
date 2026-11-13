"""Durable storage for finished verification reports.

A verification's result lives in three places over its life, and this module
owns the third: the Temporal workflow result (gone once retention passes), the
``BiosimCompare`` ledger row (identity and status, not the report itself), and
the report bytes kept here.

Two halves: ``encode_report`` / ``decode_report`` are the pure codec, and
``VerificationReportStore`` persists those bytes in GridFS with a small index
document in ``BiosimVerifyReports``.

Two properties are load-bearing. **Determinism**: the same report always
encodes to the same bytes and digest, so two writers recovering the same
execution converge on one stored object. **Integrity**: every read re-checks
the SHA-256, so a damaged blob raises ``ReportCorrupt`` instead of being served
as a plausible report.
"""

import gzip
import hashlib
import json
from typing import Any, cast

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from biosim_server.biosim_verify.models import VerifyWorkflowOutput
from biosim_server.config import get_settings


# Subclasses ValueError so callers written against the earlier codec keep working.
class ReportCorrupt(ValueError):
    """A stored report could not be read back."""


# Written beside every report and checked on every read; bump it whenever the
# encoding changes so an old blob is refused rather than misread.
REPORT_ENCODING = "json-gzip-v1"


def encode_report(output: VerifyWorkflowOutput) -> tuple[bytes, int, str]:
    # mode="json" renders datetimes and enums as JSON scalars; allow_nan keeps
    # NaN/Infinity, which real comparison scores contain; ensure_ascii=False
    # writes UTF-8 instead of \uXXXX escapes.
    json_data = json.dumps(
        output.model_dump(mode="json"),
        allow_nan=True,
        ensure_ascii=False,
    )
    raw = json_data.encode("utf-8")
    # mtime=0 keeps the gzip header identical across runs, so the same report
    # always produces the same bytes.
    compressed = gzip.compress(raw, mtime=0)
    # The digest covers the uncompressed JSON, not the gzip output.
    return compressed, len(raw), hashlib.sha256(raw).hexdigest()


def decode_report(compressed: bytes, expected_sha256: str) -> VerifyWorkflowOutput:
    # Every failure below becomes ReportCorrupt, so callers have one exception to
    # handle and can treat any of them as "this report is unusable".
    try:
        decompressed = gzip.decompress(compressed)
    except Exception as e:
        raise ReportCorrupt(f"Decompression failed: {e}") from e
    try:
        json_data = decompressed.decode("utf-8")
    except Exception as e:
        raise ReportCorrupt(f"JSON decoding failed: {e}") from e
    if hashlib.sha256(decompressed).hexdigest() != expected_sha256:
        raise ReportCorrupt("SHA-256 mismatch")
    try:
        output = VerifyWorkflowOutput.model_validate_json(json_data)
    except Exception as e:
        raise ReportCorrupt(f"JSON validation failed: {e}") from e
    return output


class VerificationReportStore:
    """Write-once store of finished verification reports (BiosimVerifyReports)."""

    def __init__(self, db_client: AsyncIOMotorClient) -> None:
        settings = get_settings()
        database = db_client.get_database(settings.mongodb_database)
        self._reports = database.get_collection(settings.mongodb_collection_verify_reports)
        # GridFS chunks anything past the 16 MB BSON limit, so large reports store fine.
        self._files = AsyncIOMotorGridFSBucket(database, bucket_name="biosim_verify_reports")

    async def ensure_indexes(self) -> None:
        # The unique index is what makes save() write-once; it must exist before
        # the first save, or two writers can both insert.
        await self._reports.create_index("workflow_id", unique=True)

    async def save(self, output: VerifyWorkflowOutput) -> None:
        compressed, length, sha256 = encode_report(output)
        # Blob first, so an index document always points at a file that exists.
        file_id = await self._files.upload_from_stream(
            output.workflow_id,
            compressed,
            metadata={"sha256": sha256, "encoding": REPORT_ENCODING},
        )
        await self._reports.insert_one(
            {
                "workflow_id": output.workflow_id,
                "encoding": REPORT_ENCODING,
                "sha256": sha256,
                "uncompressed_length": length,
                "file_id": file_id,
            }
        )

    async def get(self, workflow_id: str) -> VerifyWorkflowOutput | None:
        document = await self._reports.find_one({"workflow_id": workflow_id})
        # No row means never stored (or removed): a miss, not an error.
        if document is None:
            return None
        # An unknown tag means a future format; refuse it rather than misread it.
        if document["encoding"] != REPORT_ENCODING:
            raise ReportCorrupt(f"Unsupported encoding: {document['encoding']}")
        grid_out = await self._files.open_download_stream(document["file_id"])
        compressed = await grid_out.read()
        return decode_report(compressed, document["sha256"])

    async def delete_all(self) -> None:
        # Tests only: production code never removes a stored report.
        # The bucket has no bulk delete, so remove each file by id.
        async for stored_file in self._files.find({}):
            await self._files.delete(cast(Any, stored_file)._id)
        await self._reports.delete_many({})
