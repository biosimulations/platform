from typing import TYPE_CHECKING

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from temporalio.client import Client as TemporalClient

from biosim_server.biosim_omex.database import OmexDatabaseService, OmexDatabaseServiceMongo
from biosim_server.biosim_runs.biosim_service import BiosimService, BiosimServiceRest
from biosim_server.biosim_runs.database import DatabaseService, DatabaseServiceMongo
from biosim_server.common.storage import FileService, FileServiceGCS, FileServiceLocal, FileServiceMinio
from biosim_server.config import get_local_cache_dir, get_settings

if TYPE_CHECKING:
    # Imported lazily at runtime (see init_standalone) to avoid an import cycle:
    # simulations/__init__.py -> router -> dependencies.
    from biosim_server.simulations.database import SimulationRunDatabaseService
    from biosim_server.projects.database import ProjectDatabaseService

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

#------- simulation run database service (standalone or pytest) ------

global_simulation_run_database_service: "SimulationRunDatabaseService | None" = None

def set_simulation_run_database_service(service: "SimulationRunDatabaseService | None") -> None:
    global global_simulation_run_database_service
    global_simulation_run_database_service = service

def get_simulation_run_database_service() -> "SimulationRunDatabaseService | None":
    global global_simulation_run_database_service
    return global_simulation_run_database_service

#------- project database service (standalone or pytest) ------

global_project_database_service: "ProjectDatabaseService | None" = None

def set_project_database_service(service: "ProjectDatabaseService | None") -> None:
    global global_project_database_service
    global_project_database_service = service

def get_project_database_service() -> "ProjectDatabaseService | None":
    global global_project_database_service
    return global_project_database_service

#------- biosim service (standalone or pytest) ------

global_biosim_service: BiosimService | None = None

def set_biosim_service(biosim_service: BiosimService | None) -> None:
    global global_biosim_service
    global_biosim_service = biosim_service

def get_biosim_service() -> BiosimService | None:
    global global_biosim_service
    return global_biosim_service

#------ raw Mongo client (standalone or pytest) ------
# Exposed globally (distinct from the per-domain *DatabaseService wrappers)
# so GET /ready can ping Mongo directly without adding a ping() method to
# every domain's ABC.

global_mongo_client: AsyncIOMotorClient | None = None

def set_mongo_client(mongo_client: AsyncIOMotorClient | None) -> None:
    global global_mongo_client
    global_mongo_client = mongo_client

def get_mongo_client() -> AsyncIOMotorClient | None:
    global global_mongo_client
    return global_mongo_client

#------ shared HTTP client for upstream biosimulations.org calls ------
# One pooled AsyncClient for the whole process, injected as a FastAPI dependency
# so tests can swap in an httpx.MockTransport client via dependency_overrides.
# Lazily constructed: the API creates it in init_standalone, but a test client
# that skips lifespan still gets a usable one.

_HTTP_TIMEOUT = httpx.Timeout(30.0)

global_http_client: httpx.AsyncClient | None = None

def set_http_client(http_client: httpx.AsyncClient | None) -> None:
    global global_http_client
    global_http_client = http_client

def get_http_client() -> httpx.AsyncClient:
    global global_http_client
    if global_http_client is None:
        global_http_client = httpx.AsyncClient(
            base_url=get_settings().biosimulations_api_base_url.rstrip("/"),
            timeout=_HTTP_TIMEOUT,
        )
    return global_http_client

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
        return FileServiceMinio()
    raise ValueError(f"Unknown STORAGE_BACKEND={backend!r}")


async def init_standalone() -> None:
    settings = get_settings()
    set_file_service(_make_file_service(settings.storage_backend))
    set_biosim_service(BiosimServiceRest())
    set_http_client(
        httpx.AsyncClient(
            base_url=settings.biosimulations_api_base_url.rstrip("/"),
            timeout=_HTTP_TIMEOUT,
        )
    )
    set_temporal_client(await TemporalClient.connect(settings.temporal_service_url))

    # Local import avoids the simulations -> dependencies import cycle at module load.
    from biosim_server.simulations.database import SimulationRunDatabaseServiceMongo
    from biosim_server.projects.search import ProjectSearchServiceMongo

    motor_client = AsyncIOMotorClient(get_settings().mongodb_uri)
    set_mongo_client(motor_client)
    db_service = DatabaseServiceMongo(db_client=motor_client)
    omex_db_service = OmexDatabaseServiceMongo(db_client=motor_client)
    runs_db_service = SimulationRunDatabaseServiceMongo(db_client=motor_client)
    # Phase 1 search: queries a platform-owned project_search collection with our
    # own $text index (see projects/search.py).
    projects_db_service = ProjectSearchServiceMongo(db_client=motor_client)

    # create_index is idempotent; calling on every start keeps schema in sync as
    # we add lookups. Each service knows which fields its queries hit.
    await db_service.ensure_indexes()
    await omex_db_service.ensure_indexes()
    await runs_db_service.ensure_indexes()
    await projects_db_service.ensure_indexes()
    # Populate the search index on first run (no-op once built); refresh on demand
    # via POST /projects/reindex.
    await projects_db_service.rebuild_index_if_empty()

    set_database_service(db_service)
    set_omex_database_service(omex_db_service)
    set_simulation_run_database_service(runs_db_service)
    set_project_database_service(projects_db_service)

async def shutdown_standalone() -> None:
    db_service = get_database_service()
    if db_service:
        await db_service.close()
    file_service = get_file_service()
    if file_service:
        await file_service.close()
    if global_http_client is not None:
        await global_http_client.aclose()
        set_http_client(None)
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
    # Shares the motor client closed via db_service above; just clear the handles.
    set_simulation_run_database_service(None)
    set_project_database_service(None)
    set_mongo_client(None)
