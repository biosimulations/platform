"""GCS helpers honor the FileService missing-object and create-only contracts without credentials.

gcloud-aio-storage reports a missing object as ``aiohttp.ClientResponseError``
with status 404, never ``FileNotFoundError``, and a failed ``ifGenerationMatch``
precondition as status 412. These tests replace ``Storage`` with a fake that
raises exactly those, so the contracts are checked offline.
"""

import base64
import hashlib
from typing import Any

import pytest
from aiohttp import ClientResponseError, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from biosim_server.common.storage import gcs_aio


def gcs_error(status: int) -> ClientResponseError:
    url = URL("https://storage.googleapis.com/storage/v1/b/bucket/o/object")
    info = RequestInfo(url=url, method="GET", headers=CIMultiDictProxy(CIMultiDict()), real_url=url)
    return ClientResponseError(request_info=info, history=(), status=status, message="gcs")


class FakeGcsStorage:
    """In-memory stand-in for ``gcloud.aio.storage.Storage``."""

    objects: dict[str, bytes] = {}
    fail_status: int | None = None
    # Report this MD5 for every upload instead of the real one (a server-side corruption).
    reported_md5: str | None = None

    def __init__(self, token: Any = None) -> None:
        pass

    async def __aenter__(self) -> "FakeGcsStorage":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _check(self, object_name: str) -> None:
        if self.fail_status is not None:
            raise gcs_error(self.fail_status)
        if object_name not in self.objects:
            raise gcs_error(404)

    async def download(self, bucket: str, object_name: str, **_kwargs: Any) -> bytes:
        self._check(object_name)
        return self.objects[object_name]

    async def download_metadata(self, bucket: str, object_name: str, **_kwargs: Any) -> dict[str, Any]:
        self._check(object_name)
        return {"updated": "2026-01-01T00:00:00+00:00"}

    async def upload(self, bucket: str, file_data: bytes, object_name: str,
                     parameters: dict[str, str] | None = None, **_kwargs: Any) -> dict[str, Any]:
        if self.fail_status is not None:
            raise gcs_error(self.fail_status)
        # ifGenerationMatch=0 succeeds only when no live object exists.
        if (parameters or {}).get("ifGenerationMatch") == "0" and object_name in self.objects:
            raise gcs_error(412)
        self.objects[object_name] = file_data
        md5 = base64.b64encode(hashlib.md5(file_data, usedforsecurity=False).digest()).decode()
        return {"name": object_name, "md5Hash": self.reported_md5 or md5}

    async def delete(self, bucket: str, object_name: str, **_kwargs: Any) -> str:
        self._check(object_name)
        del self.objects[object_name]
        return ""

    page_tokens: list[str | None] = []

    async def list_objects(self, bucket: str, params: dict[str, str] | None = None, **_kwargs: Any) -> dict[str, Any]:
        """One page of the JSON API listing; the page token is the offset."""
        params = params or {}
        self.page_tokens.append(params.get("pageToken"))
        names = sorted(name for name in self.objects if name.startswith(params.get("prefix", "")))
        start, size = int(params.get("pageToken") or 0), int(params.get("maxResults", 1000))
        page: dict[str, Any] = {"items": [
            {"name": name, "updated": "2026-01-01T00:00:00.000Z", "size": str(len(self.objects[name])), "etag": "e"}
            for name in names[start:start + size]
        ]}
        if start + size < len(names):
            page["nextPageToken"] = str(start + size)
        return page


@pytest.fixture
def fake_gcs(monkeypatch: pytest.MonkeyPatch) -> type[FakeGcsStorage]:
    FakeGcsStorage.objects = {}
    FakeGcsStorage.fail_status = None
    FakeGcsStorage.reported_md5 = None
    FakeGcsStorage.page_tokens = []
    monkeypatch.setattr(gcs_aio, "Storage", FakeGcsStorage)
    return FakeGcsStorage


@pytest.mark.asyncio
async def test_missing_object_contents_are_none(fake_gcs: type[FakeGcsStorage]) -> None:
    assert await gcs_aio.get_gcs_file_contents("absent.json", token=None) is None  # type: ignore[arg-type]
    fake_gcs.objects["present.json"] = b"{}"
    assert await gcs_aio.get_gcs_file_contents("present.json", token=None) == b"{}"  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_other_contents_errors_are_not_reported_as_missing(fake_gcs: type[FakeGcsStorage], status: int) -> None:
    fake_gcs.objects["present.json"] = b"{}"
    fake_gcs.fail_status = status
    with pytest.raises(ClientResponseError) as raised:
        await gcs_aio.get_gcs_file_contents("present.json", token=None)  # type: ignore[arg-type]
    assert raised.value.status == status


@pytest.mark.asyncio
async def test_missing_object_modified_date_is_file_not_found(fake_gcs: type[FakeGcsStorage]) -> None:
    with pytest.raises(FileNotFoundError):
        await gcs_aio.get_gcs_modified_date("absent.omex", token=None)  # type: ignore[arg-type]
    fake_gcs.fail_status = 503
    with pytest.raises(ClientResponseError):
        await gcs_aio.get_gcs_modified_date("absent.omex", token=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_412_means_the_object_exists(fake_gcs: type[FakeGcsStorage]) -> None:
    assert await gcs_aio.put_gcs_object_if_absent(b"first", "content/abc", token=None) is True  # type: ignore[arg-type]
    assert await gcs_aio.put_gcs_object_if_absent(b"second", "content/abc", token=None) is False  # type: ignore[arg-type]
    assert fake_gcs.objects["content/abc"] == b"first"  # never overwritten


@pytest.mark.asyncio
async def test_a_stored_checksum_mismatch_is_an_error(fake_gcs: type[FakeGcsStorage]) -> None:
    fake_gcs.reported_md5 = base64.b64encode(b"0" * 16).decode()
    with pytest.raises(IOError, match="does not match"):
        await gcs_aio.put_gcs_object_if_absent(b"bytes", "content/abc", token=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_other_create_errors_are_not_reported_as_existing(fake_gcs: type[FakeGcsStorage], status: int) -> None:
    fake_gcs.fail_status = status
    with pytest.raises(ClientResponseError) as raised:
        await gcs_aio.put_gcs_object_if_absent(b"bytes", "content/abc", token=None)  # type: ignore[arg-type]
    assert raised.value.status == status


@pytest.mark.asyncio
async def test_exists_and_an_idempotent_delete(fake_gcs: type[FakeGcsStorage]) -> None:
    assert await gcs_aio.gcs_object_exists("content/abc", token=None) is False  # type: ignore[arg-type]
    await gcs_aio.delete_gcs_object("content/abc", token=None)  # type: ignore[arg-type]  # missing: no error
    fake_gcs.objects["content/abc"] = b"x"
    assert await gcs_aio.gcs_object_exists("content/abc", token=None) is True  # type: ignore[arg-type]
    await gcs_aio.delete_gcs_object("content/abc", token=None)  # type: ignore[arg-type]
    assert "content/abc" not in fake_gcs.objects
    fake_gcs.objects["content/abc"] = b"x"
    fake_gcs.fail_status = 403
    with pytest.raises(ClientResponseError):
        await gcs_aio.delete_gcs_object("content/abc", token=None)  # type: ignore[arg-type]
    with pytest.raises(ClientResponseError):
        await gcs_aio.gcs_object_exists("content/abc", token=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_listing_follows_page_tokens(fake_gcs: type[FakeGcsStorage]) -> None:
    for index in range(5):
        fake_gcs.objects[f"verification-reports/r/{index}"] = b"xy"
    fake_gcs.objects["elsewhere/0"] = b"x"
    items = [item async for item in gcs_aio.iter_gcs_objects("verification-reports/", token=None,  # type: ignore[arg-type]
                                                               page_size=2)]
    assert [item.Key for item in items] == [f"verification-reports/r/{index}" for index in range(5)]
    assert fake_gcs.page_tokens == [None, "2", "4"]
    assert items[0].Size == 2 and items[0].LastModified.tzinfo is not None
