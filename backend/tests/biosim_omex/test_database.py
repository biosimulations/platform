from typing import Literal

import pytest

from biosim_server.biosim_omex import OmexFile, OmexDatabaseServiceMongo
from tests.biosim_omex.memory_db import MemoryOmexDb


@pytest.mark.asyncio
async def test_omex_file(omex_database_service_mongo: OmexDatabaseServiceMongo) -> None:
    omex_file = OmexFile(file_hash_md5="1234", bucket_name="test_bucket",
                         uploaded_filename="BIOMD0000000010_tellurium_Negative_feedback_and_ultrasen.omex",
                         omex_gcs_path="path/to/omex", file_size=100000)
    inserted_omex_file = await omex_database_service_mongo.insert_omex_file(omex_file=omex_file)
    database_omex_file = await omex_database_service_mongo.get_omex_file(file_hash_md5=omex_file.file_hash_md5)
    assert database_omex_file == inserted_omex_file

    assert database_omex_file.database_id is not None
    database_id = database_omex_file.database_id
    database_omex_file.database_id = None
    assert database_omex_file == omex_file

    await omex_database_service_mongo.delete_omex_file(database_id=database_id)

    assert await omex_database_service_mongo.get_omex_file(file_hash_md5=omex_file.file_hash_md5) is None

    with pytest.raises(Exception):
        await omex_database_service_mongo.delete_omex_file(database_id=database_id)


@pytest.mark.parametrize("owner", [None, "auth0|owner"])
def test_omex_owner_is_optional_and_legacy_documents_validate(owner: str | None) -> None:
    """Both anonymous/new-null and authenticated owners are valid."""
    record = OmexFile.model_validate(
        {
            "file_hash_md5": "1234",
            "bucket_name": "test_bucket",
            "uploaded_filename": "model.omex",
            "omex_gcs_path": "path/to/omex",
            "file_size": 1,
            "owner": owner,
        }
    )
    assert record.owner == owner

    legacy = OmexFile.model_validate(
        {
            "file_hash_md5": "5678",
            "bucket_name": "test_bucket",
            "uploaded_filename": "legacy.omex",
            "omex_gcs_path": "path/to/legacy",
            "file_size": 1,
        }
    )
    assert legacy.owner is None
    assert legacy.visibility is None


@pytest.mark.parametrize("visibility", [None, "public", "private"])
def test_omex_visibility_is_optional_and_legacy_documents_validate(
    visibility: str | None,
) -> None:
    record = OmexFile.model_validate(
        {
            "file_hash_md5": "abcd",
            "bucket_name": "test_bucket",
            "uploaded_filename": "model.omex",
            "omex_gcs_path": "path/to/omex",
            "file_size": 1,
            "visibility": visibility,
        }
    )
    assert record.visibility == visibility


def _omex(
    *,
    owner: str | None = None,
    visibility: Literal["public", "private"] | None = None,
    suffix: str = "",
) -> OmexFile:
    return OmexFile(
        file_hash_md5="samehash",
        bucket_name="test_bucket",
        uploaded_filename=f"model{suffix}.omex",
        omex_gcs_path="path/to/omex",
        file_size=1,
        owner=owner,
        visibility=visibility,
    )


@pytest.mark.asyncio
async def test_omex_lookup_prefers_caller_private_then_public() -> None:
    db = MemoryOmexDb()
    public = await db.insert_omex_file(_omex(owner=None, visibility="public", suffix="-pub"))
    alice = await db.insert_omex_file(
        _omex(owner="auth0|alice", visibility="private", suffix="-alice")
    )
    bob = await db.insert_omex_file(_omex(owner="auth0|bob", visibility="private", suffix="-bob"))

    assert await db.get_omex_file_for_caller("samehash", owner="auth0|alice") == alice
    assert await db.get_omex_file_for_caller("samehash", owner="auth0|bob") == bob
    assert await db.get_omex_file_for_caller("samehash", owner=None) == public
    # A third caller must not receive Alice or Bob's private row.
    charlie = await db.get_omex_file_for_caller("samehash", owner="auth0|charlie")
    assert charlie == public


@pytest.mark.asyncio
async def test_omex_lookup_never_returns_another_users_private_row() -> None:
    db = MemoryOmexDb()
    await db.insert_omex_file(_omex(owner="auth0|alice", visibility="private"))

    assert await db.get_omex_file_for_caller("samehash", owner="auth0|bob") is None
    assert await db.get_omex_file_for_caller("samehash", owner=None) is None
    assert await db.get_omex_file_by_hash_and_owner("samehash", None) is None


@pytest.mark.asyncio
async def test_omex_owned_public_row_is_publicly_eligible() -> None:
    """Public eligibility is decided by visibility alone: an explicit-public
    row stays publicly readable even though an owner is populated."""
    db = MemoryOmexDb()
    owned_public = await db.insert_omex_file(_omex(owner="auth0|alice", visibility="public"))

    assert await db.get_omex_file_for_caller("samehash", owner=None) == owned_public
    assert await db.get_omex_file_for_caller("samehash", owner="auth0|bob") == owned_public
    assert await db.get_omex_file_by_hash_and_owner("samehash", None) == owned_public


@pytest.mark.asyncio
async def test_omex_owned_null_visibility_row_is_publicly_eligible() -> None:
    """Legacy compatibility: missing/null visibility is public regardless of
    whether an owner is populated."""
    db = MemoryOmexDb()
    legacy_owned = await db.insert_omex_file(_omex(owner="auth0|alice", visibility=None))

    assert await db.get_omex_file_for_caller("samehash", owner=None) == legacy_owned
    assert await db.get_omex_file_for_caller("samehash", owner="auth0|bob") == legacy_owned


@pytest.mark.asyncio
async def test_omex_caller_private_row_precedes_shared_public_row() -> None:
    """When the same hash has both a caller-private version and a shared
    public version, the caller receives their own private row; everyone else
    receives the public one."""
    db = MemoryOmexDb()
    shared_public = await db.insert_omex_file(
        _omex(owner="auth0|bob", visibility="public", suffix="-shared")
    )
    alice_private = await db.insert_omex_file(
        _omex(owner="auth0|alice", visibility="private", suffix="-alice")
    )

    assert await db.get_omex_file_for_caller("samehash", owner="auth0|alice") == alice_private
    assert await db.get_omex_file_for_caller("samehash", owner=None) == shared_public
    assert await db.get_omex_file_for_caller("samehash", owner="auth0|charlie") == shared_public


def _omex_with_hash(
    file_hash_md5: str,
    *,
    owner: str | None = None,
    visibility: Literal["public", "private"] | None = None,
) -> OmexFile:
    return OmexFile(
        file_hash_md5=file_hash_md5,
        bucket_name="test_bucket",
        uploaded_filename="model.omex",
        omex_gcs_path="path/to/omex",
        file_size=1,
        owner=owner,
        visibility=visibility,
    )


@pytest.mark.asyncio
async def test_mongo_public_fallback_matches_memory_semantics(
    omex_database_service_mongo: OmexDatabaseServiceMongo,
) -> None:
    """Production Mongo lookup parity for the in-memory double: owned rows
    with public or null/missing visibility are publicly eligible; private
    rows are owner-only; the caller's own private row takes precedence."""
    db = omex_database_service_mongo
    await db.delete_all_omex_files()

    owned_public = await db.insert_omex_file(
        _omex_with_hash("h-owned-public", owner="auth0|alice", visibility="public")
    )
    owned_legacy = await db.insert_omex_file(
        _omex_with_hash("h-owned-legacy", owner="auth0|alice", visibility=None)
    )
    await db.insert_omex_file(
        _omex_with_hash("h-owned-private", owner="auth0|alice", visibility="private")
    )
    shared_public = await db.insert_omex_file(
        _omex_with_hash("h-precedence", owner="auth0|bob", visibility="public")
    )
    alice_private = await db.insert_omex_file(
        _omex_with_hash("h-precedence", owner="auth0|alice", visibility="private")
    )

    assert await db.get_omex_file_for_caller("h-owned-public", owner=None) == owned_public
    assert await db.get_omex_file_for_caller("h-owned-public", owner="auth0|bob") == owned_public
    assert await db.get_omex_file_for_caller("h-owned-legacy", owner=None) == owned_legacy
    assert await db.get_omex_file_for_caller("h-owned-private", owner=None) is None
    assert await db.get_omex_file_for_caller("h-owned-private", owner="auth0|bob") is None
    owned = await db.get_omex_file_for_caller("h-owned-private", owner="auth0|alice")
    assert owned is not None and owned.visibility == "private"
    assert await db.get_omex_file_for_caller("h-precedence", owner="auth0|alice") == alice_private
    assert await db.get_omex_file_for_caller("h-precedence", owner=None) == shared_public

    await db.delete_all_omex_files()
