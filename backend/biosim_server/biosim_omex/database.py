import logging
from abc import abstractmethod, ABC
from collections.abc import Mapping
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, ReturnDocument
from pymongo.results import InsertOneResult
from typing_extensions import override

from biosim_server.config import get_settings
from biosim_server.biosim_omex.models import OmexFile

logger = logging.getLogger(__name__)

# "Publicly usable" as a Mongo predicate. `$in [..., None]` also matches documents
# with no `visibility` field -- legacy rows, which stay public (see OmexFile).
_PUBLIC_VISIBILITY_CLAUSE: dict[str, Any] = {"visibility": {"$in": ["public", None]}}


class OmexDatabaseService(ABC):
    @abstractmethod
    async def insert_omex_file(self, omex_file: OmexFile) -> OmexFile:
        pass

    @abstractmethod
    async def get_omex_file(self, file_hash_md5: str, owner_sub: str | None = None) -> OmexFile | None:
        """Exact resource lookup for the ``(file_hash_md5, owner_sub)`` pair.

        This is *not* an authorization boundary: it returns whatever resource
        that pair names, private included. Route handlers must go through
        ``find_accessible_omex_file`` instead.
        """
        pass

    @abstractmethod
    async def find_accessible_omex_file(
        self, file_hash_md5: str, viewer_sub: str | None = None
    ) -> OmexFile | None:
        """Resolve a hash to a resource the caller may actually use.

        Prefers the caller's own resource for that blob, then falls back to a
        public (or legacy) one. Another owner's private resource is never
        returned, so knowing a hash is not an access capability.
        """
        pass

    @abstractmethod
    async def upsert_omex_file(self, omex_file: OmexFile) -> OmexFile:
        """Insert the ``(file_hash_md5, owner_sub)`` resource, or return the existing one.

        Never overwrites an existing document: a concurrent or repeat ingest of
        the same bytes must not be able to rewrite ownership or visibility.
        """
        pass

    @abstractmethod
    async def find_blob_location(self, file_hash_md5: str) -> tuple[str, str] | None:
        """``(bucket_name, omex_gcs_path)`` for an already-stored blob, if any.

        Storage-layer dedup only -- the blob is content-addressed and shared, so
        this deliberately ignores ownership. It carries no ACL meaning and must
        never be used to decide access.
        """
        pass

    @abstractmethod
    async def set_omex_visibility(
        self, file_hash_md5: str, owner_sub: str | None, visibility: str
    ) -> bool:
        """Set visibility on one owner's resource. Returns False if it doesn't exist.

        Scoped to ``(file_hash_md5, owner_sub)`` so publishing your own archive
        can never flip somebody else's resource over the same blob.
        """
        pass

    @abstractmethod
    async def delete_omex_file(self, database_id: str) -> None:
        pass

    @abstractmethod
    async def delete_all_omex_files(self) -> None:
        pass

    @abstractmethod
    async def list_omex_files(self) -> list[OmexFile]:
        pass

    async def ensure_indexes(self) -> None:
        """Create or refresh indexes for the underlying store. Default: no-op."""
        return

    @abstractmethod
    async def close(self) -> None:
        pass


class OmexDatabaseServiceMongo(OmexDatabaseService):
    _db_client: AsyncIOMotorClient
    _omex_file_col: AsyncIOMotorCollection

    def __init__(self, db_client: AsyncIOMotorClient) -> None:
        self._db_client = db_client
        database = self._db_client.get_database(get_settings().mongodb_database)
        self._omex_file_col = database.get_collection(get_settings().mongodb_collection_omex)

    @override
    async def insert_omex_file(self, omex_file: OmexFile) -> OmexFile:
        if omex_file.database_id is not None:
            raise Exception("Cannot insert document that already has a database id")
        logger.info(f"Inserting OMEX file with hash {omex_file.file_hash_md5}")
        result: InsertOneResult = await self._omex_file_col.insert_one(omex_file.model_dump())
        if result.acknowledged:
            inserted_omex_file: OmexFile = omex_file.model_copy(deep=True)
            inserted_omex_file.database_id = str(result.inserted_id)
            return inserted_omex_file
        else:
            raise Exception("Insert failed")

    @staticmethod
    def _to_omex_file(document: Mapping[str, Any]) -> OmexFile:
        doc_dict = dict(document)
        doc_dict["database_id"] = str(document["_id"])
        del doc_dict["_id"]
        return OmexFile.model_validate(doc_dict)

    # @lru_cache
    @override
    async def get_omex_file(self, file_hash_md5: str, owner_sub: str | None = None) -> OmexFile | None:
        logger.info(f"Getting OMEX file with hash {file_hash_md5} for owner {owner_sub}")
        # Mongo matches a missing field against None, so owner_sub=None also finds
        # legacy rows written before the field existed.
        document = await self._omex_file_col.find_one(
            {"file_hash_md5": file_hash_md5, "owner_sub": owner_sub}
        )
        return self._to_omex_file(document) if document is not None else None

    @override
    async def find_accessible_omex_file(
        self, file_hash_md5: str, viewer_sub: str | None = None
    ) -> OmexFile | None:
        if viewer_sub:
            own = await self._omex_file_col.find_one(
                {"file_hash_md5": file_hash_md5, "owner_sub": viewer_sub}
            )
            if own is not None:
                return self._to_omex_file(own)
        document = await self._omex_file_col.find_one(
            {"file_hash_md5": file_hash_md5, **_PUBLIC_VISIBILITY_CLAUSE}
        )
        return self._to_omex_file(document) if document is not None else None

    @override
    async def upsert_omex_file(self, omex_file: OmexFile) -> OmexFile:
        if omex_file.database_id is not None:
            raise Exception("Cannot insert document that already has a database id")
        document = omex_file.model_dump()
        document.pop("database_id", None)
        # $setOnInsert + upsert is atomic: two concurrent ingests of the same bytes
        # by the same principal converge on one document and neither rewrites the
        # other's ownership/visibility. A plain check-then-insert could double-write.
        result = await self._omex_file_col.find_one_and_update(
            {"file_hash_md5": omex_file.file_hash_md5, "owner_sub": omex_file.owner_sub},
            {"$setOnInsert": document},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return self._to_omex_file(result)

    @override
    async def find_blob_location(self, file_hash_md5: str) -> tuple[str, str] | None:
        document = await self._omex_file_col.find_one(
            {"file_hash_md5": file_hash_md5},
            {"bucket_name": 1, "omex_gcs_path": 1},
        )
        if document is None:
            return None
        return str(document["bucket_name"]), str(document["omex_gcs_path"])

    @override
    async def set_omex_visibility(
        self, file_hash_md5: str, owner_sub: str | None, visibility: str
    ) -> bool:
        logger.info(f"Setting OMEX ({file_hash_md5}, {owner_sub}) visibility to {visibility}")
        result = await self._omex_file_col.update_one(
            {"file_hash_md5": file_hash_md5, "owner_sub": owner_sub},
            {"$set": {"visibility": visibility}},
        )
        return result.matched_count > 0

    @override
    async def delete_omex_file(self, database_id: str) -> None:
        logger.info(f"Deleting OMEX file with database_id {database_id}")
        result = await self._omex_file_col.delete_one({"_id": ObjectId(database_id)})
        if result.deleted_count == 1:
            return
        else:
            raise Exception("Delete failed")

    @override
    async def delete_all_omex_files(self) -> None:
        logger.info("Deleting all OMEX file records")
        result = await self._omex_file_col.delete_many({})
        if not result.acknowledged:
            raise Exception("Delete failed")

    @override
    async def list_omex_files(self) -> list[OmexFile]:
        # Internal/admin use only -- deliberately not exposed over HTTP, since it
        # would enumerate every owner's private archives.
        logger.info("listing OMEX files")
        return [self._to_omex_file(doc) for doc in await self._omex_file_col.find().to_list(length=100)]

    @override
    async def ensure_indexes(self) -> None:
        # Every OMEX pipeline starts with a lookup by hash (blob dedup, and the
        # public-visibility fallback in find_accessible_omex_file).
        await self._omex_file_col.create_index("file_hash_md5")
        # The resource key: (blob, owner). Serves both the owner-scoped lookup and
        # the upsert filter. Left non-unique on purpose -- collections written
        # before owner_sub existed can hold duplicate (hash, null) rows from the
        # old check-then-insert race, and a unique index would fail to build
        # against them at startup. upsert_omex_file's $setOnInsert is what makes
        # concurrent ingest safe, so uniqueness isn't load-bearing here.
        await self._omex_file_col.create_index([("file_hash_md5", ASCENDING), ("owner_sub", ASCENDING)])

    @override
    async def close(self) -> None:
        self._db_client.close()
