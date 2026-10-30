"""Immutable Mongo-backed verification report persistence."""

import json
from datetime import UTC, datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from biosim_server.biosim_verify.models import (
    GenerateStatisticsActivityOutput,
    ReportPersistenceState,
    VerificationProvenance,
    VerificationReportEnvelope,
    VerifyWorkflowOutput,
)
from biosim_server.biosim_verify.report_codec import decode_report, encode_report
from biosim_server.config import get_settings
from biosim_server.common.storage import FileService


class ReportIntegrityConflict(RuntimeError):
    pass


class ReportUnavailable(RuntimeError):
    pass


class ReportArtifactMissing(ReportUnavailable):
    """The referenced object does not exist.

    Object reads are strongly consistent, so a missing object will not
    reappear; retrying cannot help.  Storage that is unconfigured or
    unreachable stays a plain :class:`ReportUnavailable`.
    """


class ReportCorrupt(RuntimeError):
    pass


class VerificationReportStore:
    def __init__(self, db_client: AsyncIOMotorClient, file_service: FileService | None = None) -> None:
        settings = get_settings()
        self._collection = db_client.get_database(settings.mongodb_database).get_collection(
            settings.mongodb_collection_verify_reports
        )
        self._file_service = file_service
        self._settings = settings.model_copy()

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("report_id", unique=True)
        await self._collection.create_index("workflow_id", unique=True)

    async def save(self, output: VerifyWorkflowOutput, provenance: VerificationProvenance) -> VerificationReportEnvelope:
        if provenance.verification_id != output.workflow_id:
            raise ReportIntegrityConflict("provenance verification identity mismatch")
        if provenance.owner_sub != output.owner_sub:
            raise ReportIntegrityConflict("provenance owner identity mismatch")
        if provenance.workflow_run_id != output.workflow_run_id:
            raise ReportIntegrityConflict("provenance workflow run identity mismatch")
        if not output.workflow_status.is_done:
            raise ReportIntegrityConflict("only terminal reports can be archived")
        output = output.model_copy(update={
            "persistence_state": ReportPersistenceState.PENDING, "report_id": None, "report_available": None,
        })
        compressed, encoding, uncompressed_bytes, digest = encode_report(output.model_dump(mode="json"))
        if uncompressed_bytes > self._settings.verification_report_max_decompressed_bytes:
            raise ValueError("verification report exceeds archival size limit")
        report_id = output.workflow_id
        existing = await self._collection.find_one({
            "$or": [{"workflow_id": output.workflow_id}, {"report_id": report_id}]
        })
        if existing:
            if (
                existing.get("workflow_id") != output.workflow_id
                or existing.get("report_id") != report_id
                or existing.get("owner_sub") != output.owner_sub
                or existing.get("workflow_run_id") != output.workflow_run_id
                or existing.get("content_sha256") != digest
            ):
                raise ReportIntegrityConflict(f"conflicting report for {output.workflow_id}")
            return VerificationReportEnvelope.model_validate(existing)
        envelope = VerificationReportEnvelope(
            report_id=report_id, workflow_id=output.workflow_id, workflow_run_id=output.workflow_run_id,
            owner_sub=output.owner_sub, workflow_status=output.workflow_status,
            computed_at=provenance.computed_at or datetime.fromisoformat(output.timestamp), saved_at=datetime.now(UTC), content_sha256=digest,
            encoding=encoding, uncompressed_bytes=uncompressed_bytes, provenance=provenance,
        )
        if len(compressed) <= self._settings.verification_report_inline_max_bytes:
            envelope.inline_payload = compressed
        elif self._file_service is not None:
            reference = f"verification-reports/{report_id}/{digest}.json.gz"
            envelope.artifact_reference = await self._file_service.upload_bytes_immutable(compressed, reference)
        else:
            raise ValueError("verification report exceeds inline storage limit")
        try:
            await self._collection.insert_one(envelope.model_dump())
        except DuplicateKeyError:
            other = await self._collection.find_one({
                "$or": [{"workflow_id": output.workflow_id}, {"report_id": report_id}]
            })
            if other and all([
                other.get("workflow_id") == output.workflow_id,
                other.get("report_id") == report_id,
                other.get("owner_sub") == output.owner_sub,
                other.get("workflow_run_id") == output.workflow_run_id,
                other.get("content_sha256") == digest,
            ]):
                return VerificationReportEnvelope.model_validate(other)
            raise ReportIntegrityConflict(f"conflicting report for {output.workflow_id}")
        return envelope

    async def get_provenance(self, workflow_id: str) -> VerificationProvenance | None:
        doc = await self._collection.find_one({"workflow_id": workflow_id}, projection={"provenance": 1, "_id": 0})
        if not doc or not isinstance(doc.get("provenance"), dict):
            return None
        return VerificationProvenance.model_validate(doc["provenance"])

    async def get(self, workflow_id: str) -> VerifyWorkflowOutput | None:
        doc = await self._collection.find_one({"workflow_id": workflow_id})
        if not doc:
            return None
        if doc.get("report_schema_version") != 1 or doc.get("provenance_schema_version") != 1:
            raise ReportCorrupt("unsupported report schema")
        if doc.get("inline_payload") is not None:
            payload = bytes(doc["inline_payload"])
        elif self._file_service is not None and doc.get("artifact_reference"):
            found_payload = await self._file_service.get_file_contents(doc["artifact_reference"])
            if found_payload is None:
                raise ReportArtifactMissing("verification report artifact is unavailable")
            payload = found_payload
        else:
            raise ReportUnavailable("verification report payload is unavailable")
        try:
            decoded = decode_report(payload, encoding=doc["encoding"],
                                    max_decompressed_bytes=self._settings.verification_report_max_decompressed_bytes)
        except (ValueError, OSError, EOFError, UnicodeError, TypeError, json.JSONDecodeError) as exc:
            raise ReportCorrupt("verification report payload is corrupt") from exc
        _, _, uncompressed_bytes, digest = encode_report(decoded)
        if digest != doc.get("content_sha256") or uncompressed_bytes != doc.get("uncompressed_bytes"):
            raise ReportCorrupt("verification report checksum mismatch")
        output = VerifyWorkflowOutput.model_validate(decoded)
        if (
            output.workflow_id != doc.get("workflow_id")
            or output.owner_sub != doc.get("owner_sub")
            or output.workflow_run_id != doc.get("workflow_run_id")
        ):
            raise ReportCorrupt("verification report identity mismatch")
        return output.model_copy(update={
            "persistence_state": ReportPersistenceState.STORED,
            "report_available": True, "report_id": doc["report_id"],
        })

    async def hydrate_activity_reference(self, output: VerifyWorkflowOutput) -> VerifyWorkflowOutput:
        ref = output.workflow_results_reference
        if ref is None:
            return output
        if self._file_service is None:
            raise ReportUnavailable("statistics artifact storage is unavailable")
        payload = await self._file_service.get_file_contents(ref.artifact_reference)
        if payload is None:
            raise ReportArtifactMissing("statistics artifact is unavailable")
        try:
            decoded = decode_report(
                payload, encoding=ref.encoding,
                max_decompressed_bytes=self._settings.verification_report_max_decompressed_bytes,
            )
            _, _, size, digest = encode_report(decoded)
            if digest != ref.content_sha256 or size != ref.uncompressed_bytes:
                raise ReportCorrupt("statistics artifact checksum mismatch")
            return output.model_copy(update={
                "workflow_results": GenerateStatisticsActivityOutput.model_validate(decoded),
            })
        except ReportCorrupt:
            raise
        except Exception as exc:
            raise ReportCorrupt("statistics artifact is corrupt") from exc

    async def delete_all(self) -> None:
        await self._collection.delete_many({})
