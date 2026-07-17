import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

KV_DRIVER = Literal['file', 's3', 'gcs']
TS_DRIVER = Literal['zarr', 'n5', 'zarr3']
STORAGE_BACKEND = Literal['gcs', 'local', 'minio']

load_dotenv()

ENV_CONFIG_ENV_FILE = "CONFIG_ENV_FILE"
ENV_SECRET_ENV_FILE = "SECRET_ENV_FILE"

if os.getenv(ENV_CONFIG_ENV_FILE) is not None and os.path.exists(str(os.getenv(ENV_CONFIG_ENV_FILE))):
    load_dotenv(os.getenv(ENV_CONFIG_ENV_FILE))

if os.getenv(ENV_SECRET_ENV_FILE) is not None and os.path.exists(str(os.getenv(ENV_SECRET_ENV_FILE))):
    load_dotenv(os.getenv(ENV_SECRET_ENV_FILE))


class Settings(BaseSettings):
    storage_backend: STORAGE_BACKEND = "gcs"
    storage_bucket: str = "files.biosimulations.dev"
    storage_endpoint_url: str = "https://storage.googleapis.com"
    storage_region: str = "us-east4"
    # S3-compatible credentials, used by FileServiceMinio.
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_tensorstore_driver: TS_DRIVER = "zarr3"
    storage_tensorstore_kvstore_driver: KV_DRIVER = "gcs"

    temporal_service_url: str = "localhost:7233"

    storage_local_cache_dir: str = "./local_cache"

    storage_gcs_credentials_file: str = ""

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "biosimulations"
    mongodb_collection_omex: str = "BiosimOmex"
    mongodb_collection_sims: str = "BiosimSims"
    mongodb_collection_compare: str = "BiosimCompare"
    mongodb_collection_simulation_runs: str = "BiosimSimulationRuns"
    mongodb_collection_projects: str = "Projects"
    mongodb_collection_metadata: str = "Metadata"
    mongodb_collection_specifications: str = "Specifications"
    # Platform-owned materialized search collection (Phase 1 $text). Built by
    # reading the biosimulations collections above; we own its $text index. The
    # "Platform" prefix keeps it clearly ours, not a biosimulations collection.
    mongodb_collection_project_search: str = "PlatformProjectSearch"
    # Legacy pre-2022 materialized summary; dead/abandoned (nothing writes it).
    # Kept only for reference — the API assembles from Projects + Metadata live.
    mongodb_collection_project_summary: str = "projectSummary"
    # TTL (seconds) for the platform-owned facet-stats cache.
    project_stats_cache_ttl_seconds: int = 300

    simdata_api_base_url: str = "https://simdata.api.biosimulations.org"
    biosimulators_api_base_url: str = "https://api.biosimulators.org"
    biosimulations_api_base_url: str = "https://api.biosimulations.org"

    slurm_submit_host: str = ""   # "hamantis.cam.uchc.edu"
    slurm_submit_user: str = ""   # "crbmapi"
    slurm_submit_key: str = ""    # "/Users/jimschaff/.ssh/crbmapi"
    # sbatch scheduling. Defaults are valid for the crbmapi user on hamantis;
    # override per deployment. sbatch templates read these instead of hardcoding.
    slurm_submit_partition: str = "vcell"
    slurm_submit_qos: str = "vcell-services"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_local_cache_dir() -> Path:
    settings = get_settings()
    local_cache_dir = Path(settings.storage_local_cache_dir)
    local_cache_dir.mkdir(parents=True, exist_ok=True)
    return local_cache_dir

