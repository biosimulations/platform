"""Verification ledger persistence (BiosimCompare collection).

One document is written per verification workflow immediately before
``temporal_client.start_workflow`` in both POST /verify/* handlers.  This
enables GET /verification_ids (a caller-scoped, cursor-paginated listing) and produces
an informative 404 detail for callers who query an expired Temporal history.
"""

import base64
import binascii
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from collections.abc import Mapping
from typing import Any, Final

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING

from biosim_server.biosim_verify.models import (
    MAX_WORKFLOW_ID_BYTES,
    VERIFICATION_IDS_MAX_PAGE_SIZE,
    VerificationRecord,
    VerificationSummary,
    VerifyWorkflowStatus,
    ReportPersistenceState,
)
from biosim_server.config import get_settings

logger = logging.getLogger(__name__)

# Listing order: newest first, workflow_id (unique) as the tie-breaker, so the
# order is total and a (created, workflow_id) position is a stable continuation.
_LISTING_SORT = [("created", DESCENDING), ("workflow_id", ASCENDING)]


@dataclass(frozen=True)
class VerificationCursor:
    """Position of the last row on a page: ``(created, workflow_id)``.

    ``created`` is held exactly as Mongo returns it -- naive UTC, millisecond
    precision -- so rows that tie after BSON truncation still paginate by
    workflow_id.
    """

    created: datetime
    workflow_id: str


@dataclass(frozen=True)
class VerificationIdPage:
    verification_ids: list[str]
    next_cursor: VerificationCursor | None
    # The same rows as ``verification_ids``, in the same order, never carrying ``owner_sub``.
    verifications: list[VerificationSummary] = field(default_factory=list)


# Listing projection: everything a VerificationSummary needs and nothing else.
# ``owner_sub`` is deliberately absent so it can never reach a response.
_LISTING_PROJECTION: Final = {
    "workflow_id": 1, "verify_type": 1, "created": 1, "omex_hash": 1, "status": 1, "_id": 0,
}


def _summary_from_doc(doc: Mapping[str, Any]) -> VerificationSummary:
    created = doc["created"]
    assert isinstance(created, datetime)
    # The Motor client is not ``tz_aware`` (shared with every other collection), so
    # ``created`` comes back naive. It was stored as UTC; say so, or a browser's
    # ``new Date()`` reads it as local time.
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return VerificationSummary(
        workflow_id=doc["workflow_id"],
        verify_type=doc["verify_type"],
        created=created,
        omex_hash=doc.get("omex_hash"),
        status=doc.get("status"),
    )


class InvalidVerificationCursor(ValueError):
    """A ``cursor`` that is not a token this service issued."""


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo is not None else value


# The timestamp never contains it, so the first one ends the timestamp and the
# workflow ID (which may contain anything but "/") is everything after it.
_CURSOR_SEPARATOR = "\n"


def encode_verification_cursor(cursor: VerificationCursor) -> str:
    """Opaque, URL-safe continuation token: unpadded base64url of
    ``"<created ISO 8601>\\n<workflow_id>"`` in UTF-8.

    Deliberately not JSON: escaping grows a control character six-fold, so the
    token length would depend on the ID's content. Here it is a fixed function
    of the ID's UTF-8 size, which VERIFICATION_CURSOR_MAX_LENGTH bounds.
    """
    payload = f"{_naive_utc(cursor.created).isoformat()}{_CURSOR_SEPARATOR}{cursor.workflow_id}"
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


# The longest token the service can issue: the longest naive ISO timestamp and a
# workflow ID at Temporal's limit. GET /verification_ids accepts exactly this.
VERIFICATION_CURSOR_MAX_LENGTH: Final = len(
    encode_verification_cursor(VerificationCursor(created=datetime.max, workflow_id="x" * MAX_WORKFLOW_ID_BYTES))
)


def decode_verification_cursor(token: str) -> VerificationCursor:
    """Inverse of :func:`encode_verification_cursor`; any other input is refused."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        created, separator, workflow_id = raw.decode().partition(_CURSOR_SEPARATOR)
    except (binascii.Error, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        raise InvalidVerificationCursor("cursor is not a valid token") from exc
    if not separator or not workflow_id:
        raise InvalidVerificationCursor("cursor has an unexpected shape")
    try:
        # Normalising an offset timestamp at either end of the calendar
        # (e.g. 0001-01-01T00:00:00+01:00) overflows.
        parsed = _naive_utc(datetime.fromisoformat(created))
    except (ValueError, OverflowError) as exc:
        raise InvalidVerificationCursor("cursor timestamp is not a valid ISO 8601 instant") from exc
    return VerificationCursor(created=parsed, workflow_id=workflow_id)


class VerificationDatabaseService(ABC):
    """Abstract interface for the verification ledger."""

    @abstractmethod
    async def insert_verification(self, record: VerificationRecord) -> VerificationRecord:
        """Insert a new record.  Raises on duplicate workflow_id."""
        ...

    @abstractmethod
    async def list_verification_ids(
        self, owner_sub: str | None, *, limit: int, after: VerificationCursor | None = None,
        omex_hash: str | None = None,
    ) -> VerificationIdPage:
        """Return one page of workflow_id values (and their summaries), newest-first.

        ``owner_sub`` is the verified caller subject, not an arbitrary owner selector.
        ``None`` returns only ownerless (null or missing owner) rows; a subject
        returns ownerless rows plus that caller's own. There is no unfiltered mode.
        ``omex_hash`` narrows the page to rows about that archive, inside the same
        visibility. ``limit`` (1..VERIFICATION_IDS_MAX_PAGE_SIZE) bounds the read;
        ``after`` continues strictly past a previous page's ``next_cursor``, which
        is ``None`` on the last page.
        """
        ...

    @abstractmethod
    async def get_verification(self, workflow_id: str) -> VerificationRecord | None:
        """Return the record for *workflow_id*, or None if absent."""
        ...

    @abstractmethod
    async def record_terminal_status(self, workflow_id: str, status: VerifyWorkflowStatus) -> None:
        """Record the terminal *status* for *workflow_id*, once.

        A row whose status is already set is left alone, so the first observed
        terminal status sticks and a second call is a no-op. Absent rows are a no-op.
        """
        ...

    @abstractmethod
    async def record_report_persisted(self, workflow_id: str, report_id: str, status: VerifyWorkflowStatus) -> None:
        ...

    @abstractmethod
    async def list_persistence_candidates(
        self, limit: int, after_created: datetime | None = None, after_workflow_id: str | None = None,
    ) -> list[VerificationRecord]:
        ...

    @abstractmethod
    async def acquire_persistence_scan(self, namespace: str, owner: str) -> tuple[datetime | None, str | None] | None:
        ...

    @abstractmethod
    async def checkpoint_persistence_scan(self, namespace: str, owner: str, created: datetime | None,
                                          workflow_id: str | None, release: bool = False) -> None:
        ...

    @abstractmethod
    async def mark_persistence_issue(self, workflow_id: str, blocked: bool) -> None:
        ...

    @abstractmethod
    async def mark_persistence_unavailable(self, workflow_id: str, reason: str) -> None:
        ...

    @abstractmethod
    async def record_workflow_input(self, workflow_id: str, workflow_input: dict[str, object]) -> None:
        ...

    @abstractmethod
    async def record_workflow_run_id(self, workflow_id: str, workflow_run_id: str) -> None:
        ...

    @abstractmethod
    async def delete_verification(self, workflow_id: str) -> None:
        """Remove the record for *workflow_id*.  No-op if absent."""
        ...

    @abstractmethod
    async def delete_all_verifications(self) -> None:
        """Remove every record.  For test teardown only."""
        ...

    @abstractmethod
    async def ensure_indexes(self) -> None:
        """Create indexes idempotently.  Called at startup."""
        ...

    @abstractmethod
    async def close(self) -> None: ...


class VerificationDatabaseServiceMongo(VerificationDatabaseService):
    """MongoDB-backed verification ledger using the BiosimCompare collection."""

    def __init__(self, db_client: AsyncIOMotorClient) -> None:
        settings = get_settings()
        db = db_client.get_database(settings.mongodb_database)
        self._collection = db.get_collection(settings.mongodb_collection_compare)
        self._maintenance = db.get_collection("BiosimVerifyMaintenance")

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("workflow_id", unique=True)
        # Both serve the listing sort and its (created, workflow_id) range predicate.
        await self._collection.create_index([("owner_sub", ASCENDING), *_LISTING_SORT])
        await self._collection.create_index(_LISTING_SORT)
        # A hash-filtered page is served in listing order from the index, not sorted in memory.
        await self._collection.create_index([("omex_hash", ASCENDING), *_LISTING_SORT])

    async def insert_verification(self, record: VerificationRecord) -> VerificationRecord:
        doc = record.model_dump()
        await self._collection.insert_one(doc)
        return record

    async def list_verification_ids(
        self, owner_sub: str | None, *, limit: int, after: VerificationCursor | None = None,
        omex_hash: str | None = None,
    ) -> VerificationIdPage:
        if not 1 <= limit <= VERIFICATION_IDS_MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {VERIFICATION_IDS_MAX_PAGE_SIZE}")
        # Scope before pagination: hidden rows must not affect lookahead/cursors.
        visibility: dict[str, object] = (
            {"owner_sub": None}
            if owner_sub is None
            else {"owner_sub": {"$in": [None, owner_sub]}}
        )
        clauses: list[dict[str, object]] = [visibility]
        if omex_hash is not None:
            clauses.append({"omex_hash": omex_hash})
        if after is not None:
            # Equivalent to the lexicographic continuation predicate for
            # (created DESC, workflow_id ASC), expressed as one bounded range
            # so Mongo can preserve the compound index order.
            clauses.extend([
                {"created": {"$lte": after.created}},
                {"$nor": [{
                    "created": after.created,
                    "workflow_id": {"$lte": after.workflow_id},
                }]},
            ])
        query: dict[str, object] = {"$and": clauses}
        # One extra row tells us whether another page exists; the read is bounded
        # however large the ledger grows.
        cursor = self._collection.find(
            query, projection=_LISTING_PROJECTION
        ).sort(_LISTING_SORT).limit(limit + 1)
        docs = await cursor.to_list(length=limit + 1)
        page = docs[:limit]
        next_cursor = (
            VerificationCursor(created=page[-1]["created"], workflow_id=page[-1]["workflow_id"])
            if len(docs) > limit
            else None
        )

        return VerificationIdPage(
            verification_ids=[doc["workflow_id"] for doc in page],
            next_cursor=next_cursor,
            verifications=[_summary_from_doc(doc) for doc in page],
        )

    async def get_verification(self, workflow_id: str) -> VerificationRecord | None:
        doc = await self._collection.find_one(
            {"workflow_id": workflow_id}, projection={"_id": 0}
        )
        if doc is None:
            return None
        return VerificationRecord.model_validate(doc)

    async def record_terminal_status(self, workflow_id: str, status: VerifyWorkflowStatus) -> None:
        # ``{"status": None}`` matches a null status and a missing field alike, so rows
        # written before the field existed are covered; a set status never changes.
        await self._collection.update_one(
            {"workflow_id": workflow_id, "status": None},
            {"$set": {"status": status.value}},
        )

    async def record_report_persisted(self, workflow_id: str, report_id: str, status: VerifyWorkflowStatus) -> None:
        await self._collection.update_one(
            {"workflow_id": workflow_id},
            {"$set": {
                "report_id": report_id, "status": status.value,
                "persistence_state": ReportPersistenceState.STORED.value,
                "persisted_at": datetime.now(UTC), "persistence_error": None,
            }},
        )

    async def list_persistence_candidates(
        self, limit: int, after_created: datetime | None = None, after_workflow_id: str | None = None,
    ) -> list[VerificationRecord]:
        query: dict[str, object] = {
            "$or": [
                {"persistence_state": {"$in": [ReportPersistenceState.PENDING.value, ReportPersistenceState.BLOCKED.value]}},
                {"persistence_state": {"$exists": False}},
            ]
        }
        if after_created is not None and after_workflow_id is not None:
            query["$and"] = [{
                "$or": [
                    {"created": {"$gt": after_created}},
                    {"created": after_created, "workflow_id": {"$gt": after_workflow_id}},
                ]
            }]
        docs = await self._collection.find(
            query, projection={"_id": 0}
        ).sort([("created", ASCENDING), ("workflow_id", ASCENDING)]).limit(limit).to_list(limit)
        return [VerificationRecord.model_validate(doc) for doc in docs]

    def _scan_collection(self) -> AsyncIOMotorCollection:
        return self._maintenance

    async def acquire_persistence_scan(self, namespace: str, owner: str) -> tuple[datetime | None, str | None] | None:
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError
        from datetime import timedelta
        collection = self._scan_collection()
        try:
            await collection.update_one({"_id": namespace}, {"$setOnInsert": {"after_created": None,
                "after_workflow_id": None, "lease_until": datetime.min.replace(tzinfo=UTC)}}, upsert=True)
        except DuplicateKeyError:
            pass
        now = datetime.now(UTC)
        doc = await collection.find_one_and_update({"_id": namespace, "lease_until": {"$lte": now}},
            {"$set": {"owner": owner, "lease_until": now + timedelta(minutes=5)}}, return_document=ReturnDocument.AFTER)
        return None if doc is None else (doc.get("after_created"), doc.get("after_workflow_id"))

    async def checkpoint_persistence_scan(self, namespace: str, owner: str, created: datetime | None,
                                          workflow_id: str | None, release: bool = False) -> None:
        now = datetime.now(UTC)
        values: dict[str, object] = {"after_created": created, "after_workflow_id": workflow_id,
                                     "updated_at": now}
        if release:
            values["lease_until"] = now
        result = await self._scan_collection().update_one({"_id": namespace, "owner": owner,
            "lease_until": {"$gt": now}}, {"$set": values})
        if result.matched_count != 1:
            raise RuntimeError("Reconciliation lease was lost")

    async def mark_persistence_issue(self, workflow_id: str, blocked: bool) -> None:
        await self._collection.update_one(
            {"workflow_id": workflow_id, "persistence_state": {"$ne": ReportPersistenceState.STORED.value}},
            {"$set": {"persistence_state": "blocked" if blocked else "pending",
                      "persistence_error": "Report integrity/encoding failure" if blocked else "Storage dependency unavailable",
                      "persistence_attempted_at": datetime.now(UTC)}},
        )

    async def mark_persistence_unavailable(self, workflow_id: str, reason: str) -> None:
        await self._collection.update_one(
            {"workflow_id": workflow_id},
            {"$set": {
                "persistence_state": ReportPersistenceState.UNAVAILABLE.value,
                "persistence_error": reason[:1000],
                "persistence_attempted_at": datetime.now(UTC),
            }},
        )

    async def record_workflow_input(self, workflow_id: str, workflow_input: dict[str, object]) -> None:
        from biosim_server.biosim_verify.report_codec import encode_report
        await self._collection.update_one(
            {"workflow_id": workflow_id, "workflow_input": None},
            {"$set": {"workflow_input": workflow_input, "request_schema_version": 1,
                      "request_hash": encode_report(workflow_input)[3]}},
        )

    async def record_workflow_run_id(self, workflow_id: str, workflow_run_id: str) -> None:
        await self._collection.update_one(
            {"workflow_id": workflow_id, "$or": [{"workflow_run_id": None}, {"workflow_run_id": {"$exists": False}}]},
            {"$set": {"workflow_run_id": workflow_run_id}},
        )

    async def delete_verification(self, workflow_id: str) -> None:
        await self._collection.delete_one({"workflow_id": workflow_id})

    async def delete_all_verifications(self) -> None:
        await self._collection.delete_many({})

    async def close(self) -> None:
        pass  # Motor client lifetime is managed externally
