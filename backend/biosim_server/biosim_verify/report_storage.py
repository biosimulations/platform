"""Immutable Mongo-backed verification report persistence."""

import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from biosim_server.biosim_verify.models import (
    GenerateStatisticsActivityOutput,
    ReportPersistenceState,
    VerificationProvenance,
    VerificationReportEnvelope,
    VerifyWorkflowOutput,
)
from biosim_server.biosim_verify.object_names import holds_content, report_artifact_path
from biosim_server.biosim_verify.report_codec import decode_report, encode_report
from biosim_server.config import get_settings
from biosim_server.common.storage import FileService
from biosim_server.common.storage.content_addressed import publish_content_addressed


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
            # Retries and racing writers publish the same name; none adds an object.
            envelope.artifact_reference = await publish_content_addressed(
                self._file_service, compressed, report_artifact_path(report_id, digest),
                holds_content(digest, encoding),
            )
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

    async def find_envelope(self, workflow_id: str) -> VerificationReportEnvelope | None:
        """The stored report's envelope, without its payload, or None."""
        doc = await self._collection.find_one({"workflow_id": workflow_id}, projection={"_id": 0, "inline_payload": 0})
        return VerificationReportEnvelope.model_validate(doc) if doc else None

    async def referenced_objects(self) -> AsyncIterator[str]:
        """Every object name a stored report references: its payload, snapshots and manifest."""
        projection = {"_id": 0, "artifact_reference": 1, "provenance.input_snapshots.artifact_reference": 1,
                      "provenance.snapshot_manifest_reference": 1}
        async for doc in self._collection.find({}, projection=projection):
            if doc.get("artifact_reference"):
                yield str(doc["artifact_reference"])
            provenance = doc.get("provenance") or {}
            if provenance.get("snapshot_manifest_reference"):
                yield str(provenance["snapshot_manifest_reference"])
            for snapshot in provenance.get("input_snapshots") or []:
                if snapshot.get("artifact_reference"):
                    yield str(snapshot["artifact_reference"])

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

    # --- Backup and restore -------------------------------------------------------------

    async def iter_envelopes(self) -> AsyncIterator[dict[str, Any]]:
        """Every stored envelope, exactly as stored."""
        async for doc in self._collection.find({}):
            yield dict(doc)

    async def read_artifact(self, reference: str) -> bytes | None:
        """The bytes of an object a report references, or None when it does not exist."""
        if self._file_service is None:
            raise ReportUnavailable("report artifact storage is unavailable")
        return await self._file_service.get_file_contents(reference)

    def check_envelope(self, doc: Mapping[str, Any], payload: bytes) -> None:
        """Raise ``ValueError`` unless *payload* is exactly the report *doc* describes."""
        envelope = VerificationReportEnvelope.model_validate(doc)
        decoded = decode_report(payload, encoding=envelope.encoding,
                                max_decompressed_bytes=self._settings.verification_report_max_decompressed_bytes)
        _, _, size, digest = encode_report(decoded)
        output = VerifyWorkflowOutput.model_validate(decoded)
        if size != envelope.uncompressed_bytes or digest != envelope.content_sha256 or (
            output.workflow_id != envelope.workflow_id or output.owner_sub != envelope.owner_sub
            or output.workflow_run_id != envelope.workflow_run_id
        ):
            raise ValueError("Backup report integrity mismatch")

    async def restore_envelope(self, doc: dict[str, Any], artifact: bytes | None = None) -> bool:
        """Insert a backed-up envelope; never replace a report.  True when it was inserted.

        An identical report already present is left as it is (False).  A
        different one raises :class:`ReportIntegrityConflict`.  An externalized
        payload (*artifact*) is published under the name the report would have
        had, so a repeated restore reuses one object.  The restored report is
        read back before this returns.
        """
        def conflicts(existing: Mapping[str, Any]) -> bool:
            return any(existing.get(key) != doc.get(key) for key in ("content_sha256", "owner_sub", "workflow_run_id"))

        existing = await self._collection.find_one({"workflow_id": doc["workflow_id"]})
        if existing:
            if conflicts(existing):
                raise ReportIntegrityConflict("Restore would change an existing report")
            return False
        doc = {key: value for key, value in doc.items() if key != "_id"}
        if doc.get("artifact_reference"):
            if self._file_service is None or artifact is None:
                raise ReportUnavailable("Destination object store unavailable")
            doc["artifact_reference"] = await publish_content_addressed(
                self._file_service, artifact, report_artifact_path(doc["report_id"], doc["content_sha256"]),
                holds_content(doc["content_sha256"], doc["encoding"]),
            )
        try:
            await self._collection.insert_one(doc)
        except DuplicateKeyError:
            current = await self._collection.find_one({"workflow_id": doc["workflow_id"]})
            if not current or conflicts(current):
                raise ReportIntegrityConflict("Concurrent restore conflict")
            return False
        await self.get(doc["workflow_id"])
        return True

    async def delete_all(self) -> None:
        await self._collection.delete_many({})
