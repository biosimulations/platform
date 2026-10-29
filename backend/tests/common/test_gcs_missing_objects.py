"""GCS helpers honor the FileService missing-object contract without credentials.

gcloud-aio-storage reports a missing object as ``aiohttp.ClientResponseError``
with status 404, never ``FileNotFoundError``. These tests replace ``Storage``
with a fake that raises exactly that, so the contract is checked offline.
"""

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

    async def upload(self, bucket: str, file_data: bytes, object_name: str, **_kwargs: Any) -> dict[str, Any]:
        self.objects[object_name] = file_data
        return {}


@pytest.fixture
def fake_gcs(monkeypatch: pytest.MonkeyPatch) -> type[FakeGcsStorage]:
    FakeGcsStorage.objects = {}
    FakeGcsStorage.fail_status = None
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
