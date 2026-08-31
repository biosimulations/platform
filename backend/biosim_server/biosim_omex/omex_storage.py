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
from biosim_server.biosim_omex.models import OmexFile, OmexVisibility

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


def default_visibility_for(owner_sub: str | None) -> OmexVisibility:
    """Server-authoritative ingest defaults: authenticated -> private, anonymous -> public.

    Deriving this from the (verified) owner rather than taking it as a parameter
    is deliberate -- there is no code path, and no request field, that can ingest
    an archive as somebody else's or as public-while-owned.
    """
    return "private" if owner_sub else "public"


async def get_cached_omex_file_from_upload(file_service: FileService, omex_database: OmexDatabaseService,
                                           uploaded_file: UploadFile, owner_sub: str | None = None) -> OmexFile:
    contents = await uploaded_file.read()
    return await get_cached_omex_file_from_raw(file_service, omex_database, contents, uploaded_file.filename,
                                               owner_sub=owner_sub)


async def get_cached_omex_file_from_local(file_service: FileService, omex_database: OmexDatabaseService,
                                          omex_file: Path, filename: str, owner_sub: str | None = None) -> OmexFile:
    async with aiofiles_open(omex_file, 'rb') as file:
        contents = await file.read()
    return await get_cached_omex_file_from_raw(file_service, omex_database, contents, filename, owner_sub=owner_sub)


async def get_cached_omex_file_from_raw(file_service: FileService, omex_database: OmexDatabaseService,
                                        omex_file_contents: bytes, filename: str | None,
                                        owner_sub: str | None = None) -> OmexFile:
    """Ingest an OMEX archive as ``owner_sub``'s resource, deduplicating the blob.

    ``owner_sub`` must come from a verified token (or be None for anonymous
    ingest) -- never from a request payload. Two separate identities exist here:

    * the **blob**, keyed by content hash and shared in GCS, and
    * the **resource**, keyed by ``(hash, owner_sub)`` in Mongo.

    So a cache hit only ever reuses the caller's *own* resource: uploading bytes
    that somebody else already uploaded gives you your own private resource over
    the shared blob, and never mutates or grants access to theirs.
    """
    file_hash_md5: str = hashlib.md5(omex_file_contents).hexdigest()
    logger.info(f"processing OMEX file with hash {file_hash_md5} for owner {owner_sub}")

    omex_file: OmexFile | None = await omex_database.get_omex_file(file_hash_md5=file_hash_md5, owner_sub=owner_sub)
    if omex_file is not None:
        logger.info(f"OMEX resource ({file_hash_md5}, {owner_sub}) already exists: {omex_file}")
        return omex_file

    logger.info(f"OMEX resource ({file_hash_md5}, {owner_sub}) does not exist; creating it")
    save_dest_dir = get_local_cache_dir() / "uploaded_files"
    save_dest_dir.mkdir(exist_ok=True)

    filename = Path(filename or (uuid.uuid4().hex + ".omex")).name

    # Blob-level dedup: the archive is content-addressed, so if any resource
    # already points at these exact bytes, reuse that object rather than
    # re-uploading. Storage sharing carries no ACL meaning -- the new resource
    # below still gets its own owner and visibility.
    existing_blob = await omex_database.find_blob_location(file_hash_md5=file_hash_md5)
    if existing_blob is not None:
        bucket_name, full_gcs_path = existing_blob
        logger.info(f"Reusing existing GCS object for hash {file_hash_md5} at {full_gcs_path}")
    else:
        gcs_path = str(Path("verify") / "omex" / f"{file_hash_md5}.omex")
        full_gcs_path = await file_service.upload_bytes(file_contents=omex_file_contents, gcs_path=gcs_path)
        bucket_name = get_settings().storage_bucket
        logger.info(f"Uploaded file to GCS at {full_gcs_path}")

    omex_file = OmexFile(file_hash_md5=file_hash_md5, omex_gcs_path=full_gcs_path, uploaded_filename=filename,
                         bucket_name=bucket_name, file_size=len(omex_file_contents),
                         owner_sub=owner_sub, visibility=default_visibility_for(owner_sub))
    return await omex_database.upsert_omex_file(omex_file=omex_file)
