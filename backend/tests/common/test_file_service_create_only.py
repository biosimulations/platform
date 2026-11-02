"""Create-only writes, existence checks and idempotent deletes on each FileService backend (WP-7a)."""
import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from botocore.exceptions import ClientError

from biosim_server.common.storage.file_service_local import FileServiceLocal
from biosim_server.common.storage.file_service_minio import FileServiceMinio


@pytest.mark.asyncio
async def test_local_put_if_absent_never_overwrites(tmp_path: Path) -> None:
    files = FileServiceLocal(tmp_path)
    assert await files.exists("a/b.json") is False
    assert await files.put_if_absent(b"first", "a/b.json") is True
    assert await files.put_if_absent(b"second", "a/b.json") is False
    assert await files.get_file_contents("a/b.json") == b"first"
    assert await files.exists("a/b.json") is True
    assert sorted(path.name for path in (tmp_path / "a").iterdir()) == ["b.json"]  # no temporary files left


@pytest.mark.asyncio
async def test_local_concurrent_creators_produce_one_object(tmp_path: Path) -> None:
    files = FileServiceLocal(tmp_path)
    created = await asyncio.gather(*(files.put_if_absent(f"writer-{index}".encode(), "same.json")
                                     for index in range(10)))
    assert created.count(True) == 1
    winner = created.index(True)
    assert await files.get_file_contents("same.json") == f"writer-{winner}".encode()
    assert [path.name for path in tmp_path.iterdir()] == ["same.json"]


@pytest.mark.asyncio
async def test_local_delete_is_idempotent(tmp_path: Path) -> None:
    files = FileServiceLocal(tmp_path)
    await files.delete_file("missing.json")
    await files.put_if_absent(b"x", "present.json")
    await files.delete_file("present.json")
    assert await files.exists("present.json") is False


# --- MinIO / S3, with the server's responses stubbed -----------------------------------------


def _client_error(status: int, code: str, operation: str = "PutObject") -> ClientError:
    response: Any = {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
    return ClientError(response, operation)


def _minio(s3: Any) -> FileServiceMinio:
    files = FileServiceMinio()

    @asynccontextmanager
    async def client() -> AsyncIterator[Any]:
        yield s3

    files._client = client  # type: ignore[method-assign]
    return files


def _md5(data: bytes) -> str:
    return '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"'


@pytest.mark.asyncio
async def test_minio_conditional_put() -> None:
    s3 = AsyncMock()
    s3.put_object.return_value = {"ETag": _md5(b"bytes")}
    files = _minio(s3)
    assert await files.put_if_absent(b"bytes", "key") is True
    assert s3.put_object.await_args.kwargs["IfNoneMatch"] == "*"
    s3.put_object.side_effect = _client_error(412, "PreconditionFailed")
    assert await files.put_if_absent(b"bytes", "key") is False


@pytest.mark.asyncio
async def test_minio_checks_the_single_part_etag() -> None:
    s3 = AsyncMock()
    s3.put_object.return_value = {"ETag": _md5(b"other")}
    with pytest.raises(IOError, match="does not match"):
        await _minio(s3).put_if_absent(b"bytes", "key")
    s3.put_object.return_value = {"ETag": '"0123-2"'}  # a multipart ETag is not an MD5
    assert await _minio(s3).put_if_absent(b"bytes", "key") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("present", [True, False])
async def test_minio_without_conditional_writes_checks_then_puts(present: bool) -> None:
    s3 = AsyncMock()
    s3.put_object.side_effect = [_client_error(501, "NotImplemented"), {"ETag": _md5(b"bytes")}]
    if not present:
        s3.head_object.side_effect = _client_error(404, "404", "HeadObject")
    assert await _minio(s3).put_if_absent(b"bytes", "key") is (not present)
    assert s3.put_object.await_count == (1 if present else 2)
    if not present:
        assert "IfNoneMatch" not in s3.put_object.await_args.kwargs


@pytest.mark.asyncio
async def test_minio_other_errors_propagate() -> None:
    s3 = AsyncMock()
    s3.put_object.side_effect = _client_error(403, "AccessDenied")
    with pytest.raises(ClientError):
        await _minio(s3).put_if_absent(b"bytes", "key")


@pytest.mark.asyncio
async def test_minio_missing_objects() -> None:
    s3 = AsyncMock()
    s3.head_object.side_effect = _client_error(404, "404", "HeadObject")
    files = _minio(s3)
    assert await files.exists("key") is False
    with pytest.raises(FileNotFoundError):
        await files.get_modified_date("key")
    s3.head_object.side_effect = None
    s3.head_object.return_value = {"LastModified": datetime(2026, 1, 1, tzinfo=UTC)}
    assert await files.exists("key") is True
    assert await files.get_modified_date("key") == datetime(2026, 1, 1, tzinfo=UTC)
    s3.head_object.side_effect = _client_error(403, "AccessDenied", "HeadObject")
    with pytest.raises(ClientError):
        await files.exists("key")
    with pytest.raises(ClientError):
        await files.get_modified_date("key")
    await files.delete_file("key")
    s3.delete_object.assert_awaited_once()
