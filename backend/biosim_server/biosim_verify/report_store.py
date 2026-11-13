"""Durable storage for finished verification reports.

A verification's result lives in three places over its life, and this module
owns the third: the Temporal workflow result (gone once retention passes), the
``BiosimCompare`` ledger row (identity and status, not the report itself), and
the report bytes kept here.

Two halves: ``encode_report`` / ``decode_report`` are the pure codec, and
``VerificationReportStore`` keeps one document per verification in
``BiosimVerifyReports`` holding both the index fields and the compressed
payload.

Two properties are load-bearing. **Determinism**: the same report always
encodes to the same bytes and digest, so two writers recovering the same
execution converge on one stored object. **Integrity**: every read re-checks
the SHA-256, so a damaged payload raises ``ReportCorrupt`` instead of being
served as a plausible report.

``save`` is write-once and reports it: a second save for the same
``workflow_id`` returns ``False`` rather than replacing the stored report.
"""

import asyncio
from datetime import datetime, UTC
import gzip
import hashlib
import json
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from biosim_server.biosim_verify.models import VerifyWorkflowOutput
from biosim_server.config import get_settings


# Subclasses ValueError so callers written against the earlier codec keep working.
class ReportCorrupt(ValueError):
    """A stored report could not be read back."""


# Written beside every report and checked on every read; bump it whenever the
# encoding changes so an old payload is refused rather than misread.
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
        self._collection = database.get_collection(
            settings.mongodb_collection_verify_reports
        )

    async def ensure_indexes(self) -> None:
        # The unique index is what makes save() write-once; it must exist before
        # the first save, or two writers can both insert.
        await self._collection.create_index("workflow_id", unique=True)

    async def save(self, output: VerifyWorkflowOutput) -> bool:
        # A caller mistake, not stored damage: nothing is encoded or written.
        if not output.workflow_status.is_done:
            raise ValueError("Only a finished verification can be stored")
        # Cheap path once a save has committed. Two in-flight saves can both
        # pass this check; the unique index below is what decides the winner.
        if (
            await self._collection.find_one({"workflow_id": output.workflow_id})
            is not None
        ):
            return False
        # Compression is CPU-bound, so keep it off the event loop.
        compressed, length, sha256 = await asyncio.to_thread(encode_report, output)
        # The index fields are duplicated out of the payload so a listing can be
        # served without decompressing every report.
        document: dict[str, Any] = {
            "workflow_id": output.workflow_id,
            "workflow_run_id": output.workflow_run_id,
            "owner_sub": output.owner_sub,
            "workflow_status": output.workflow_status.value,
            "saved_at": datetime.now(UTC),
            "encoding": REPORT_ENCODING,
            "uncompressed_bytes": length,
            "sha256": sha256,
            "payload": compressed,
        }
        try:
            await self._collection.insert_one(document)
        # Lost the race against a concurrent save: the first report stands.
        except DuplicateKeyError:
            return False
        return True

    async def get(self, workflow_id: str) -> VerifyWorkflowOutput | None:
        document = await self._collection.find_one({"workflow_id": workflow_id})
        # No row means never stored (or removed): a miss, not an error.
        if document is None:
            return None
        # An unknown or missing tag means a format this code cannot read; refuse
        # it rather than misread it.
        if document.get("encoding") != REPORT_ENCODING:
            raise ReportCorrupt(f"Unsupported encoding: {document.get('encoding')}")
        payload = bytes(document["payload"])
        # Decompression is CPU-bound, so keep it off the event loop.
        return await asyncio.to_thread(decode_report, payload, document["sha256"])

    async def delete_all(self) -> None:
        # Tests only: production code never removes a stored report.
        await self._collection.delete_many({})
