"""Unit tests for the VerificationDatabaseServiceMongo ledger.

Uses a real MongoDB testcontainer (session-scoped) so index creation and
ordering guarantees are tested against actual MongoDB behaviour.
"""

import base64
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from pymongo.errors import DuplicateKeyError

from biosim_server.biosim_verify.database import (
    VERIFICATION_CURSOR_MAX_LENGTH,
    InvalidVerificationCursor,
    VerificationCursor,
    VerificationDatabaseServiceMongo,
    decode_verification_cursor,
    encode_verification_cursor,
)
from biosim_server.biosim_verify.models import (
    MAX_WORKFLOW_ID_BYTES,
    VERIFICATION_IDS_MAX_PAGE_SIZE,
    WORKFLOW_ID_PREFIX_MAX_LENGTH,
    VerificationRecord,
    VerificationType,
)


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

async def _all_ids(
    svc: VerificationDatabaseServiceMongo, owner_sub: str | None, *, limit: int = 100
) -> tuple[list[str], list[int]]:
    """Traverse every page by following next_cursor; return ids and page sizes."""
    ids: list[str] = []
    sizes: list[int] = []
    after: VerificationCursor | None = None
    while True:
        page = await svc.list_verification_ids(owner_sub, limit=limit, after=after)
        ids.extend(page.verification_ids)
        sizes.append(len(page.verification_ids))
        if page.next_cursor is None:
            return ids, sizes
        # Round-trip through the public token, exactly as an API client would.
        after = decode_verification_cursor(encode_verification_cursor(page.next_cursor))


@pytest.mark.asyncio
async def test_list_all_returns_newest_first(svc: VerificationDatabaseServiceMongo) -> None:
    """3 records inserted out of order → returned newest-first (no owner filter)."""
    await svc.insert_verification(_omex_record("wf-a", "u1", _utc(2025, 1, 1)))
    await svc.insert_verification(_omex_record("wf-b", "u1", _utc(2025, 1, 3)))
    await svc.insert_verification(VerificationRecord(
        workflow_id="wf-c", verify_type=VerificationType.RUNS, owner_sub="u2", created=_utc(2025, 1, 2),
    ))

    page = await svc.list_verification_ids(None, limit=10)
    assert page.verification_ids == ["wf-b", "wf-c", "wf-a"]
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_list_same_created_tiebreak_by_workflow_id(svc: VerificationDatabaseServiceMongo) -> None:
    """Identical created timestamps → tiebreak is workflow_id ascending."""
    ts = _utc(2025, 6, 1)
    await svc.insert_verification(_omex_record("wf-z", "u1", ts))
    await svc.insert_verification(_omex_record("wf-a", "u1", ts))
    await svc.insert_verification(_omex_record("wf-m", "u1", ts))

    page = await svc.list_verification_ids(None, limit=10)
    assert page.verification_ids == ["wf-a", "wf-m", "wf-z"]


@pytest.mark.asyncio
async def test_list_owner_scoped_returns_only_own(svc: VerificationDatabaseServiceMongo) -> None:
    """owner_sub filter returns only that caller's rows."""
    await svc.insert_verification(_omex_record("wf-own-1", "auth0|alice", _utc(2025, 1, 2)))
    await svc.insert_verification(_omex_record("wf-own-2", "auth0|alice", _utc(2025, 1, 1)))
    await svc.insert_verification(_omex_record("wf-other", "auth0|bob",   _utc(2025, 1, 3)))

    ids, _ = await _all_ids(svc, "auth0|alice")
    assert ids == ["wf-own-1", "wf-own-2"]


@pytest.mark.asyncio
async def test_list_empty_collection(svc: VerificationDatabaseServiceMongo) -> None:
    """Empty collection → empty page, no continuation."""
    page = await svc.list_verification_ids(None, limit=10)
    assert page.verification_ids == []
    assert page.next_cursor is None


# ---------------------------------------------------------------------------
# list_verification_ids — bounded, stable pagination (PR #120, B3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pagination_traverses_every_row_once_across_equal_timestamps(
    svc: VerificationDatabaseServiceMongo,
) -> None:
    tie = _utc(2025, 7, 1, 12)
    # BSON keeps milliseconds: these two differ only below 1 ms and tie once stored.
    sub_ms_a = datetime(2025, 7, 1, 11, 0, 0, 123100, tzinfo=timezone.utc)
    sub_ms_b = datetime(2025, 7, 1, 11, 0, 0, 123900, tzinfo=timezone.utc)
    rows = [
        ("wf-t3", tie), ("wf-t1", tie), ("wf-t2", tie),
        ("wf-ms-b", sub_ms_a), ("wf-ms-a", sub_ms_b),
        ("wf-new", _utc(2025, 8, 1)), ("wf-mid", _utc(2025, 7, 1, 13)), ("wf-old", _utc(2025, 1, 1)),
    ]
    for workflow_id, created in rows:
        await svc.insert_verification(_omex_record(workflow_id, None, created))

    ids, sizes = await _all_ids(svc, None, limit=3)

    assert ids == ["wf-new", "wf-mid", "wf-t1", "wf-t2", "wf-t3", "wf-ms-a", "wf-ms-b", "wf-old"]
    assert all(size <= 3 for size in sizes)
    assert len(ids) == len(set(ids)) == len(rows)


@pytest.mark.asyncio
async def test_pagination_exact_multiple_of_page_size_has_no_empty_trailing_page(
    svc: VerificationDatabaseServiceMongo,
) -> None:
    for day in range(1, 7):
        await svc.insert_verification(_omex_record(f"wf-{day}", None, _utc(2025, 2, day)))

    ids, sizes = await _all_ids(svc, None, limit=3)
    assert sizes == [3, 3]
    assert ids == [f"wf-{day}" for day in range(6, 0, -1)]


@pytest.mark.asyncio
async def test_rows_inserted_mid_traversal_do_not_duplicate_or_skip(
    svc: VerificationDatabaseServiceMongo,
) -> None:
    for day in range(1, 6):
        await svc.insert_verification(_omex_record(f"wf-{day}", None, _utc(2025, 3, day)))

    first = await svc.list_verification_ids(None, limit=2)
    assert first.verification_ids == ["wf-5", "wf-4"]
    assert first.next_cursor is not None
    await svc.insert_verification(_omex_record("wf-newer", None, _utc(2025, 4, 1)))

    rest: list[str] = []
    after: VerificationCursor | None = first.next_cursor
    while after is not None:
        page = await svc.list_verification_ids(None, limit=2, after=after)
        rest.extend(page.verification_ids)
        after = page.next_cursor
    assert rest == ["wf-3", "wf-2", "wf-1"]


@pytest.mark.asyncio
async def test_owner_scoped_pagination(svc: VerificationDatabaseServiceMongo) -> None:
    for day in range(1, 6):
        await svc.insert_verification(_omex_record(f"wf-alice-{day}", "auth0|alice", _utc(2025, 5, day)))
        await svc.insert_verification(_omex_record(f"wf-bob-{day}", "auth0|bob", _utc(2025, 5, day)))

    ids, sizes = await _all_ids(svc, "auth0|alice", limit=2)
    assert ids == [f"wf-alice-{day}" for day in range(5, 0, -1)]
    assert sizes == [2, 2, 1]


@pytest.mark.asyncio
async def test_ensure_indexes_creates_the_sort_index(svc: VerificationDatabaseServiceMongo) -> None:
    keys = [info["key"] for info in (await svc._collection.index_information()).values()]
    assert [("created", -1), ("workflow_id", 1)] in keys
    assert [("owner_sub", 1), ("created", -1), ("workflow_id", 1)] in keys


@pytest.mark.parametrize("limit", [0, VERIFICATION_IDS_MAX_PAGE_SIZE + 1])
@pytest.mark.asyncio
async def test_page_size_outside_the_bound_is_refused(limit: int) -> None:
    svc = object.__new__(VerificationDatabaseServiceMongo)
    with pytest.raises(ValueError):
        await svc.list_verification_ids(None, limit=limit)


class _RecordingCursor:
    def __init__(self, docs: list[dict[str, object]]) -> None:
        self.docs = docs
        self.limits: list[int] = []
        self.to_list_lengths: list[int | None] = []

    def sort(self, _spec: object) -> "_RecordingCursor":
        return self

    def limit(self, n: int) -> "_RecordingCursor":
        self.limits.append(n)
        return self

    async def to_list(self, length: int | None) -> list[dict[str, object]]:
        self.to_list_lengths.append(length)
        return self.docs[: length if length is not None else len(self.docs)]


class _RecordingCollection:
    def __init__(self, cursor: _RecordingCursor) -> None:
        self.cursor = cursor

    def find(self, *_args: object, **_kwargs: object) -> _RecordingCursor:
        return self.cursor


@pytest.mark.asyncio
async def test_page_read_is_bounded() -> None:
    """Each page is one limit+1 read: never ``to_list(length=None)``."""
    docs: list[dict[str, object]] = [
        {"workflow_id": f"wf-{i}", "created": datetime(2025, 1, 1, 0, 0, i)} for i in range(10)
    ]
    cursor = _RecordingCursor(docs)
    svc = object.__new__(VerificationDatabaseServiceMongo)
    svc._collection = _RecordingCollection(cursor)  # type: ignore[assignment]

    page = await svc.list_verification_ids(None, limit=4)

    assert cursor.limits == [5]
    assert cursor.to_list_lengths == [5]
    assert page.verification_ids == ["wf-0", "wf-1", "wf-2", "wf-3"]
    assert page.next_cursor == VerificationCursor(created=datetime(2025, 1, 1, 0, 0, 3), workflow_id="wf-3")


# ---------------------------------------------------------------------------
# cursor codec
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_id", ["omex-verification-x/y", "wf\nwith\nnewlines", "wf-\u00e9\U0001f600", 'wf-"\\'])
def test_cursor_round_trips(workflow_id: str) -> None:
    cursor = VerificationCursor(created=datetime(2025, 1, 2, 3, 4, 5, 678000), workflow_id=workflow_id)
    token = encode_verification_cursor(cursor)
    assert "=" not in token
    assert decode_verification_cursor(token) == cursor


def test_tz_aware_cursor_is_normalised_to_naive_utc() -> None:
    aware = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=2)))
    token = encode_verification_cursor(VerificationCursor(created=aware, workflow_id="wf"))
    assert decode_verification_cursor(token) == VerificationCursor(
        created=datetime(2025, 1, 2, 1, 4, 5), workflow_id="wf"
    )


def test_new_workflow_ids_fit_temporal_id_limit() -> None:
    """A maximal prefix of 4-byte characters plus the uuid4 suffix stays within the ID limit."""
    assert len(("\U0001f600" * WORKFLOW_ID_PREFIX_MAX_LENGTH + "x" * 36).encode()) <= MAX_WORKFLOW_ID_BYTES


# One character of each UTF-8 width, plus the characters JSON would escape
# (quote, backslash, control): a token's length must not depend on content.
@pytest.mark.parametrize("char", ["x", '"', "\\", "\x00", "\n", "\u00e9", "\u20ac", "\U0001f600"])
def test_cursor_for_any_storable_workflow_id_is_accepted(char: str) -> None:
    """Every ID Temporal can hold (<= MAX_WORKFLOW_ID_BYTES UTF-8 bytes) round-trips
    within VERIFICATION_CURSOR_MAX_LENGTH, at the longest timestamp the codec emits."""
    workflow_id = char * (MAX_WORKFLOW_ID_BYTES // len(char.encode()))
    cursor = VerificationCursor(created=datetime.max, workflow_id=workflow_id)
    token = encode_verification_cursor(cursor)
    assert len(token) <= VERIFICATION_CURSOR_MAX_LENGTH
    assert decode_verification_cursor(token) == cursor


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.mark.parametrize(
    "token",
    [
        "!!!",
        "",
        _b64(b"no separator"),
        _b64(b'{"c": "2025-01-01T00:00:00", "w": "wf"}'),  # the retired JSON form
        _b64(b"2025-01-01T00:00:00\n"),
        _b64(b"\nwf"),
        _b64(b"yesterday\nwf"),
        _b64(b"\xff\xfe\nwf"),
        # Parse, but overflow when normalised to UTC (PR #120 review: was a 500).
        _b64(b"0001-01-01T00:00:00+01:00\nwf"),
        _b64(b"9999-12-31T23:59:59-01:00\nwf"),
    ],
)
def test_malformed_cursor_is_rejected(token: str) -> None:
    with pytest.raises(InvalidVerificationCursor):
        decode_verification_cursor(token)


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
