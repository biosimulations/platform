"""S3-compatible FileService backed by minio (or any S3 endpoint).

Targets the bucket / endpoint / credentials from ``Settings``:

- ``storage_bucket``
- ``storage_endpoint_url``  (e.g. ``http://localhost:9000`` for local minio)
- ``storage_region``
- ``storage_access_key``
- ``storage_secret_key``

The interface mirrors ``FileServiceGCS``: methods take an opaque ``gcs_path``
which is used as the S3 object key. The legacy parameter name is kept across
the family of services for symmetry; it is *not* a GCS-only construct.
"""

import hashlib
import logging
import re
import uuid
from temporalio import workflow
with workflow.unsafe.imports_passed_through():
    from datetime import datetime
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Optional

from aiobotocore.session import AioSession, get_session
from botocore.exceptions import ClientError
from types_aiobotocore_s3 import S3Client
from typing_extensions import override

from biosim_server.common.storage.file_service import FileService, ListingItem
from biosim_server.config import get_local_cache_dir, get_settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Error codes of a server that does not support conditional writes (If-None-Match).
_CONDITIONAL_WRITE_UNSUPPORTED = frozenset({"NotImplemented", "InvalidArgument", "InvalidRequest"})


def _status(error: ClientError) -> int | None:
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status if isinstance(status, int) else None


def _code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _is_missing(error: ClientError) -> bool:
    return _status(error) == 404 or _code(error) in {"404", "NoSuchKey", "NotFound"}


class FileServiceMinio(FileService):
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket: str = settings.storage_bucket
        self._endpoint_url: str = settings.storage_endpoint_url
        self._region: str = settings.storage_region
        self._access_key: str = settings.storage_access_key
        self._secret_key: str = settings.storage_secret_key
        self._session: AioSession = get_session()

    def _client(self) -> AbstractAsyncContextManager[S3Client]:
        return self._session.create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    @override
    async def download_file(self, gcs_path: str, file_path: Optional[Path]=None) -> tuple[str, str]:
        logger.info(f"Downloading {gcs_path} to {file_path}")
        if file_path is None:
            file_path = get_local_cache_dir() / ("temp_file_" + uuid.uuid4().hex)
        local_file_path = Path(file_path)
        local_file_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._client() as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=gcs_path)
            async with response["Body"] as stream:
                contents = await stream.read()
        local_file_path.write_bytes(contents)
        return str(gcs_path), str(local_file_path)

    @override
    async def upload_file(self, file_path: Path, gcs_path: str) -> str:
        logger.info(f"Uploading {file_path} to {gcs_path}")
        body = Path(file_path).read_bytes()
        async with self._client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=gcs_path, Body=body)
        return str(gcs_path)

    @override
    async def upload_bytes(self, file_contents: bytes, gcs_path: str) -> str:
        logger.info(f"Uploading {len(file_contents)} bytes to {gcs_path}")
        async with self._client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=gcs_path, Body=file_contents)
        return str(gcs_path)

    @override
    async def put_if_absent(self, file_contents: bytes, gcs_path: str) -> bool:
        async with self._client() as s3:
            try:
                response = await s3.put_object(Bucket=self._bucket, Key=gcs_path, Body=file_contents,
                                               IfNoneMatch="*")
            except ClientError as e:
                if _status(e) == 412 or _code(e) == "PreconditionFailed":
                    return False
                if _status(e) != 501 and _code(e) not in _CONDITIONAL_WRITE_UNSUPPORTED:
                    raise
                # No conditional writes: check, then put.  A race can only rewrite a
                # content-addressed name with the same bytes, which is harmless.
                logger.info(f"Conditional put unsupported; checking {gcs_path} before writing")
                try:
                    await s3.head_object(Bucket=self._bucket, Key=gcs_path)
                    return False
                except ClientError as missing:
                    if not _is_missing(missing):
                        raise
                response = await s3.put_object(Bucket=self._bucket, Key=gcs_path, Body=file_contents)
        # A single-part upload's ETag is the MD5 of the stored bytes; others are opaque.
        etag = str(response.get("ETag", "")).strip('"')
        if re.fullmatch(r"[0-9a-f]{32}", etag) and etag != hashlib.md5(file_contents, usedforsecurity=False).hexdigest():
            raise IOError(f"stored object {gcs_path} does not match the uploaded bytes")
        return True

    @override
    async def exists(self, gcs_path: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=gcs_path)
                return True
            except ClientError as e:
                if _is_missing(e):
                    return False
                raise

    @override
    async def delete_file(self, gcs_path: str) -> None:
        async with self._client() as s3:
            # S3 deletes are idempotent: a missing key is not an error.
            await s3.delete_object(Bucket=self._bucket, Key=gcs_path)

    @override
    async def get_modified_date(self, gcs_path: str) -> datetime:
        async with self._client() as s3:
            try:
                head = await s3.head_object(Bucket=self._bucket, Key=gcs_path)
            except ClientError as e:
                # The FileService contract: a missing object is FileNotFoundError.
                if _is_missing(e):
                    raise FileNotFoundError(gcs_path) from e
                raise
        return head["LastModified"]

    @override
    async def get_listing(self, gcs_path: str) -> list[ListingItem]:
        items: list[ListingItem] = []
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=gcs_path):
                for obj in page.get("Contents", []):
                    items.append(ListingItem(
                        Key=obj["Key"],
                        LastModified=obj["LastModified"],
                        ETag=obj["ETag"],
                        Size=obj["Size"],
                    ))
        return items

    @override
    async def iter_objects(self, prefix: str) -> AsyncIterator[ListingItem]:
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    yield ListingItem(Key=obj["Key"], LastModified=obj["LastModified"], ETag=obj["ETag"],
                                      Size=obj["Size"])

    @override
    async def get_file_contents(self, gcs_path: str) -> bytes | None:
        async with self._client() as s3:
            try:
                response = await s3.get_object(Bucket=self._bucket, Key=gcs_path)
            except s3.exceptions.NoSuchKey:
                return None
            async with response["Body"] as stream:
                data: bytes = await stream.read()
                return data

    @override
    async def close(self) -> None:
        # Clients are created per-request via context managers — no persistent state to close.
        return None
