from motor.motor_asyncio import AsyncIOMotorClient
from temporalio.client import Client as TemporalClient

from biosim_server.biosim_omex.database import OmexDatabaseService, OmexDatabaseServiceMongo
from biosim_server.biosim_runs.biosim_service import BiosimService, BiosimServiceRest
from biosim_server.biosim_runs.database import DatabaseService, DatabaseServiceMongo
from biosim_server.common.storage import FileService, FileServiceGCS, FileServiceLocal
from biosim_server.config import get_local_cache_dir, get_settings

#------ file service (standalone or pytest) ------

global_file_service: FileService | None = None

def set_file_service(file_service: FileService | None) -> None:
    global global_file_service
    global_file_service = file_service

def get_file_service() -> FileService | None:
    global global_file_service
    return global_file_service

#------- database service (standalone or pytest) ------

global_database_service: DatabaseService | None = None

def set_database_service(database_service: DatabaseService | None) -> None:
    global global_database_service
    global_database_service = database_service

def get_database_service() -> DatabaseService | None:
    global global_database_service
    return global_database_service

#------- database service (standalone or pytest) ------

global_omex_database_service: OmexDatabaseService | None = None

def set_omex_database_service(omex_database_service: OmexDatabaseService | None) -> None:
    global global_omex_database_service
    global_omex_database_service = omex_database_service

def get_omex_database_service() -> OmexDatabaseService | None:
    global global_omex_database_service
    return global_omex_database_service

#------- biosim service (standalone or pytest) ------

global_biosim_service: BiosimService | None = None

def set_biosim_service(biosim_service: BiosimService | None) -> None:
    global global_biosim_service
    global_biosim_service = biosim_service

def get_biosim_service() -> BiosimService | None:
    global global_biosim_service
    return global_biosim_service

#------ Temporal workflow client ------

global_temporal_client: TemporalClient | None = None

def set_temporal_client(temporal_client: TemporalClient | None) -> None:
    global global_temporal_client
    global_temporal_client = temporal_client

def get_temporal_client() -> TemporalClient | None:
    global global_temporal_client
    return global_temporal_client

#------ initialized standalone application (standalone) ------

def _make_file_service(backend: str) -> FileService:
    if backend == "gcs":
        return FileServiceGCS()
    if backend == "local":
        return FileServiceLocal(base_dir=get_local_cache_dir() / "local_data" / "store")
    if backend == "minio":
        raise NotImplementedError(
            "STORAGE_BACKEND=minio is not implemented yet (planned: file_service_minio.py)"
        )
    raise ValueError(f"Unknown STORAGE_BACKEND={backend!r}")


async def init_standalone() -> None:
    settings = get_settings()
    set_file_service(_make_file_service(settings.storage_backend))
    set_biosim_service(BiosimServiceRest())
    set_temporal_client(await TemporalClient.connect(settings.temporal_service_url))

    motor_client = AsyncIOMotorClient(get_settings().mongodb_uri)
    set_database_service(DatabaseServiceMongo(db_client=motor_client))
    set_omex_database_service(OmexDatabaseServiceMongo(db_client=motor_client))

async def shutdown_standalone() -> None:
    db_service = get_database_service()
    if db_service:
        await db_service.close()
    file_service = get_file_service()
    if file_service:
        await file_service.close()
    # biosim_service = get_biosim_service()
    # if biosim_service:
    #     await biosim_service.close()
    # temporal_client = get_temporal_client()
    # if temporal_client:
    #     await temporal_client.close()
    set_file_service(None)
    set_biosim_service(None)
    set_temporal_client(None)
    set_database_service(None)