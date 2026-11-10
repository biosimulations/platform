import asyncio
import base64
import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from aiohttp import ClientResponseError
from gcloud.aio.auth import Token
from gcloud.aio.storage import Storage
from gcloud.aio.storage.constants import DEFAULT_TIMEOUT

from biosim_server.common.storage import ListingItem
from biosim_server.config import get_settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class _StorageWithListPrefix(Storage):

    def __init__(self, token: Token):
        super().__init__(token=token)

    async def list_objects_with_prefix(self, bucket: str, prefix: str) -> Dict[str, Any]:
        encoded_prefix = quote(string=prefix, safe='')
        url = f'{self._api_root_read}/{bucket}/o?prefix={encoded_prefix}/'
        headers: dict[str, Any] = {}
        headers.update(await self._headers())

        s = self.session
        resp = await s.get(url=url, headers=headers, params={}, timeout=DEFAULT_TIMEOUT)
        data: Dict[str, Any] = await resp.json(content_type=None)
        return data


def create_token() -> Token:
    return Token(service_file=get_settings().storage_gcs_credentials_file,
                 scopes=["https://www.googleapis.com/auth/cloud-platform.read-only",
                         "https://www.googleapis.com/auth/devstorage.read_write"])

async def close_token(token: Token) -> None:
    if token.session:
        await token.close()


async def download_gcs_file(gcs_path: str, file_path: Path, token: Token) -> str:
    logger.info(f"Downloading {file_path} to {gcs_path}")
    async with Storage(token=token) as client:
        await client.download_to_filename(bucket=get_settings().storage_bucket, object_name=gcs_path, filename=str(file_path))
        return gcs_path


async def upload_file_to_gcs(file_path: Path, gcs_path: str, token: Token) -> str:
    logger.info(f"Uploading {file_path} to {gcs_path}")
    async with Storage(token=token) as client:
        result: dict[str, Any] = await client.upload_from_filename(bucket=get_settings().storage_bucket, object_name=gcs_path, filename=str(file_path))
        logger.info(f"Upload result: {result}")
        return gcs_path


async def upload_bytes_to_gcs(file_contents: bytes, gcs_path: str, token: Token) -> str:
    logger.info(f"Uploading {len(file_contents)} bytes to {gcs_path}")
    async with Storage(token=token) as client:
        await client.upload(bucket=get_settings().storage_bucket, file_data=file_contents, object_name=gcs_path)
        return gcs_path


async def put_gcs_object_if_absent(file_contents: bytes, gcs_path: str, token: Token) -> bool:
    """Create the object only if it does not exist.  True if this call created it."""
    logger.info(f"Creating {gcs_path} ({len(file_contents)} bytes) if absent")
    try:
        async with Storage(token=token) as client:
            # Generation 0 matches only a missing object, so the server refuses to overwrite.
            result: dict[str, Any] = await client.upload(
                bucket=get_settings().storage_bucket, object_name=gcs_path, file_data=file_contents,
                parameters={"ifGenerationMatch": "0"},
            )
    except ClientResponseError as e:
        # 412 Precondition Failed: an object already exists at this name.
        if e.status == 412:
            return False
        raise
    reported = result.get("md5Hash")
    expected = base64.b64encode(hashlib.md5(file_contents, usedforsecurity=False).digest()).decode()
    if reported is not None and reported != expected:
        raise IOError(f"stored object {gcs_path} does not match the uploaded bytes")
    return True


async def gcs_object_exists(gcs_path: str, token: Token) -> bool:
    try:
        async with Storage(token=token) as client:
            await client.download_metadata(bucket=get_settings().storage_bucket, object_name=gcs_path)
            return True
    except ClientResponseError as e:
        if e.status == 404:
            return False
        raise


async def delete_gcs_object(gcs_path: str, token: Token) -> None:
    logger.info(f"Deleting {gcs_path}")
    try:
        async with Storage(token=token) as client:
            await client.delete(bucket=get_settings().storage_bucket, object_name=gcs_path)
    except ClientResponseError as e:
        # Already gone is the outcome a delete wants.
        if e.status != 404:
            raise


async def get_gcs_modified_date(gcs_path: str, token: Token) -> datetime:
    logger.info(f"Getting modified date for {gcs_path}")
    try:
        async with Storage(token=token) as client:
            metadata: dict[str, Any] = await client.download_metadata(bucket=get_settings().storage_bucket, object_name=gcs_path)
            return datetime.fromisoformat(metadata["updated"])
    except ClientResponseError as e:
        # gcloud-aio reports a missing object as an HTTP 404, not FileNotFoundError.
        if e.status == 404:
            raise FileNotFoundError(gcs_path) from e
        raise


async def get_listing_of_gcs(token: Token) -> list[ListingItem]:
    logger.info("Retrieving file list from root of bucket")
    async with Storage(token=token) as client:
        metadata: dict[str, Any] = await client.list_objects(bucket=get_settings().storage_bucket)
        files: list[ListingItem] = [ListingItem(Key=item["id"], LastModified=datetime.fromisoformat(item["updated"]),
                                                Size=item["size"], ETag=item["etag"]) for item in metadata["items"]]
        return files


async def get_listing_of_gcs_path(gcs_path: str, token: Token) -> list[ListingItem]:
    logger.info(f"Retrieving file list from {gcs_path}")
    async with _StorageWithListPrefix(token=token) as my_client:
        assert isinstance(my_client, _StorageWithListPrefix)  # to avoid mypy error
        metadata: dict[str, Any] = await my_client.list_objects_with_prefix(bucket=get_settings().storage_bucket,
                                                                            prefix=gcs_path)
        files: list[ListingItem] = [ListingItem(Key=item["id"], LastModified=datetime.fromisoformat(item["updated"]),
                                                Size=item["size"], ETag=item["etag"]) for item in metadata["items"]]
        return files


async def iter_gcs_objects(prefix: str, token: Token, page_size: int = 1000) -> AsyncIterator[ListingItem]:
    """List every object under *prefix*, following ``nextPageToken``."""
    page_token: str | None = None
    async with Storage(token=token) as client:
        while True:
            params = {"prefix": prefix, "maxResults": str(page_size)}
            if page_token:
                params["pageToken"] = page_token
            page: dict[str, Any] = await client.list_objects(bucket=get_settings().storage_bucket, params=params)
            for item in page.get("items", []):
                yield ListingItem(Key=item["name"], LastModified=datetime.fromisoformat(item["updated"]),
                                  Size=int(item["size"]), ETag=item.get("etag", ""))
            page_token = page.get("nextPageToken")
            if not page_token:
                return


async def get_gcs_file_contents(gcs_path: str, token: Token) -> bytes | None:
    logger.info(f"Getting file contents for {gcs_path}")
    try:
        async with Storage(token=token) as client:
            return await client.download(bucket=get_settings().storage_bucket, object_name=gcs_path)
    except ClientResponseError as e:
        # A missing object is None (the FileService contract); other HTTP errors are not "missing".
        if e.status == 404:
            logger.info(f"File not found: {gcs_path}")
            return None
        raise

async def main() -> None:
    get_settings()
    token = create_token()
    # await download_gcs_file(gcs_path="local_data/toy_zarr/.zarray", file_path=Path(".zarray"), token=token)
    print(f"datetime is {await get_gcs_modified_date(gcs_path='local_data/toy_zarr/.zarray', token=token)}")
    # print(f"datetime is {await get_listing_of_gcs(token=token)}")
    print(f"datetime is {await get_listing_of_gcs_path(token=token, gcs_path='local_data/toy_zarr')}")
    await close_token(token)

if __name__ == "__main__":
    asyncio.run(main())