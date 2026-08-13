import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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

class Auth0Settings(BaseSettings):
    domain: str = Field(default="", alias="AUTH0_DOMAIN")
    audience: str = Field(default="", alias="AUTH0_AUDIENCE")
    # Startup gate: when true, the API refuses to start with an incomplete or
    # malformed configuration (see _validate_auth0_configuration in api/main.py).
    # Set false only to run deliberately without an identity provider.
    required: bool = Field(default=True, alias="AUTH_REQUIRED")
    algorithms: list[str] = ["RS256"]
    # get_current_user derives the expected JWT issuer and JWKS URL from
    # `domain` using Auth0's own convention ("https://{domain}/" and
    # "https://{domain}/.well-known/jwks.json") when these are left blank.
    # Set them explicitly to verify tokens from a different OIDC provider
    # (e.g. a Keycloak realm in tests) whose issuer/JWKS URLs don't follow
    # that shape -- Keycloak's issuer is "{base_url}/realms/{realm}" and its
    # JWKS lives at "{issuer}/protocol/openid-connect/certs".
    issuer: str = Field(default="", alias="AUTH0_ISSUER")
    jwks_uri: str = Field(default="", alias="AUTH0_JWKS_URI")
    # M2M application credentials for the Auth0 Management API (used by
    # PATCH/DELETE /api/v1/me). Optional -- those endpoints 503 when unset.
    management_client_id: str = Field(default="", alias="AUTH0_MANAGEMENT_CLIENT_ID")
    management_client_secret: str = Field(default="", alias="AUTH0_MANAGEMENT_CLIENT_SECRET")
    # Auth0 access tokens don't include role assignments by default -- roles
    # have to be copied onto the token as a custom claim by an Auth0 Action
    # (Auth0 Dashboard -> Actions -> Flows -> Login -> add a post-login
    # action that sets `event.authorization.roles` into this claim on
    # api.accessToken). Must be a fully-qualified, non-reserved URI per
    # Auth0's namespaced-claim rules. Defaults to this API's own audience so
    # it works out of the box for this tenant; override if the Action uses a
    # different namespace.
    roles_claim: str = Field(default="https://api.biosimulations.org/roles", alias="AUTH0_ROLES_CLAIM")
    # Auth0 access tokens don't include `email` by default either -- unlike
    # `roles_claim`, this is a hard Auth0 platform behavior, not a missing
    # role assignment: `email` only lives on the ID token / `/userinfo` unless
    # a Post-Login Action also stamps it onto the access token as a namespaced
    # custom claim (get_current_user falls back to a plain "email" claim for
    # OIDC providers that do put it on the access token, e.g. the Keycloak
    # realm used in tests).
    email_claim: str = Field(default="https://api.biosimulations.org/email", alias="AUTH0_EMAIL_CLAIM")

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", populate_by_name=True)

    def issuer_url(self) -> str:
        return self.issuer or f"https://{self.domain}/"

    def jwks_url(self) -> str:
        return self.jwks_uri or f"https://{self.domain}/.well-known/jwks.json"

    def configuration_errors(self) -> list[str]:
        """
        Names every reason these settings could not verify a token.

        Returns an empty list when the configuration is usable. Pure and free
        of side effects, so the startup gate, the tests, and any future /ready
        check can all call it without coordinating.

        Two shapes are accepted:
          * the normal Auth0 one -- a bare AUTH0_DOMAIN, from which issuer_url()
            and jwks_url() are derived; or
          * explicit AUTH0_ISSUER *and* AUTH0_JWKS_URI overrides, which is how
            a non-Auth0 OIDC provider is configured (the Keycloak realm in
            tests/fixtures/keycloak does exactly this).
        Half of the second shape is not a valid configuration and is reported.
        """
        errors: list[str] = []

        if not self.audience:
            errors.append("AUTH0_AUDIENCE is not set")

        if not self.domain:
            if not self.issuer:
                errors.append(
                    "AUTH0_DOMAIN is not set and no AUTH0_ISSUER override was provided"
                )
            if not self.jwks_uri:
                errors.append(
                    "AUTH0_DOMAIN is not set and no AUTH0_JWKS_URI override was provided"
                )
        elif "://" in self.domain or "/" in self.domain:
            errors.append(
                f"AUTH0_DOMAIN must be a bare hostname such as "
                f"'tenant.us.auth0.com', not a URL or a path: {self.domain!r}"
            )
        elif self.domain != self.domain.strip() or "." not in self.domain:
            errors.append(
                f"AUTH0_DOMAIN does not look like a hostname: {self.domain!r}"
            )
        if not self.algorithms:
            errors.append("the JWT algorithm allowlist resolved to an empty list")
        return errors


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
    # biosimulations per-run records (note the space); source of the `simulator`
    # facet. Keyed by `id` == a project's `simulationRun`.
    mongodb_collection_biosimulations_runs: str = "Simulation Runs"
    # Platform-owned materialized search collection (Phase 1 $text). Built by
    # reading the biosimulations collections above; we own its $text index. The
    # "Platform" prefix keeps it clearly ours, not a biosimulations collection.
    mongodb_collection_project_search: str = "PlatformProjectSearch"
    # $text searchable fields -> relevance weights (higher = ranks stronger).
    # Each field must exist on the search document: title/abstract/description are
    # text; keywords/taxa are label arrays (Mongo text-indexes them element-wise).
    # To retune, change this and restart (ensure_indexes drops & recreates the
    # index when it differs) — a `POST /projects/reindex` is only needed if the
    # set of stored document fields changes, not for weight/field-toggle changes.
    project_search_text_weights: dict[str, int] = {
        "title": 10,
        "abstract": 5,
        "description": 1,
        "keywords": 4,
        "taxa": 3,
        "biology": 4,
        "modelFormats": 3,
        "simulationTypes": 2,
        "simulationAlgorithms": 2,
        "simulator": 3,
    }
    # Bearer token required to call POST /projects/reindex. Empty (default)
    # disables the endpoint entirely — the routine rebuild runs as an in-cluster
    # CronJob (direct Mongo, no token), and admins can reindex via
    # `python -m biosim_server.projects.reindex_cli`. Set a token only to enable
    # ad-hoc HTTP-triggered reindexing.
    project_reindex_token: str = ""
    # Legacy pre-2022 materialized summary; dead/abandoned (nothing writes it).
    # Kept only for reference — the API assembles from Projects + Metadata live.
    mongodb_collection_project_summary: str = "projectSummary"
    # TTL (seconds) for the platform-owned facet-stats cache.
    project_stats_cache_ttl_seconds: int = 300

    simdata_api_base_url: str = "https://simdata.api.biosimulations.org"
    biosimulators_api_base_url: str = "https://api.biosimulators.org"
    biosimulations_api_base_url: str = "https://api.biosimulations.org"
    auth0: Auth0Settings = Field(default_factory=Auth0Settings)

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
