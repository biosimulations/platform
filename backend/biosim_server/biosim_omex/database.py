import logging
from abc import abstractmethod, ABC
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo.results import InsertOneResult
from typing_extensions import override

from biosim_server.config import get_settings
from biosim_server.biosim_omex.models import OmexFile

logger = logging.getLogger(__name__)


def _omex_from_document(document: dict[str, Any]) -> OmexFile:
    doc_dict = dict(document)
    doc_dict["database_id"] = str(document["_id"])
    del doc_dict["_id"]
    return OmexFile.model_validate(doc_dict)


class OmexDatabaseService(ABC):
    @abstractmethod
    async def insert_omex_file(self, omex_file: OmexFile) -> OmexFile:
        pass

    @abstractmethod
    async def get_omex_file(self, file_hash_md5: str) -> OmexFile | None:
        pass

    @abstractmethod
    async def get_omex_file_by_hash_and_owner(
        self, file_hash_md5: str, owner: str | None
    ) -> OmexFile | None:
        """``owner`` set: that caller's own row for this hash (any visibility).

        ``owner is None``: a publicly eligible row -- visibility null/missing
        (legacy) or explicit "public", regardless of whether an owner is
        populated. Never a private row.
        """
        pass

    async def get_omex_file_for_caller(
        self, file_hash_md5: str, *, owner: str | None
    ) -> OmexFile | None:
        """Prefer the caller's private row for this hash; otherwise the public row.

        Never returns another caller's private OMEX. ``owner is None`` is the
        anonymous/public lookup.
        """
        if owner is not None:
            private = await self.get_omex_file_by_hash_and_owner(file_hash_md5, owner)
            if private is not None:
                return private
        return await self.get_omex_file_by_hash_and_owner(file_hash_md5, None)

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

    # @lru_cache
    @override
    async def get_omex_file(self, file_hash_md5: str) -> OmexFile | None:
        logger.info(f"Getting OMEX file with hash {file_hash_md5}")
        document = await self._omex_file_col.find_one({"file_hash_md5": file_hash_md5})
        if document is None:
            return None
        return _omex_from_document(dict(document))

    @override
    async def get_omex_file_by_hash_and_owner(
        self, file_hash_md5: str, owner: str | None
    ) -> OmexFile | None:
        if owner is None:
            # Public eligibility is decided by visibility alone: null/missing
            # (legacy) or explicit "public" rows are publicly readable whether
            # or not an owner is populated. Only visibility == "private" is
            # excluded -- an owner field must never hide a public record, and
            # a private record must never leak through this fallback.
            query: dict[str, Any] = {
                "file_hash_md5": file_hash_md5,
                "$or": [
                    {"visibility": None},
                    {"visibility": {"$exists": False}},
                    {"visibility": "public"},
                ],
            }
        else:
            query = {"file_hash_md5": file_hash_md5, "owner": owner}
        document = await self._omex_file_col.find_one(query)
        if document is None:
            return None
        return _omex_from_document(dict(document))

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
        logger.info("listing OMEX files")
        omex_files: list[OmexFile] = []
        for document in await self._omex_file_col.find().to_list(length=100):
            omex_files.append(_omex_from_document(dict(document)))
        return omex_files

    @override
    async def ensure_indexes(self) -> None:
        # Hash is deliberately non-unique: authenticated callers each get their
        # own policy row for the same bytes. Logical uniqueness is (hash, owner).
        await self._omex_file_col.create_index("file_hash_md5")
        await self._omex_file_col.create_index([("file_hash_md5", 1), ("owner", 1)])

    @override
    async def close(self) -> None:
        self._db_client.close()
