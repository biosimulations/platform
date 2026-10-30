"""Portable, verified backups of verification ledgers, manifests, and report objects.

This covers application verification records, not Temporal or the complete Mongo database.
A completed bundle is immutable operator evidence; partial bundles cannot be restored.
"""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bson import BSON
from pymongo.errors import DuplicateKeyError

from biosim_server.biosim_verify.report_codec import decode_report, encode_report
from biosim_server.biosim_verify.report_storage import VerificationReportStore, ReportIntegrityConflict
from biosim_server.biosim_verify.models import VerificationReportEnvelope, VerifyWorkflowOutput
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def export_bundle(directory: Path, store: VerificationReportStore,
                        ledger: VerificationDatabaseServiceMongo) -> dict[str, Any]:
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    files: dict[str, str] = {}
    objects: dict[str, str] = {}

    def write(data: bytes, suffix: str) -> str:
        name = digest(data) + suffix
        (directory / name).write_bytes(data)
        (directory / name).chmod(0o600)
        files[name] = digest(data)
        return name

    manifests, records = [], []
    async for doc in store._collection.find({}):
        # Verify the source before claiming a usable backup.
        if await store.get(doc['workflow_id']) is None:
            raise RuntimeError('Report disappeared during backup')
        manifests.append(write(BSON.encode(doc), '.report.bson'))
        ref = doc.get('artifact_reference')
        if ref:
            if store._file_service is None:
                raise RuntimeError('Artifact storage unavailable')
            payload = await store._file_service.get_file_contents(ref)
            if payload is None:
                raise RuntimeError('Missing report object')
            objects[ref] = write(payload, '.object')
    async for doc in ledger._collection.find({}):
        records.append(write(BSON.encode(doc), '.ledger.bson'))
    manifest = {'version': 1, 'complete': True, 'created_at': datetime.now(UTC).isoformat(),
                'reports': manifests, 'ledger': records, 'objects': objects, 'files': files}
    (directory / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2))
    (directory / 'manifest.json').chmod(0o600)
    return manifest


async def restore_bundle(directory: Path, store: VerificationReportStore,
                         ledger: VerificationDatabaseServiceMongo, *, apply: bool = False) -> dict[str, int]:
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('version') != 1 or manifest.get('complete') is not True:
        raise ValueError('Incomplete or unsupported backup')
    data: dict[str, bytes] = {}
    for name, expected in manifest['files'].items():
        if Path(name).name != name or (directory / name).is_symlink():
            raise ValueError('Unsafe backup entry')
        payload = (directory / name).read_bytes()
        if digest(payload) != expected:
            raise ValueError('Backup checksum mismatch')
        data[name] = payload
    reports = [BSON(data[name]).decode() for name in manifest['reports']]
    records = [BSON(data[name]).decode() for name in manifest['ledger']]
    # Validate all payloads before making any destination writes.
    for doc in reports:
        envelope = VerificationReportEnvelope.model_validate(doc)
        blob = envelope.inline_payload
        if blob is None:
            blob = data[manifest['objects'][envelope.artifact_reference]]
        decoded = decode_report(blob, encoding=envelope.encoding,
                                max_decompressed_bytes=store._settings.verification_report_max_decompressed_bytes)
        _, _, size, hash_value = encode_report(decoded)
        output = VerifyWorkflowOutput.model_validate(decoded)
        if size != envelope.uncompressed_bytes or hash_value != envelope.content_sha256 or (
            output.workflow_id != envelope.workflow_id or output.owner_sub != envelope.owner_sub
            or output.workflow_run_id != envelope.workflow_run_id
        ):
            raise ValueError('Backup report integrity mismatch')
    if apply:
        await store.ensure_indexes()
        await ledger.ensure_indexes()
        for doc in reports:
            existing = await store._collection.find_one({'workflow_id': doc['workflow_id']})
            if existing:
                if any(existing.get(k) != doc.get(k) for k in ('content_sha256', 'owner_sub', 'workflow_run_id')):
                    raise ReportIntegrityConflict('Restore would change an existing report')
                continue
            doc.pop('_id', None)
            ref = doc.get('artifact_reference')
            if ref:
                if store._file_service is None:
                    raise RuntimeError('Destination object store unavailable')
                doc['artifact_reference'] = await store._file_service.upload_bytes_immutable(
                    data[manifest['objects'][ref]], f"verification-restored/{doc['content_sha256']}")
            try:
                await store._collection.insert_one(doc)
            except DuplicateKeyError:
                current = await store._collection.find_one({'workflow_id': doc['workflow_id']})
                if not current or any(current.get(k) != doc.get(k) for k in ('content_sha256', 'owner_sub', 'workflow_run_id')):
                    raise ReportIntegrityConflict('Concurrent restore conflict')
            await store.get(doc['workflow_id'])
        for doc in records:
            doc.pop('_id', None)
            existing = await ledger._collection.find_one({'workflow_id': doc['workflow_id']})
            if existing and any(existing.get(k) != doc.get(k) for k in ('owner_sub', 'verify_type', 'workflow_input')):
                raise ReportIntegrityConflict('Restore would change existing request/ownership')
            await ledger._collection.update_one({'workflow_id': doc['workflow_id']}, {'$setOnInsert': doc}, upsert=True)
    return {'reports': len(reports), 'ledger': len(records), 'objects': len(manifest['objects'])}
