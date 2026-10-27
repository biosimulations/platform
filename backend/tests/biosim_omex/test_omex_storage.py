"""OMEX cache identity: per-caller policy rows sharing hash-keyed GCS bytes."""

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from biosim_server.biosim_omex import get_cached_omex_file_from_raw, get_cached_omex_file_from_upload
from biosim_server.biosim_omex.omex_storage import read_upload_capped
from tests.biosim_omex.memory_db import MemoryOmexDb


@pytest.mark.asyncio
async def test_anonymous_then_auth_same_bytes_creates_two_rows() -> None:
    db = MemoryOmexDb()
    file_service = AsyncMock()
    file_service.upload_bytes = AsyncMock(return_value="verify/omex/deadbeef.omex")
    contents = b"identical-omex-bytes"

    public = await get_cached_omex_file_from_raw(
        file_service, db, contents, "a.omex", owner=None
    )
    private = await get_cached_omex_file_from_raw(
        file_service, db, contents, "a.omex", owner="auth0|alice"
    )

    assert public.owner is None
    assert public.visibility == "public"
    assert private.owner == "auth0|alice"
    assert private.visibility == "private"
    assert private.omex_gcs_path == public.omex_gcs_path
    file_service.upload_bytes.assert_awaited_once()
    listed = await db.list_omex_files()
    assert len(listed) == 2


@pytest.mark.asyncio
async def test_two_authenticated_callers_get_separate_private_rows() -> None:
    db = MemoryOmexDb()
    file_service = AsyncMock()
    file_service.upload_bytes = AsyncMock(return_value="verify/omex/deadbeef.omex")
    contents = b"shared-bytes"

    alice = await get_cached_omex_file_from_raw(
        file_service, db, contents, "a.omex", owner="auth0|alice"
    )
    bob = await get_cached_omex_file_from_raw(
        file_service, db, contents, "a.omex", owner="auth0|bob"
    )

    assert alice.owner == "auth0|alice"
    assert bob.owner == "auth0|bob"
    assert alice.visibility == bob.visibility == "private"
    assert alice.omex_gcs_path == bob.omex_gcs_path
    file_service.upload_bytes.assert_awaited_once()
    listed = await db.list_omex_files()
    assert {row.owner for row in listed} == {"auth0|alice", "auth0|bob"}


@pytest.mark.asyncio
async def test_auth_then_anonymous_creates_private_and_public_rows() -> None:
    db = MemoryOmexDb()
    file_service = AsyncMock()
    file_service.upload_bytes = AsyncMock(return_value="verify/omex/deadbeef.omex")
    contents = b"shared-bytes"

    private = await get_cached_omex_file_from_raw(
        file_service, db, contents, "a.omex", owner="auth0|alice"
    )
    public = await get_cached_omex_file_from_raw(
        file_service, db, contents, "a.omex", owner=None
    )

    assert private.visibility == "private"
    assert public.visibility == "public"
    assert public.owner is None
    assert public.omex_gcs_path == private.omex_gcs_path
    file_service.upload_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_row_is_not_overwritten_on_hash_hit() -> None:
    db = MemoryOmexDb()
    file_service = AsyncMock()
    file_service.upload_bytes = AsyncMock(return_value="verify/omex/deadbeef.omex")
    contents = b"shared-bytes"

    first = await get_cached_omex_file_from_raw(
        file_service, db, contents, "a.omex", owner="auth0|alice"
    )
    second = await get_cached_omex_file_from_raw(
        file_service, db, contents, "renamed.omex", owner="auth0|alice"
    )

    assert first.database_id == second.database_id
    assert second.uploaded_filename == first.uploaded_filename
    listed = await db.list_omex_files()
    assert len(listed) == 1

# --- audit P1 item 7: uploads are size-capped on every ingestion path --- #


def _upload(payload: bytes) -> UploadFile:
    """A real starlette UploadFile over an in-memory body."""
    return UploadFile(file=BytesIO(payload), filename="model.omex")


@pytest.mark.asyncio
async def test_read_upload_capped_returns_body_under_the_limit() -> None:
    contents = await read_upload_capped(_upload(b"x" * 4096), max_bytes=8192)
    assert contents == b"x" * 4096


@pytest.mark.asyncio
async def test_read_upload_capped_rejects_oversized_body() -> None:
    """A body past the cap raises 413 instead of being fully buffered."""
    with pytest.raises(HTTPException) as excinfo:
        await read_upload_capped(_upload(b"x" * 8192), max_bytes=4096)
    assert excinfo.value.status_code == 413
    assert "limit" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_get_cached_omex_file_from_upload_is_capped() -> None:
    """The /verify/omex ingestion path rejects an oversized upload with 413."""
    db = MemoryOmexDb()
    file_service = AsyncMock()
    with patch("biosim_server.biosim_omex.omex_storage.MAX_OMEX_BYTES", 4096):
        with pytest.raises(HTTPException) as excinfo:
            await get_cached_omex_file_from_upload(
                file_service, db, _upload(b"x" * (64 * 1024)), owner="auth0|alice"
            )
    assert excinfo.value.status_code == 413
    file_service.upload_bytes.assert_not_awaited()
