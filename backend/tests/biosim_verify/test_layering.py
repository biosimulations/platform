"""Persistence internals stay inside their classes; derivation kinds are typed (S-7)."""
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.models import (
    DerivationKind,
    VerificationProvenance,
    VerificationRecord,
    VerificationType,
    VerifyWorkflowOutput,
    sanitize_workflow_error,
)
from biosim_server.biosim_verify.report_codec import encode_report
from tests.biosim_verify.test_report_storage import _output

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

BACKEND = Path(__file__).resolve().parents[2]
# Private state of the report store and the ledger: only `self` may touch it.
_PRIVATE = re.compile(r"(\w+)\.(_collection|_file_service|_idempotency|_maintenance|_settings)\b")


def test_no_production_code_reaches_into_persistence_internals() -> None:
    offenders = []
    for path in sorted([*(BACKEND / "biosim_server").rglob("*.py"), *(BACKEND / "scripts").rglob("*.py")]):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            for match in _PRIVATE.finditer(line):
                if match.group(1) != "self":
                    offenders.append(f"{path.relative_to(BACKEND)}:{number}: {match.group(0)}")
    assert offenders == []


def test_backup_uses_only_public_persistence_methods() -> None:
    source = (BACKEND / "biosim_server/biosim_verify/report_backup.py").read_text()
    for private in ("._collection", "._file_service", "._settings", "insert_one", "find_one", "update_one"):
        assert private not in source


@pytest.mark.asyncio
async def test_rows_written_with_plain_strings_still_validate(mongo_test_client: Any) -> None:
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await ledger.ensure_indexes()
    await ledger.delete_all_verifications()
    try:
        # A row as an older build wrote it: derivation_kind is a bare string.
        await ledger._collection.insert_one({
            "workflow_id": "old-derived", "verify_type": "runs", "created": datetime.now(UTC),
            "source_verification_id": "source", "derivation_kind": "reconstruction",
        })
        row = await ledger.get_verification("old-derived")
        assert row is not None and row.derivation_kind is DerivationKind.RECONSTRUCTION
        # New rows are stored as the enum's value, the same string.
        await ledger.insert_verification(VerificationRecord(
            workflow_id="new-derived", verify_type=VerificationType.OMEX, created=datetime.now(UTC),
            source_verification_id="source", derivation_kind=DerivationKind.REVERIFICATION,
        ))
        raw = await ledger._collection.find_one({"workflow_id": "new-derived"})
        assert raw is not None and raw["derivation_kind"] == "reverification" and type(raw["derivation_kind"]) is str
    finally:
        await ledger.delete_all_verifications()


def test_reports_and_provenance_keep_their_serialized_form() -> None:
    """The enum serializes to the same string, so stored content hashes do not change."""
    text_output = _output().model_dump(mode="json") | {"derivation_kind": "reconstruction"}
    typed = VerifyWorkflowOutput.model_validate(text_output)
    assert typed.derivation_kind is DerivationKind.RECONSTRUCTION
    assert typed.model_dump(mode="json") == text_output
    assert encode_report(typed.model_dump(mode="json"))[3] == encode_report(text_output)[3]
    provenance = VerificationProvenance.model_validate({"verification_id": "wf", "derivation_kind": "reverification"})
    assert provenance.derivation_kind is DerivationKind.REVERIFICATION
    with pytest.raises(ValidationError):
        VerificationProvenance.model_validate({"verification_id": "wf", "derivation_kind": "restoration"})


def test_workflow_errors_are_still_redacted() -> None:
    error = "boom at https://example.test/secret?x=1 in /Users/alice/run.py\nsecond line"
    assert sanitize_workflow_error(error) == "boom at [redacted-url] in [redacted-path]"
