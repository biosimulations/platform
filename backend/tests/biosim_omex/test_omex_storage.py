"""OMEX ownership, visibility, and cache-reuse invariants.

The security property under test: an OMEX archive's MD5 is a *blob* key, not an
access capability. Two users who upload byte-identical archives must end up with
two independent resources over one shared blob -- neither able to reach, reuse,
or overwrite the other's.
"""

import asyncio

import pytest

from biosim_server.biosim_omex import OmexDatabaseServiceMongo, OmexFile, get_cached_omex_file_from_raw
from biosim_server.common.storage.file_service_local import FileServiceLocal

pytestmark = pytest.mark.integration_local

ARCHIVE = b"PK\x03\x04 pretend this is an omex archive"
OTHER_ARCHIVE = b"PK\x03\x04 a completely different archive"

ALICE = "auth0|alice"
BOB = "auth0|bob"


async def _ingest(
    file_service: FileServiceLocal,
    omex_db: OmexDatabaseServiceMongo,
    contents: bytes = ARCHIVE,
    filename: str = "model.omex",
    owner_sub: str | None = None,
) -> OmexFile:
    return await get_cached_omex_file_from_raw(
        file_service, omex_db, contents, filename, owner_sub=owner_sub
    )


@pytest.mark.asyncio
async def test_anonymous_ingest_is_public_and_ownerless(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    omex = await _ingest(file_service_local, omex_database_service_mongo)
    assert omex.owner_sub is None
    assert omex.visibility == "public"
    assert omex.is_public is True


@pytest.mark.asyncio
async def test_authenticated_ingest_is_private_and_owned(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    omex = await _ingest(file_service_local, omex_database_service_mongo, owner_sub=ALICE)
    assert omex.owner_sub == ALICE
    assert omex.visibility == "private"
    assert omex.is_public is False


@pytest.mark.asyncio
async def test_identical_bytes_do_not_transfer_ownership_between_users(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    """Same archive, two owners -> two resources, one blob. No ACL is inherited."""
    alice = await _ingest(file_service_local, omex_database_service_mongo, owner_sub=ALICE)
    bob = await _ingest(file_service_local, omex_database_service_mongo, owner_sub=BOB)

    assert alice.database_id != bob.database_id
    assert alice.owner_sub == ALICE and bob.owner_sub == BOB
    assert bob.visibility == "private"
    # Deduplicated storage -- and only storage.
    assert alice.file_hash_md5 == bob.file_hash_md5
    assert alice.omex_gcs_path == bob.omex_gcs_path

    # Alice's resource is untouched by Bob's upload.
    alice_again = await omex_database_service_mongo.get_omex_file(
        file_hash_md5=alice.file_hash_md5, owner_sub=ALICE
    )
    assert alice_again is not None
    assert alice_again.owner_sub == ALICE
    assert alice_again.visibility == "private"


@pytest.mark.asyncio
async def test_anonymous_ingest_of_private_bytes_does_not_expose_the_private_resource(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    alice = await _ingest(file_service_local, omex_database_service_mongo, owner_sub=ALICE)
    anon = await _ingest(file_service_local, omex_database_service_mongo)

    assert anon.owner_sub is None and anon.visibility == "public"
    assert anon.database_id != alice.database_id

    reread = await omex_database_service_mongo.get_omex_file(
        file_hash_md5=alice.file_hash_md5, owner_sub=ALICE
    )
    assert reread is not None and reread.visibility == "private"


@pytest.mark.asyncio
async def test_cache_hit_reuses_own_resource_without_rewriting_it(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    first = await _ingest(file_service_local, omex_database_service_mongo, filename="a.omex", owner_sub=ALICE)
    second = await _ingest(file_service_local, omex_database_service_mongo, filename="b.omex", owner_sub=ALICE)

    assert first.database_id == second.database_id
    # A cache hit returns the stored resource; it does not rewrite its metadata.
    assert second.uploaded_filename == "a.omex"
    assert second.visibility == "private"


@pytest.mark.asyncio
async def test_private_hash_is_not_an_idor(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    """Knowing Alice's hash must not resolve to Alice's private resource."""
    alice = await _ingest(file_service_local, omex_database_service_mongo, owner_sub=ALICE)

    assert await omex_database_service_mongo.find_accessible_omex_file(
        file_hash_md5=alice.file_hash_md5, viewer_sub=BOB
    ) is None
    assert await omex_database_service_mongo.find_accessible_omex_file(
        file_hash_md5=alice.file_hash_md5, viewer_sub=None
    ) is None
    own = await omex_database_service_mongo.find_accessible_omex_file(
        file_hash_md5=alice.file_hash_md5, viewer_sub=ALICE
    )
    assert own is not None and own.owner_sub == ALICE


@pytest.mark.asyncio
async def test_public_resource_is_accessible_to_everyone(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    public = await _ingest(file_service_local, omex_database_service_mongo)
    for viewer in (None, ALICE, BOB):
        found = await omex_database_service_mongo.find_accessible_omex_file(
            file_hash_md5=public.file_hash_md5, viewer_sub=viewer
        )
        assert found is not None and found.is_public


@pytest.mark.asyncio
async def test_own_private_resource_wins_over_a_public_one_for_the_same_blob(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    await _ingest(file_service_local, omex_database_service_mongo)  # public
    alice = await _ingest(file_service_local, omex_database_service_mongo, owner_sub=ALICE)

    resolved = await omex_database_service_mongo.find_accessible_omex_file(
        file_hash_md5=alice.file_hash_md5, viewer_sub=ALICE
    )
    assert resolved is not None and resolved.owner_sub == ALICE


@pytest.mark.asyncio
async def test_legacy_row_without_visibility_is_public(
    omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    """Rows written before ownership existed keep working for everyone."""
    await omex_database_service_mongo._omex_file_col.insert_one({
        "file_hash_md5": "legacyhash",
        "uploaded_filename": "legacy.omex",
        "bucket_name": "test_bucket",
        "omex_gcs_path": "verify/omex/legacyhash.omex",
        "file_size": 10,
        # no owner_sub, no visibility
    })
    found = await omex_database_service_mongo.find_accessible_omex_file(
        file_hash_md5="legacyhash", viewer_sub=None
    )
    assert found is not None
    assert found.owner_sub is None
    assert found.visibility is None
    assert found.is_public is True


@pytest.mark.asyncio
async def test_concurrent_ingest_does_not_clobber_ownership(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    """Alice and Bob racing on the same bytes: one blob, two resources, no overwrite."""
    results = await asyncio.gather(*(
        _ingest(file_service_local, omex_database_service_mongo, owner_sub=owner)
        for owner in (ALICE, BOB, ALICE, BOB, None)
    ))
    assert {r.owner_sub for r in results} == {ALICE, BOB, None}

    stored = [
        f for f in await omex_database_service_mongo.list_omex_files()
        if f.file_hash_md5 == results[0].file_hash_md5
    ]
    assert len(stored) == 3
    by_owner = {f.owner_sub: f for f in stored}
    assert by_owner[ALICE].visibility == "private"
    assert by_owner[BOB].visibility == "private"
    assert by_owner[None].visibility == "public"


@pytest.mark.asyncio
async def test_set_omex_visibility_is_scoped_to_one_owner(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    alice = await _ingest(file_service_local, omex_database_service_mongo, owner_sub=ALICE)
    await _ingest(file_service_local, omex_database_service_mongo, owner_sub=BOB)

    assert await omex_database_service_mongo.set_omex_visibility(
        file_hash_md5=alice.file_hash_md5, owner_sub=ALICE, visibility="public"
    ) is True

    alice_row = await omex_database_service_mongo.get_omex_file(
        file_hash_md5=alice.file_hash_md5, owner_sub=ALICE
    )
    bob_row = await omex_database_service_mongo.get_omex_file(
        file_hash_md5=alice.file_hash_md5, owner_sub=BOB
    )
    assert alice_row is not None and alice_row.visibility == "public"
    assert bob_row is not None and bob_row.visibility == "private"


@pytest.mark.asyncio
async def test_different_bytes_are_different_blobs(
    file_service_local: FileServiceLocal, omex_database_service_mongo: OmexDatabaseServiceMongo
) -> None:
    a = await _ingest(file_service_local, omex_database_service_mongo, contents=ARCHIVE)
    b = await _ingest(file_service_local, omex_database_service_mongo, contents=OTHER_ARCHIVE)
    assert a.file_hash_md5 != b.file_hash_md5
