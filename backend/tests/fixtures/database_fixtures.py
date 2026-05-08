from typing import AsyncGenerator

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from testcontainers.mongodb import MongoDbContainer  # type: ignore

from biosim_server.biosim_runs import DatabaseServiceMongo
from biosim_server.dependencies import set_database_service, get_database_service, set_omex_database_service, \
    get_omex_database_service
from biosim_server.biosim_omex import OmexDatabaseServiceMongo

MONGODB_DATABASE_NAME = "mydatabase"
MONGODB_COLLECTION_NAME = "mycollection"

@pytest.fixture(scope="session")
def mongodb_container() -> MongoDbContainer:
    with MongoDbContainer() as container:
        container.start()
        yield container

@pytest_asyncio.fixture(scope="function")
async def mongo_test_client(mongodb_container: MongoDbContainer) -> AsyncGenerator[AsyncIOMotorClient,None]:
    mongo_test_client = AsyncIOMotorClient(mongodb_container.get_connection_url())
    yield mongo_test_client
    mongo_test_client.close()

@pytest_asyncio.fixture(scope="function")
async def mongo_test_database(mongo_test_client: AsyncIOMotorClient) -> AsyncIOMotorDatabase:
    test_database: AsyncIOMotorDatabase = mongo_test_client.get_database(name=MONGODB_DATABASE_NAME)
    return test_database

@pytest_asyncio.fixture(scope="function")
async def mongo_test_collection(mongo_test_database: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:
    test_collection: AsyncIOMotorCollection = mongo_test_database.get_collection(name=MONGODB_COLLECTION_NAME)
    return test_collection

@pytest_asyncio.fixture(scope="function")
async def database_service_mongo(mongo_test_client: AsyncIOMotorClient) -> AsyncGenerator[DatabaseServiceMongo,None]:
    db_service = DatabaseServiceMongo(db_client=mongo_test_client)
    old_db_service = get_database_service()
    set_database_service(db_service)

    yield db_service

    set_database_service(old_db_service)
    # await db_service.close()  the underlying client will already be closed

@pytest_asyncio.fixture(scope="function")
async def omex_database_service_mongo(mongo_test_client: AsyncIOMotorClient) -> AsyncGenerator[OmexDatabaseServiceMongo,None]:
    omex_db_service = OmexDatabaseServiceMongo(db_client=mongo_test_client)
    old_omex_db_service = get_omex_database_service()
    set_omex_database_service(omex_db_service)

    yield omex_db_service

    await omex_db_service.delete_all_omex_files()
    set_omex_database_service(old_omex_db_service)
    # await db_service.close()  the underlying client will already be closed
