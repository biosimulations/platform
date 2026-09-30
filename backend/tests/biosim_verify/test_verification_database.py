"""Unit tests for the VerificationDatabaseServiceMongo ledger.

Uses a real MongoDB testcontainer (session-scoped) so index creation and
ordering guarantees are tested against actual MongoDB behaviour.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from pymongo.errors import DuplicateKeyError

from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.models import VerificationRecord, VerificationType


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _omex_record(workflow_id: str, owner: str | None, created: datetime) -> VerificationRecord:
    return VerificationRecord(
        workflow_id=workflow_id,
        verify_type=VerificationType.OMEX,
        owner_sub=owner,
        created=created,
    )


@pytest_asyncio.fixture
async def svc(
    verification_database_service_mongo: VerificationDatabaseServiceMongo,
) -> VerificationDatabaseServiceMongo:
    return verification_database_service_mongo


# ---------------------------------------------------------------------------
# list_verification_ids — ordering and owner scoping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_all_returns_newest_first(svc: VerificationDatabaseServiceMongo) -> None:
    """3 records inserted out of order → returned newest-first (no owner filter)."""
    await svc.insert_verification(_omex_record("wf-a", "u1", _utc(2025, 1, 1)))
    await svc.insert_verification(_omex_record("wf-b", "u1", _utc(2025, 1, 3)))
    await svc.insert_verification(VerificationRecord(
        workflow_id="wf-c", verify_type=VerificationType.RUNS, owner_sub="u2", created=_utc(2025, 1, 2),
    ))

    ids = await svc.list_verification_ids(None)
    assert ids == ["wf-b", "wf-c", "wf-a"]


@pytest.mark.asyncio
async def test_list_same_created_tiebreak_by_workflow_id(svc: VerificationDatabaseServiceMongo) -> None:
    """Identical created timestamps → tiebreak is workflow_id ascending."""
    ts = _utc(2025, 6, 1)
    await svc.insert_verification(_omex_record("wf-z", "u1", ts))
    await svc.insert_verification(_omex_record("wf-a", "u1", ts))
    await svc.insert_verification(_omex_record("wf-m", "u1", ts))

    ids = await svc.list_verification_ids(None)
    assert ids == ["wf-a", "wf-m", "wf-z"]


@pytest.mark.asyncio
async def test_list_owner_scoped_returns_only_own(svc: VerificationDatabaseServiceMongo) -> None:
    """owner_sub filter returns only that caller's rows."""
    await svc.insert_verification(_omex_record("wf-own-1", "auth0|alice", _utc(2025, 1, 2)))
    await svc.insert_verification(_omex_record("wf-own-2", "auth0|alice", _utc(2025, 1, 1)))
    await svc.insert_verification(_omex_record("wf-other", "auth0|bob",   _utc(2025, 1, 3)))

    ids = await svc.list_verification_ids("auth0|alice")
    assert ids == ["wf-own-1", "wf-own-2"]
    assert "wf-other" not in ids


@pytest.mark.asyncio
async def test_list_empty_collection(svc: VerificationDatabaseServiceMongo) -> None:
    """Empty collection → empty list."""
    ids = await svc.list_verification_ids(None)
    assert ids == []


# ---------------------------------------------------------------------------
# get_verification / delete_verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_hit_and_miss(svc: VerificationDatabaseServiceMongo) -> None:
    """get_verification returns the record on hit and None on miss."""
    rec = _omex_record("wf-get", "u1", _utc(2025, 3, 1))
    await svc.insert_verification(rec)

    result = await svc.get_verification("wf-get")
    assert result is not None
    assert result.workflow_id == "wf-get"
    assert result.owner_sub == "u1"
    assert result.verify_type == VerificationType.OMEX

    assert await svc.get_verification("wf-missing") is None


@pytest.mark.asyncio
async def test_delete_removes_row(svc: VerificationDatabaseServiceMongo) -> None:
    """delete_verification removes the row; subsequent get returns None."""
    await svc.insert_verification(_omex_record("wf-del", "u1", _utc(2025, 3, 1)))
    await svc.delete_verification("wf-del")
    assert await svc.get_verification("wf-del") is None


@pytest.mark.asyncio
async def test_delete_nonexistent_is_noop(svc: VerificationDatabaseServiceMongo) -> None:
    """delete_verification on a missing ID does not raise."""
    await svc.delete_verification("wf-never-existed")  # must not raise


# ---------------------------------------------------------------------------
# unique index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_workflow_id_raises(svc: VerificationDatabaseServiceMongo) -> None:
    """Inserting the same workflow_id twice violates the unique index."""
    rec = _omex_record("wf-dup", "u1", _utc(2025, 4, 1))
    await svc.insert_verification(rec)
    with pytest.raises(DuplicateKeyError):
        await svc.insert_verification(rec)
