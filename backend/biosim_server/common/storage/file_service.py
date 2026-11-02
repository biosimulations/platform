from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

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

    @abstractmethod
    async def put_if_absent(self, file_contents: bytes, gcs_path: str) -> bool:
        """Create the object only if nothing exists at *gcs_path*; never overwrite.

        Returns True when this call created it, and False when an object was
        already there, whose bytes are left untouched.  A backend whose server
        reports a checksum of the stored bytes compares it with *file_contents*
        and raises ``IOError`` on a mismatch.
        """
        pass

    @abstractmethod
    async def exists(self, gcs_path: str) -> bool:
        pass

    @abstractmethod
    async def delete_file(self, gcs_path: str) -> None:
        """Delete the object.  Deleting a missing object is not an error."""
        pass

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
    def iter_objects(self, prefix: str) -> AsyncIterator[ListingItem]:
        """Every object whose name starts with *prefix*, read page by page.

        ``LastModified`` is timezone-aware UTC.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        pass