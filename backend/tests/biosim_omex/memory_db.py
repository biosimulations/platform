"""In-memory OmexDatabaseService for unit tests that must not require Docker."""

from typing_extensions import override

from biosim_server.biosim_omex import OmexDatabaseService, OmexFile
from biosim_server.common.auth.roles import resource_is_public


class MemoryOmexDb(OmexDatabaseService):
    def __init__(self) -> None:
        self.rows: list[OmexFile] = []

    @override
    async def insert_omex_file(self, omex_file: OmexFile) -> OmexFile:
        copy = omex_file.model_copy()
        copy.database_id = str(len(self.rows))
        self.rows.append(copy)
        return copy

    @override
    async def get_omex_file(self, file_hash_md5: str) -> OmexFile | None:
        for row in self.rows:
            if row.file_hash_md5 == file_hash_md5:
                return row
        return None

    @override
    async def get_omex_file_by_hash_and_owner(
        self, file_hash_md5: str, owner: str | None
    ) -> OmexFile | None:
        # Mirrors OmexDatabaseServiceMongo: the owner-less (public) lookup is
        # decided by visibility alone -- owned rows with null/missing/public
        # visibility are publicly eligible; private rows never match it.
        for row in self.rows:
            if row.file_hash_md5 != file_hash_md5:
                continue
            if owner is None:
                if resource_is_public(row):
                    return row
            elif row.owner == owner:
                return row
        return None

    @override
    async def delete_omex_file(self, database_id: str) -> None:
        self.rows = [row for row in self.rows if row.database_id != database_id]

    @override
    async def delete_all_omex_files(self) -> None:
        self.rows = []

    @override
    async def list_omex_files(self) -> list[OmexFile]:
        return list(self.rows)

    @override
    async def close(self) -> None:
        return
