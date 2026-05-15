import logging
import shutil
import uuid
from temporalio import workflow
with workflow.unsafe.imports_passed_through():
    from datetime import datetime
from pathlib import Path
from typing import List, Optional

import aiofiles
from typing_extensions import override

from biosim_server.config import get_local_cache_dir
from biosim_server.common.storage.file_service import FileService, ListingItem

logger = logging.getLogger(__name__)

def generate_fake_etag(file_path: Path) -> str:
    return file_path.absolute().as_uri()


class FileServiceLocal(FileService):
    """Filesystem-backed FileService.

    Default constructor (no args) creates an ephemeral random subdir under
    ``local_cache/local_data/`` and removes it on ``close()`` — appropriate for
    tests and short-lived processes.

    Passing ``base_dir`` uses that exact directory and does NOT remove it on
    close — appropriate for ``STORAGE_BACKEND=local`` in a real process where
    the cache must persist across restarts.
    """

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        cleanup_on_close: Optional[bool] = None,
    ) -> None:
        if base_dir is None:
            parent = get_local_cache_dir() / "local_data"
            parent.mkdir(parents=True, exist_ok=True)
            base_dir = parent / ("local_" + uuid.uuid4().hex)
            if cleanup_on_close is None:
                cleanup_on_close = True
        elif cleanup_on_close is None:
            cleanup_on_close = False
        self.base_dir: Path = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_on_close: bool = cleanup_on_close
        self.files_written: list[Path] = []

    @override
    async def close(self) -> None:
        if self.cleanup_on_close:
            shutil.rmtree(self.base_dir, ignore_errors=True)

    @override
    async def download_file(self, gcs_path: str, file_path: Optional[Path]=None) -> tuple[str, str]:
        logger.info(f"Downloading {gcs_path} to {file_path}")
        if file_path is None:
            file_path = get_local_cache_dir() / ("temp_file_"+uuid.uuid4().hex)
        src = self.base_dir / gcs_path
        local_file_path = Path(file_path)
        local_file_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(src, mode='rb') as f:
            contents = await f.read()
            async with aiofiles.open(local_file_path, mode='wb') as f2:
                await f2.write(contents)
        return str(gcs_path), str(local_file_path)

    @override
    async def upload_file(self, file_path: Path, gcs_path: str) -> str:
        logger.info(f"Uploading {file_path} to {gcs_path}")
        local_file_path = Path(file_path)
        dest = self.base_dir / gcs_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(local_file_path, mode='rb') as f:
            contents = await f.read()
            async with aiofiles.open(dest, mode='wb') as f2:
                await f2.write(contents)
        self.files_written.append(dest)
        return str(gcs_path)

    @override
    async def upload_bytes(self, file_contents: bytes, gcs_path: str) -> str:
        logger.info(f"Uploading bytes to {gcs_path}")
        dest = self.base_dir / gcs_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(dest, mode='wb') as f:
            await f.write(file_contents)
        self.files_written.append(dest)
        return str(gcs_path)

    @override
    async def get_modified_date(self, gcs_path: str) -> datetime:
        target = self.base_dir / gcs_path
        return datetime.fromtimestamp(target.stat().st_mtime)

    @override
    async def get_listing(self, gcs_path: str) -> List[ListingItem]:
        dir_path = self.base_dir / gcs_path
        return [ListingItem(Key=str(file.relative_to(self.base_dir)), Size=file.stat().st_size,
                            LastModified=datetime.fromtimestamp(file.stat().st_mtime), ETag=generate_fake_etag(file))
                for file in dir_path.rglob("*")]

    @override
    async def get_file_contents(self, gcs_path: str) -> bytes | None:
        target = self.base_dir / gcs_path
        if not target.exists():
            return None
        return target.read_bytes()
