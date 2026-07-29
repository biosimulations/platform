from typing import AsyncGenerator

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from testcontainers.mongodb import MongoDbContainer  # type: ignore

from biosim_server.biosim_runs import DatabaseServiceMongo
from biosim_server.config import get_settings
from biosim_server.dependencies import set_database_service, get_database_service, set_omex_database_service, \
    get_omex_database_service, set_simulation_run_database_service, get_simulation_run_database_service, \
    set_project_database_service, get_project_database_service
from biosim_server.biosim_omex import OmexDatabaseServiceMongo
from biosim_server.simulations import SimulationRunDatabaseServiceMongo
from biosim_server.projects import ProjectDatabaseServiceMongo, ProjectSearchServiceMongo

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
    await db_service.ensure_indexes()
    old_db_service = get_database_service()
    set_database_service(db_service)

    yield db_service

    # Clean up so cached BiosimulatorWorkflowRun records don't leak across tests
    # (the container is session-scoped). Without this, a real-network test that
    # inserts a SUCCEEDED run gives later tests a spurious cache hit.
    await db_service.delete_all_biosimulator_workflow_runs()
    set_database_service(old_db_service)
    # await db_service.close()  the underlying client will already be closed

@pytest_asyncio.fixture(scope="function")
async def omex_database_service_mongo(mongo_test_client: AsyncIOMotorClient) -> AsyncGenerator[OmexDatabaseServiceMongo,None]:
    omex_db_service = OmexDatabaseServiceMongo(db_client=mongo_test_client)
    await omex_db_service.ensure_indexes()
    old_omex_db_service = get_omex_database_service()
    set_omex_database_service(omex_db_service)

    yield omex_db_service

    await omex_db_service.delete_all_omex_files()
    set_omex_database_service(old_omex_db_service)
    # await db_service.close()  the underlying client will already be closed

@pytest_asyncio.fixture(scope="function")
async def simulation_run_database_service_mongo(
    mongo_test_client: AsyncIOMotorClient,
) -> AsyncGenerator[SimulationRunDatabaseServiceMongo, None]:
    runs_db_service = SimulationRunDatabaseServiceMongo(db_client=mongo_test_client)
    await runs_db_service.ensure_indexes()
    old_runs_db_service = get_simulation_run_database_service()
    set_simulation_run_database_service(runs_db_service)

    yield runs_db_service

    await runs_db_service.delete_all_simulation_runs()
    set_simulation_run_database_service(old_runs_db_service)
    # await db_service.close()  the underlying client will already be closed


@pytest_asyncio.fixture(scope="function")
async def project_database_service_mongo(
    mongo_test_client: AsyncIOMotorClient,
) -> AsyncGenerator[
    tuple[ProjectDatabaseServiceMongo, AsyncIOMotorCollection, AsyncIOMotorCollection, AsyncIOMotorCollection],
    None,
]:
    """Project service plus the (Projects, Metadata, Specifications) collections.

    The service assembles from ``Projects`` joined to ``Metadata`` (display fields
    + facets) and ``Specifications`` (model_format) on ``simulationRun``; the test
    inserts fixture docs into those same collections, then drops them."""
    settings = get_settings()
    database = mongo_test_client.get_database(settings.mongodb_database)
    projects_col = database.get_collection(settings.mongodb_collection_projects)
    metadata_col = database.get_collection(settings.mongodb_collection_metadata)
    specifications_col = database.get_collection(settings.mongodb_collection_specifications)
    projects_db_service = ProjectDatabaseServiceMongo(db_client=mongo_test_client)
    old_projects_db_service = get_project_database_service()
    set_project_database_service(projects_db_service)

    yield projects_db_service, projects_col, metadata_col, specifications_col

    await projects_col.delete_many({})
    await metadata_col.delete_many({})
    await specifications_col.delete_many({})
    set_project_database_service(old_projects_db_service)


@pytest_asyncio.fixture(scope="function")
async def project_search_service_mongo(
    mongo_test_client: AsyncIOMotorClient,
) -> AsyncGenerator[
    tuple[
        ProjectSearchServiceMongo,
        AsyncIOMotorCollection,
        AsyncIOMotorCollection,
        AsyncIOMotorCollection,
        AsyncIOMotorCollection,
    ],
    None,
]:
    """Phase-1 search service plus the source (Projects, Metadata, Specifications)
    collections to seed and the materialized ``project_search`` collection.

    Seed the source collections, call ``rebuild_index()``, then query."""
    settings = get_settings()
    database = mongo_test_client.get_database(settings.mongodb_database)
    projects_col = database.get_collection(settings.mongodb_collection_projects)
    metadata_col = database.get_collection(settings.mongodb_collection_metadata)
    specifications_col = database.get_collection(settings.mongodb_collection_specifications)
    search_col = database.get_collection(settings.mongodb_collection_project_search)
    search_service = ProjectSearchServiceMongo(db_client=mongo_test_client)
    old_projects_db_service = get_project_database_service()
    set_project_database_service(search_service)

    yield search_service, projects_col, metadata_col, specifications_col, search_col

    await projects_col.delete_many({})
    await metadata_col.delete_many({})
    await specifications_col.delete_many({})
    await database.get_collection(settings.mongodb_collection_biosimulations_runs).delete_many({})
    await search_col.drop()
    set_project_database_service(old_projects_db_service)
