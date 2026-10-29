from abc import ABC, abstractmethod

from pydantic import BaseModel
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from datetime import datetime
from pathlib import Path
from typing import Optional


class ListingItem(BaseModel):
    Key: str
    LastModified: datetime
    ETag: str
    Size: int


class FileService(ABC):

    @abstractmethod
    async def download_file(self, gcs_path: str, file_path: Optional[Path] = None) -> tuple[str, str]:
        pass

    @abstractmethod
    async def upload_file(self, file_path: Path, gcs_path: str) -> str:
        pass

    @abstractmethod
    async def upload_bytes(self, file_contents: bytes, gcs_path: str) -> str:
        pass

    async def upload_bytes_immutable(self, file_contents: bytes, gcs_path: str) -> str:
        """Publish an object without replacing an existing reference.

        Each attempt uses a unique object and verifies its bytes before publishing
        the returned reference. The insert-once report manifest selects the winner;
        retries can leave unreferenced objects, but cannot overwrite a published one.
        """
        import uuid
        reference = f"{gcs_path}.{uuid.uuid4().hex}"
        await self.upload_bytes(file_contents, reference)
        if await self.get_file_contents(reference) != file_contents:
            raise IOError("immutable object verification failed")
        return reference

    @abstractmethod
    async def get_modified_date(self, gcs_path: str) -> datetime:
        pass

    @abstractmethod
    async def get_listing(self, gcs_path: str) -> list[ListingItem]:
        pass

    @abstractmethod
    async def get_file_contents(self, gcs_path: str) -> bytes | None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass