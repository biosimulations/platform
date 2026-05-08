import pytest

from biosim_server.biosim_omex import OmexFile, OmexDatabaseServiceMongo


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

