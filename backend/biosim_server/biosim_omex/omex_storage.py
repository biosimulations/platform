import hashlib
import logging
import uuid
from pathlib import Path

import aiofiles
from aiofiles import open as aiofiles_open
from fastapi import UploadFile

from biosim_server.common.storage import FileService
from biosim_server.config import get_local_cache_dir, get_settings
from biosim_server.biosim_omex import OmexDatabaseService
from biosim_server.biosim_omex.models import OmexFile

logger = logging.getLogger(__name__)


async def hash_file_md5(file_path: Path) -> str:
    hash_func = hashlib.md5()
    async with aiofiles.open(file_path, 'rb') as file:
        while chunk := await file.read(8192):
            hash_func.update(chunk)
    return hash_func.hexdigest()


async def hash_bytes_md5(file_contents: bytes) -> str:
    hash_func = hashlib.md5()
    hash_func.update(file_contents)
    return hash_func.hexdigest()


async def get_cached_omex_file_from_upload(file_service: FileService, omex_database: OmexDatabaseService, uploaded_file: UploadFile) -> OmexFile:
    contents = await uploaded_file.read()
    return await get_cached_omex_file_from_raw(file_service, omex_database, contents, uploaded_file.filename)


async def get_cached_omex_file_from_local(file_service: FileService, omex_database: OmexDatabaseService, omex_file: Path, filename: str) -> OmexFile:
    async with aiofiles_open(omex_file, 'rb') as file:
        contents = await file.read()
    return await get_cached_omex_file_from_raw(file_service, omex_database, contents, filename)


async def get_cached_omex_file_from_raw(file_service: FileService, omex_database: OmexDatabaseService, omex_file_contents: bytes, filename: str | None) -> OmexFile:

    file_hash_md5: str = hashlib.md5(omex_file_contents).hexdigest()
    logger.info(f"processing downloaded OMEX file with hash {file_hash_md5}")

    omex_file: OmexFile | None = await omex_database.get_omex_file(file_hash_md5=file_hash_md5)

    if omex_file is None:
        logger.info(f"OMEX file with hash {file_hash_md5} does not exist in database, with upload to GCS and store in database")
        save_dest_dir = get_local_cache_dir() / "uploaded_files"
        save_dest_dir.mkdir(exist_ok=True)

        filename = Path(filename or (uuid.uuid4().hex + ".omex")).name
        # local_temp_path = save_dest_dir / filename
        # async with aiofiles_open(local_temp_path, 'wb') as file:
        #     await file.write(contents)

        gcs_path = str(Path("verify") / "omex" / f"{file_hash_md5}.omex")

        full_gcs_path: str = await file_service.upload_bytes(file_contents=omex_file_contents, gcs_path=gcs_path)
        logger.info(f"Uploaded file to GCS at {full_gcs_path}")
        omex_file = OmexFile(file_hash_md5=file_hash_md5, omex_gcs_path=full_gcs_path, uploaded_filename=filename,
                             bucket_name=get_settings().storage_bucket, file_size=len(omex_file_contents))
        await omex_database.insert_omex_file(omex_file=omex_file)
    else:
        logger.info(f"OMEX file with hash {file_hash_md5} already exists in database {str(omex_file)}")

    return omex_file


