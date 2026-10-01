"""Verification ledger persistence (BiosimCompare collection).

One document is written per verification workflow immediately before
``temporal_client.start_workflow`` in both POST /verify/* handlers.  This
enables GET /verification_ids (a public, cursor-paginated listing) and produces
an informative 404 detail for callers who query an expired Temporal history.
"""

import base64
import binascii
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING

from biosim_server.biosim_verify.models import VERIFICATION_IDS_MAX_PAGE_SIZE, VerificationRecord
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


class InvalidVerificationCursor(ValueError):
    """A ``cursor`` that is not a token this service issued."""


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def encode_verification_cursor(cursor: VerificationCursor) -> str:
    """Opaque, URL-safe continuation token (base64url of compact JSON, unpadded)."""
    payload = json.dumps(
        {"c": _naive_utc(cursor.created).isoformat(), "w": cursor.workflow_id}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_verification_cursor(token: str) -> VerificationCursor:
    """Inverse of :func:`encode_verification_cursor`; any other input is refused."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw)
    except (binascii.Error, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        raise InvalidVerificationCursor("cursor is not a valid token") from exc
    if not token or not isinstance(payload, dict) or set(payload) != {"c", "w"}:
        raise InvalidVerificationCursor("cursor has an unexpected shape")
    created, workflow_id = payload["c"], payload["w"]
    if not isinstance(created, str) or not isinstance(workflow_id, str) or not workflow_id:
        raise InvalidVerificationCursor("cursor fields have unexpected types")
    try:
        parsed = datetime.fromisoformat(created)
    except ValueError as exc:
        raise InvalidVerificationCursor("cursor timestamp is not ISO 8601") from exc
    return VerificationCursor(created=_naive_utc(parsed), workflow_id=workflow_id)


class VerificationDatabaseService(ABC):
    """Abstract interface for the verification ledger."""

    @abstractmethod
    async def insert_verification(self, record: VerificationRecord) -> VerificationRecord:
        """Insert a new record.  Raises on duplicate workflow_id."""
        ...

    @abstractmethod
    async def list_verification_ids(
        self, owner_sub: str | None, *, limit: int, after: VerificationCursor | None = None
    ) -> VerificationIdPage:
        """Return one page of workflow_id values, newest-first.

        ``owner_sub=None`` means no owner filter (all rows); ``owner_sub=<sub>``
        filters to that caller's rows. ``limit`` (1..VERIFICATION_IDS_MAX_PAGE_SIZE)
        bounds the read; ``after`` continues strictly past a previous page's
        ``next_cursor``, which is ``None`` on the last page.
        """
        ...

    @abstractmethod
    async def get_verification(self, workflow_id: str) -> VerificationRecord | None:
        """Return the record for *workflow_id*, or None if absent."""
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

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("workflow_id", unique=True)
        # Both serve the listing sort and its (created, workflow_id) range predicate.
        await self._collection.create_index([("owner_sub", ASCENDING), *_LISTING_SORT])
        await self._collection.create_index(_LISTING_SORT)

    async def insert_verification(self, record: VerificationRecord) -> VerificationRecord:
        doc = record.model_dump()
        await self._collection.insert_one(doc)
        return record

    async def list_verification_ids(
        self, owner_sub: str | None, *, limit: int, after: VerificationCursor | None = None
    ) -> VerificationIdPage:
        if not 1 <= limit <= VERIFICATION_IDS_MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {VERIFICATION_IDS_MAX_PAGE_SIZE}")
        clauses: list[dict[str, object]] = []
        if owner_sub is not None:
            clauses.append({"owner_sub": owner_sub})
        if after is not None:
            clauses.append({"$or": [
                {"created": {"$lt": after.created}},
                {"created": after.created, "workflow_id": {"$gt": after.workflow_id}},
            ]})
        query: dict[str, object] = {"$and": clauses} if clauses else {}
        # One extra row tells us whether another page exists; the read is bounded
        # however large the ledger grows.
        cursor = self._collection.find(
            query, projection={"workflow_id": 1, "created": 1, "_id": 0}
        ).sort(_LISTING_SORT).limit(limit + 1)
        docs = await cursor.to_list(length=limit + 1)
        page = docs[:limit]
        next_cursor = (
            VerificationCursor(created=page[-1]["created"], workflow_id=page[-1]["workflow_id"])
            if len(docs) > limit
            else None
        )
        return VerificationIdPage(
            verification_ids=[doc["workflow_id"] for doc in page], next_cursor=next_cursor
        )

    async def get_verification(self, workflow_id: str) -> VerificationRecord | None:
        doc = await self._collection.find_one(
            {"workflow_id": workflow_id}, projection={"_id": 0}
        )
        if doc is None:
            return None
        return VerificationRecord.model_validate(doc)

    async def delete_verification(self, workflow_id: str) -> None:
        await self._collection.delete_one({"workflow_id": workflow_id})

    async def delete_all_verifications(self) -> None:
        await self._collection.delete_many({})

    async def close(self) -> None:
        pass  # Motor client lifetime is managed externally
