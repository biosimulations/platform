"""Portable, verified backups of verification ledgers, manifests, and report objects.

This covers application verification records, not Temporal or the complete Mongo database.
A completed bundle is immutable operator evidence; partial bundles cannot be restored.
It reads and writes only through the report store's and the ledger's public
backup methods, never their collections or storage directly.
"""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bson import BSON

from biosim_server.biosim_verify.database import VerificationDatabaseService
from biosim_server.biosim_verify.report_storage import ReportIntegrityConflict, VerificationReportStore


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def export_bundle(directory: Path, store: VerificationReportStore,
                        ledger: VerificationDatabaseService) -> dict[str, Any]:
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
    async for doc in store.iter_envelopes():
        # Verify the source before claiming a usable backup.
        if await store.get(doc['workflow_id']) is None:
            raise RuntimeError('Report disappeared during backup')
        manifests.append(write(BSON.encode(doc), '.report.bson'))
        ref = doc.get('artifact_reference')
        if ref:
            payload = await store.read_artifact(ref)
            if payload is None:
                raise RuntimeError('Missing report object')
            objects[ref] = write(payload, '.object')
    async for doc in ledger.iter_rows():
        records.append(write(BSON.encode(doc), '.ledger.bson'))
    manifest = {'version': 1, 'complete': True, 'created_at': datetime.now(UTC).isoformat(),
                'reports': manifests, 'ledger': records, 'objects': objects, 'files': files}
    (directory / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2))
    (directory / 'manifest.json').chmod(0o600)
    return manifest


async def restore_bundle(directory: Path, store: VerificationReportStore,
                         ledger: VerificationDatabaseService, *, apply: bool = False) -> dict[str, int]:
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
    artifacts = {name: data[manifest['objects'][doc['artifact_reference']]] if doc.get('artifact_reference') else None
                 for name, doc in zip(manifest['reports'], reports)}
    # Validate all payloads before making any destination writes.
    for name, doc in zip(manifest['reports'], reports):
        payload = doc.get('inline_payload')
        store.check_envelope(doc, bytes(payload) if payload is not None else artifacts[name] or b'')
    if apply:
        await store.ensure_indexes()
        await ledger.ensure_indexes()
        for name, doc in zip(manifest['reports'], reports):
            await store.restore_envelope(doc, artifacts[name])
        for doc in records:
            existing = await ledger.get_verification(doc['workflow_id'])
            if existing is not None and (
                existing.owner_sub != doc.get('owner_sub') or existing.verify_type != doc.get('verify_type')
                or existing.workflow_input != doc.get('workflow_input')
            ):
                raise ReportIntegrityConflict('Restore would change existing request/ownership')
            await ledger.restore_row(doc)
    return {'reports': len(reports), 'ledger': len(records), 'objects': len(manifest['objects'])}
