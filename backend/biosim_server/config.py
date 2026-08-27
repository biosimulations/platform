import json
import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TrustedIssuer:
    """One explicitly trusted issuer and the audiences it may mint tokens for (P3 #27).

    Token validation looks up the token's ``iss`` in this map and then accepts
    only that issuer's audiences. An audience configured for issuer A is never
    valid for issuer B merely because both appear in the same deployment.
    """

    issuer: str
    audiences: tuple[str, ...]
    jwks_uri: str


def parse_trusted_issuers_json(raw: str) -> tuple[dict[str, TrustedIssuer], list[str]]:
    """Parse ``AUTH0_TRUSTED_ISSUERS``.

    Returns ``(mapping, errors)``. ``mapping`` is empty when ``raw`` is blank
    (single-issuer mode) *or* when ``errors`` is non-empty. Callers must not
    treat a parse failure as "fall back to AUTH0_AUDIENCE for any issuer".
    """
    text = raw.strip()
    if not text:
        return {}, []

    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError:
        return {}, ["AUTH0_TRUSTED_ISSUERS is not valid JSON"]

    if not isinstance(payload, dict):
        return {}, [
            "AUTH0_TRUSTED_ISSUERS must be a JSON object mapping issuer URL -> "
            "{audiences, jwks_uri}"
        ]
    if not payload:
        return {}, [
            "AUTH0_TRUSTED_ISSUERS must not be an empty object; omit the variable "
            "to use AUTH0_DOMAIN/AUTH0_AUDIENCE"
        ]

    mapping: dict[str, TrustedIssuer] = {}
    errors: list[str] = []
    for raw_issuer, spec in payload.items():
        issuer = raw_issuer.strip() if isinstance(raw_issuer, str) else ""
        if not issuer:
            errors.append("AUTH0_TRUSTED_ISSUERS contains an empty issuer key")
            continue
        if not _looks_like_absolute_url(issuer):
            errors.append(
                f"AUTH0_TRUSTED_ISSUERS issuer {issuer!r} must be an absolute http(s) URL"
            )
            continue
        if not isinstance(spec, dict):
            errors.append(
                f"AUTH0_TRUSTED_ISSUERS[{issuer!r}] must be an object with audiences and jwks_uri"
            )
            continue

        audiences, audience_error = _parse_trusted_audiences(spec.get("audiences"), issuer)
        if audience_error:
            errors.append(audience_error)
            continue

        jwks_uri = spec.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri.strip():
            errors.append(f"AUTH0_TRUSTED_ISSUERS[{issuer!r}] is missing a string jwks_uri")
            continue
        jwks_uri = jwks_uri.strip()
        if not _looks_like_absolute_url(jwks_uri):
            errors.append(
                f"AUTH0_TRUSTED_ISSUERS[{issuer!r}].jwks_uri must be an absolute http(s) URL"
            )
            continue

        extra = set(spec) - {"audiences", "jwks_uri"}
        if extra:
            errors.append(
                f"AUTH0_TRUSTED_ISSUERS[{issuer!r}] has unknown field(s): {sorted(extra)}"
            )
            continue

        mapping[issuer] = TrustedIssuer(
            issuer=issuer, audiences=audiences, jwks_uri=jwks_uri
        )

    if errors:
        return {}, errors
    return mapping, []


def _parse_trusted_audiences(raw: object, issuer: str) -> tuple[tuple[str, ...], str | None]:
    if not isinstance(raw, list) or not raw:
        return (), f"AUTH0_TRUSTED_ISSUERS[{issuer!r}].audiences must be a non-empty JSON array"
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return (), (
                f"AUTH0_TRUSTED_ISSUERS[{issuer!r}].audiences must be non-empty strings"
            )
        audience = item.strip()
        if audience not in seen:
            seen.add(audience)
            out.append(audience)
    return tuple(out), None


def _looks_like_absolute_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("http://")


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
    # NOTE: there is deliberately no `algorithms` field here (P1 #14). It was a
    # bare, unaliased field, which under env_prefix="" bound to a stray
    # ALGORITHMS environment variable -- see common/auth/auth0.py's
    # _ALLOWED_ALGORITHMS, a hardcoded module constant, for the replacement
    # and the full rationale.
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
    # Same story as email_claim: Auth0 access tokens don't carry email_verified
    # by default, so the Post-Login Action stamps it as a namespaced claim
    # (auth0/actions/post-login.js). Authorization treats a missing claim as
    # unverified (fail closed) -- see get_current_user and
    # roles.require_owner_or_admin.
    email_verified_claim: str = Field(
        default="https://api.biosimulations.org/email_verified", alias="AUTH0_EMAIL_VERIFIED_CLAIM"
    )
    # Auth0 RBAC "Add Permissions in the Access Token" puts API permissions on
    # the access token as a `permissions` array (not namespaced). Override only
    # if a custom Action stamps a different claim. Missing/malformed values are
    # treated as no permissions (fail closed) -- see get_current_user.
    permissions_claim: str = Field(default="permissions", alias="AUTH0_PERMISSIONS_CLAIM")
    # Optional JSON object mapping issuer URL -> {audiences, jwks_uri}. When
    # set, token validation uses this explicit pairing and does *not* accept
    # "any trusted issuer + any trusted audience". When unset, the existing
    # single AUTH0_DOMAIN/AUTH0_AUDIENCE (or AUTH0_ISSUER/AUTH0_JWKS_URI) shape
    # is used unchanged. See parse_trusted_issuers_json and docs/auth0-tokens-claims-endpoints.md.
    trusted_issuers_json: str = Field(default="", alias="AUTH0_TRUSTED_ISSUERS")

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", populate_by_name=True)

    def issuer_url(self) -> str:
        return self.issuer or f"https://{self.domain}/"

    def jwks_url(self) -> str:
        return self.jwks_uri or f"https://{self.domain}/.well-known/jwks.json"

    def has_explicit_trusted_issuers(self) -> bool:
        """True when AUTH0_TRUSTED_ISSUERS is set (even if the JSON is malformed)."""
        return bool(self.trusted_issuers_json.strip())

    def trusted_issuer_map(self) -> dict[str, TrustedIssuer]:
        """Parsed issuer -> trust entry. Empty when unset *or* unparseable."""
        mapping, errors = parse_trusted_issuers_json(self.trusted_issuers_json)
        if errors:
            return {}
        return mapping

    def lookup_trusted_issuer(self, iss: str | None) -> TrustedIssuer | None:
        """Exact ``iss`` lookup in AUTH0_TRUSTED_ISSUERS. None if unset or unknown."""
        if not self.has_explicit_trusted_issuers() or not isinstance(iss, str) or not iss:
            return None
        return self.trusted_issuer_map().get(iss)

    def configuration_errors(self) -> list[str]:
        """
        Names every reason these settings could not verify a token.

        Returns an empty list when the configuration is usable. Pure and free
        of side effects, so the startup gate, the tests, and any future /ready
        check can all call it without coordinating.

        Three shapes are accepted:
          * the normal Auth0 one -- a bare AUTH0_DOMAIN, from which issuer_url()
            and jwks_url() are derived; or
          * explicit AUTH0_ISSUER *and* AUTH0_JWKS_URI overrides, which is how
            a non-Auth0 OIDC provider is configured (the Keycloak realm in
            tests/fixtures/keycloak does exactly this);
          * AUTH0_TRUSTED_ISSUERS, an explicit issuer -> {audiences, jwks_uri}
            map (P3 #27). When that variable is set, it is the source of truth
            for token validation; AUTH0_AUDIENCE is not required.
        Half of the second shape is not a valid configuration and is reported.
        A malformed AUTH0_TRUSTED_ISSUERS is never treated as "allow anything".
        """
        errors: list[str] = []

        if self.has_explicit_trusted_issuers():
            _mapping, parse_errors = parse_trusted_issuers_json(self.trusted_issuers_json)
            errors.extend(parse_errors)
            if self.domain:
                errors.extend(self._domain_format_errors())
            return errors

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
        else:
            errors.extend(self._domain_format_errors())
        return errors

    def _domain_format_errors(self) -> list[str]:
        if "://" in self.domain or "/" in self.domain:
            return [
                f"AUTH0_DOMAIN must be a bare hostname such as "
                f"'tenant.us.auth0.com', not a URL or a path: {self.domain!r}"
            ]
        if self.domain != self.domain.strip() or "." not in self.domain:
            return [f"AUTH0_DOMAIN does not look like a hostname: {self.domain!r}"]
        return []

class RateLimitSettings(BaseSettings):
    """
    Per-pod rate limiting for workflow-starting endpoints (TODO P1 #10).

    PER-POD, NOT GLOBAL: `api` runs 3 replicas (kustomize/base/api.yaml:8) and
    this limiter (common/ratelimit.py) keeps its counters in a single
    process's memory -- there is no Redis or other shared datastore in this
    stack today. If traffic distributes evenly across all 3 pods, a caller
    can achieve up to 3x the configured per-pod number before every pod has
    independently started rejecting it. To target a specific GLOBAL ceiling
    G, configure authenticated_per_window / anonymous_per_window as
    G / replica_count (currently G / 3). A precise, cluster-wide limit needs
    a Mongo- or Redis-backed shared counter -- named as explicit P2/P3 future
    work, not built here.
    """

    # Operational kill-switch, mirroring AUTH_REQUIRED's role as an incident
    # lever (Auth0Settings.required, above). Set false to disable rate
    # limiting entirely without a code change -- e.g. if the limiter itself
    # is misbehaving during an incident.
    enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")
    # Materially higher than the anonymous quota -- authenticating should be
    # worth something. Defaults are starting points to tune from observed
    # traffic (see the tutorial's Operational Considerations), not a
    # precision-engineered ceiling. Both are PER-POD; see the class
    # docstring for the replica-count arithmetic.
    authenticated_per_window: int = Field(default=30, alias="RATE_LIMIT_AUTHENTICATED_PER_WINDOW")
    anonymous_per_window: int = Field(default=5, alias="RATE_LIMIT_ANONYMOUS_PER_WINDOW")

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", populate_by_name=True)

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
    ratelimit: RateLimitSettings = Field(default_factory=RateLimitSettings)
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
    # #20: mount the /api/v1/demo/* RBAC teaching router only when explicitly
    # enabled. Default false so any cluster that says nothing gets the safe
    # behaviour -- the demo endpoints 404 and are absent from the OpenAPI schema
    # in production. Explicit alias on the P1 #14 precedent (a bare, generically
    # named env binding is the hazard that removed the algorithms field).
    enable_rbac_demo: bool = Field(default=False, alias="ENABLE_RBAC_DEMO")
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
