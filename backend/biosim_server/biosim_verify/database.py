"""Verification ledger persistence (BiosimCompare collection).

One document is written per verification workflow immediately before
``temporal_client.start_workflow`` in both POST /verify/* handlers.  This
enables GET /verification_ids (owner-scoped listing) and produces an
informative 404 detail for callers who query an expired Temporal history.
"""

import logging
from abc import ABC, abstractmethod

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING

from biosim_server.biosim_verify.models import VerificationRecord
from biosim_server.config import get_settings

logger = logging.getLogger(__name__)


class VerificationDatabaseService(ABC):
    """Abstract interface for the verification ledger."""

    @abstractmethod
    async def insert_verification(self, record: VerificationRecord) -> VerificationRecord:
        """Insert a new record.  Raises on duplicate workflow_id."""
        ...

    @abstractmethod
    async def list_verification_ids(self, owner_sub: str | None) -> list[str]:
        """Return workflow_id values newest-first.

        ``owner_sub=None`` means no owner filter (admin: all rows).
        ``owner_sub=<sub>`` filters to that caller's rows.
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
        await self._collection.create_index(
            [("owner_sub", ASCENDING), ("created", DESCENDING)]
        )
        await self._collection.create_index([("created", DESCENDING)])

    async def insert_verification(self, record: VerificationRecord) -> VerificationRecord:
        doc = record.model_dump()
        await self._collection.insert_one(doc)
        return record

    async def list_verification_ids(self, owner_sub: str | None) -> list[str]:
        query: dict[str, object] = {} if owner_sub is None else {"owner_sub": owner_sub}
        cursor = self._collection.find(
            query,
            projection={"workflow_id": 1, "_id": 0},
        ).sort([("created", DESCENDING), ("workflow_id", ASCENDING)])
        docs = await cursor.to_list(length=None)
        return [doc["workflow_id"] for doc in docs]

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
