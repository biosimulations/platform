# CLAUDE.md - Platform Backend Development Guide

This guide covers the backend service. For monorepo orientation (frontend, kustomize, deployment), see the root `CLAUDE.md`. For the integrated local-dev workflow (Mongo + Temporal in containers, backend + frontend native), see the **Local development** section of the root `README.md`.

All commands below assume the working directory is `backend/` (i.e., `cd backend` from the repo root first).

## Project Overview

The platform backend is a distributed microservices application for biosimulation verification and comparison. It runs biological simulations across multiple simulators (AMICI, COPASI, PySCES, Tellurium, VCell) and compares outputs to verify model correctness.

**Version:** 0.4.0
**Python:** 3.13
**Production URL:** [https://biosim.biosimulations.org/docs](https://biosim.biosimulations.org/docs)

## Quick Commands

```bash
# Install dependencies
uv sync

# Run API server locally
uv run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000

# Run worker locally
uv run python -m biosim_server.worker.worker_main

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=biosim_server

# Type checking
uv run mypy biosim_server

# Single test file
uv run pytest tests/biosim_runs/test_sim_workflow.py -v

# Regenerate the committed OpenAPI artifact after changing any route or model
uv run python -m scripts.generate_openapi
```

Always regenerate the spec with `scripts/generate_openapi.py` rather than by hand.
The demo router is environment-gated (`ENABLE_RBAC_DEMO`, default false), so dumping
`app.openapi()` in a default shell silently deletes `/api/v1/demo/*` from the
artifact; the script forces the flag on so the output does not depend on the
shell it ran in.

## Verification

Run these checks on every changeset before considering work complete (both `biosim_server/` source and the `tests/` suite are in scope):

```bash
# Linting
uv run ruff check .

# Type checking (strict mode)
uv run mypy biosim_server tests

# Tests (exclude integration tests that hit external APIs)
uv run pytest -m "not integration"
```

These are the same checks the repo-root `lefthook.yml` runs on commit/push and that `frontend-ci` / `backend-ci` gate on `main`.

## Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                   REST API (FastAPI)                     │
│                   Port 8000                              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│          Temporal Workflow Orchestration                 │
│              localhost:7233                              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│          Worker Processes (Temporal Workers)             │
│              verification_tasks queue                    │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│     MongoDB + Google Cloud Storage                       │
└─────────────────────────────────────────────────────────┘
```

## Directory Structure

```text
backend/
├── biosim_server/
│   ├── api/                    # FastAPI REST endpoints
│   │   └── main.py            # App entry, all endpoints
│   ├── biosim_runs/           # Simulation execution
│   │   ├── activities.py      # Temporal activities (submit, poll, retrieve)
│   │   ├── biosim_service.py  # HTTP client for biosimulations.org API
│   │   ├── database.py        # MongoDB operations for sim runs
│   │   ├── models.py          # Pydantic models (BiosimulatorVersion, etc.)
│   │   └── workflows.py       # OmexSimWorkflow
│   ├── biosim_verify/         # Verification workflows
│   │   ├── activities.py      # generate_statistics_activity
│   │   ├── models.py          # Verification models
│   │   ├── omex_verify_workflow.py  # Multi-simulator OMEX verification
│   │   ├── runs_verify_workflow.py  # Compare existing runs
│   │   └── hdf5_compare.py    # Comparison logic
│   ├── biosim_omex/           # OMEX file handling
│   │   ├── database.py        # MongoDB for OMEX metadata
│   │   ├── models.py          # OmexFile model
│   │   └── omex_storage.py    # Upload/caching logic
│   ├── compatibility/         # OMEX compatibility checking
│   │   ├── models.py          # CompatibilityResponse, EligibleSimulator, etc.
│   │   ├── router.py          # POST /compatibility/check
│   │   ├── omex_parser.py     # Parse OMEX archives for model/algorithm info
│   │   ├── simulator_matcher.py # Match simulators to OMEX requirements
│   │   ├── kisao_data.py      # KiSAO ontology data for algorithm matching
│   │   └── equivalence_categories.yaml # Algorithm equivalence groups
│   ├── simulations/           # Simulation run management (backend-for-frontend)
│   │   ├── activities.py      # Temporal activities (submit, poll, retrieve results)
│   │   ├── models.py          # RunSimulationRequest, ConglomerateStatus, etc.
│   │   ├── router.py          # POST /simulations/run, GET /simulations/{id}
│   │   └── workflow.py        # SimulationRunWorkflow (Temporal)
│   ├── common/
│   │   ├── storage/           # GCS file operations
│   │   ├── temporal/          # Temporal client utilities
│   │   ├── hpc/               # SLURM integration
│   │   └── ssh/               # SSH service
│   ├── worker/
│   │   └── worker_main.py     # Worker entry point
│   ├── config.py              # Pydantic Settings
│   ├── dependencies.py        # Global service instances
│   └── version.py             # Version string
├── tests/                     # pytest suite
├── scripts/                   # backend tooling (KiSAO data generation, etc.)
├── docs/                      # backend-specific docs
├── pyproject.toml
├── uv.lock
├── pytest.ini
├── Dockerfile.api
├── Dockerfile.worker
└── .dockerignore
```

## Key API Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/projects/{id}/page` | GET | Platform-owned project page aggregation (public; rate-limited per client IP; 3 upstream calls) |
| `/runs/{id}/page` | GET | Platform-owned run page aggregation (public; rate-limited per client IP; 4 upstream calls) |
| `/compatibility/check` | POST | Check OMEX archive compatibility with simulators (public; rate-limited; `archive_url` SSRF-restricted) |
| `/simulations/run` | POST | Run simulations for an OMEX archive across selected simulators |
| `/simulations/runs` | POST | List simulation runs (`type=all` public with email redacted; `type=user` scoped to `owner_sub`) |
| `/simulations/{processing_id}` | GET | Get status of a simulation run |
| `/verify/omex` | POST | Verify OMEX file across simulators (authenticated; persists `owner_sub`) |
| `/verify/{workflow_id}` | GET | Get verification results (authenticated; owner-or-admin when `owner_sub` is set) |
| `/verify/runs` | POST | Compare existing biosimulation runs (authenticated; persists `owner_sub`) |
| `/api/v1/me/password-reset` | POST | Issue a short-lived Auth0-hosted password-change URL for the authenticated primary database user (New Universal Login; no email is sent; requires `create:user_tickets` on the server M2M client) |
| `/validation/model` | POST | Validate a model document via the COMBINE API (relay; works with or without auth; rate-limited) |
| `/validation/sed-ml` | POST | Validate a SED-ML document via the COMBINE API (relay; works with or without auth; rate-limited) |
| `/version` | GET | Get API version |
| `/docs` | GET | Swagger UI |

## Database Collections (MongoDB)

- **BiosimOmex** - OMEX file metadata (file_hash_md5, gcs_path)
- **BiosimSims** - Simulation workflow runs (workflow_id, status, results)
- **BiosimCompare** - Comparison results
- **BiosimSimulationRuns** - User-facing run records for the `/simulations/runs` listing (one per submission × simulator; run_id, processing_id, name, simulator, email, status, timestamps)

## Key Patterns

### Async Everywhere

All I/O is async: FastAPI endpoints, MongoDB (Motor), HTTP (aiohttp), file ops.

### Temporal Workflows

- `OmexSimWorkflow` - Run single simulator on OMEX file
- `OmexVerifyWorkflow` - Orchestrate multiple simulators in parallel, then compare
- `RunsVerifyWorkflow` - Compare existing runs
- `SimulationRunWorkflow` - Run simulations without comparison (backend-for-frontend)

### Caching

- OMEX files cached by MD5 hash in MongoDB + GCS
- Sim results cached by (file_hash_md5, image_digest, cache_buster)
- Simulator versions cached in memory (aiocache, 1hr TTL)

### Dependency Injection

Global services in `dependencies.py`:

```python
get_file_service()          # GCS operations
get_database_service()      # MongoDB for runs
get_omex_database_service() # MongoDB for OMEX
get_biosim_service()        # biosimulations.org API client
get_temporal_client()       # Temporal workflow client
```

## Configuration

Environment variables (see `config.py`). All have defaults; override as needed.

### Storage

| Variable | Default | Purpose |
| --- | --- | --- |
| `STORAGE_BACKEND` | `gcs` | Which `FileService` implementation to construct. One of `gcs`, `local`, `minio`. |
| `STORAGE_BUCKET` | `files.biosimulations.dev` | Bucket name (used by `gcs` and `minio` backends). |
| `STORAGE_ENDPOINT_URL` | `https://storage.googleapis.com` | S3-compatible endpoint URL (used by `minio`; e.g., `http://localhost:9000` for a local minio). |
| `STORAGE_REGION` | `us-east4` | Region (used by `minio`). |
| `STORAGE_ACCESS_KEY` | _empty_ | S3 access key (used by `minio`). |
| `STORAGE_SECRET_KEY` | _empty_ | S3 secret key (used by `minio`). |
| `STORAGE_GCS_CREDENTIALS_FILE` | _empty_ | Path to GCS service account JSON (used by `gcs`). |
| `STORAGE_LOCAL_CACHE_DIR` | `./local_cache` | Base directory for the `local` backend and on-disk caches. |

### External APIs

These point at the public biosimulations.org services. Defaults are production; override only to target a staging instance or a mock for tests.

| Variable | Default | Used by |
| --- | --- | --- |
| `BIOSIMULATIONS_API_BASE_URL` | `https://api.biosimulations.org` | `BiosimServiceRest` submission/polling, typed summaries/pages, and legacy `/runs` proxies |
| `SIMDATA_API_BASE_URL` | `https://simdata.api.biosimulations.org` | `BiosimServiceRest` — fetch HDF5 outputs |
| `BIOSIMULATORS_API_BASE_URL` | `https://api.biosimulators.org` | Simulator version metadata |
| `COMBINE_API_BASE_URL` | `https://combine.api.biosimulations.org` | COMBINE validation service, relayed by `/validation/*` |
| `COMBINE_MAX_REQUEST_BYTES` | `20971520` (20 MiB) | Upload cap for `/validation/*`, matching the ingress `proxy-body-size` |
| `COMBINE_MAX_RESPONSE_BYTES` | `16777216` (16 MiB) | Cap on a relayed validation report |

### Why `/validation/*` exists

`combine.api.biosimulations.org` returns **no** `Access-Control-Allow-Origin`
header for any origin, and its preflight answers 200 with no `Access-Control-*`
headers at all. A browser treats that as a denial, so the frontend's
validate-\* utility pages cannot reach it from the client anywhere -- production
included, not only localhost. Relaying server-side moves the request onto the
Platform's own origin, which the frontend is already allowed to call.

It is a **relay, not an owned contract**: the validation report is COMBINE's
schema and we neither model nor validate it. `docs/legacy-api-passthrough-policy.md`
requires that to be justified rather than assumed; the justification is that
alternative here is not a worse contract but a feature that cannot work in any
browser, and there is no Platform concept to own -- the report belongs to the
COMBINE validators. If the Platform grows its own validation, this is deleted
rather than promoted.

Authentication is **advisory** (`get_advisory_user`): validation needs no
identity, so a valid token only raises the rate-limit ceiling and a token that
cannot be validated is treated as absent rather than refused. This differs
deliberately from `get_optional_user`, which rejects an invalid token because on
a *creation* path an expired one would silently produce a public resource -- no
resource is created here, and the frontend attaches the Platform token to every
Platform-bound request, so a lapsed session must not break a public page.

No caller credentials are forwarded upstream. The two upstream paths are
literals, so no caller input reaches the upstream URL.

### Infrastructure

| Variable | Default | Purpose |
| --- | --- | --- |
| `TEMPORAL_SERVICE_URL` | `localhost:7233` | Temporal workflow server |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGODB_DATABASE` | `biosimulations` | MongoDB database name |

### CORS

| Variable | Default | Purpose |
| --- | --- | --- |
| `CORS_EXTRA_ORIGINS` | _empty_ | Comma-separated list of additional CORS origins appended to the built-in allowlist in `api/main.py`. **Required** for every deployment so the deployed frontend host (e.g. `https://biosim.biosimulations.org`) is allowed. The built-in list only covers local-dev loopbacks and cross-org trusted services — deploy-specific URLs are not hardcoded by design. |

### Legacy runs proxy

Six transparent operations against `BIOSIMULATIONS_API_BASE_URL`: GET/PATCH/DELETE
`/runs/{id}`, GET `/runs/{id}/download`, GET `/runs/{id}/validate`, and GET
`/runs/summary`. They use their own pooled client (`dependencies.get_legacy_http_client`),
separate from the page/summary client, so proxied traffic cannot exhaust the pool the
public pages need.
`GET /runs/{id}/summary` keeps the existing typed `RunSummary` projection,
credential/query isolation, size cap and sanitized upstream errors.

The transparent operations forward caller `Authorization` without local Auth0
validation or platform ownership checks. Authorization belongs to the upstream;
no service credentials are added, and neither caller nor pooled-client cookies
are sent. Requests are attempted once, with no retry and no redirect following.
A returned redirect retains `Location`. Received statuses and opaque bodies,
including 4xx/5xx, are relayed with allowlisted headers. Transport failures before
response delivery are sanitized 502/504; failures after download headers have
been sent abort the stream and close upstream resources.

PATCH forwards raw bytes and the caller's Content-Type; upstream owns field
validation. `LEGACY_PATCH_MAX_BYTES` is **20 MiB**, matching the existing gke/rke
20m ingress ceiling (not the unrelated 16 MiB `UPSTREAM_MAX_RESPONSE_BYTES` cap on buffered *response* bodies). Actual chunks
are counted, including requests without Content-Length. Over-limit requests
return 413 before any mutation is sent. GET and DELETE send no body.

Query bytes retain repeated keys and original escaping. IDs use `upstream_url`:
dot-only IDs are rejected, special characters quoted, and decoded slashes do
not match the single-segment route. Accept and Authorization are allowlisted;
GET also forwards If-None-Match/If-Modified-Since, download additionally Range
and If-Range, and PATCH additionally Content-Type. If-Range travels with Range so a
resumed download with a stale validator gets the full current 200 from upstream,
not a 206 slice of a newer file (RFC 9110 §13.1.5). Connection-nominated headers are stripped.
Host/framing are generated afresh; arbitrary X-* and proxy headers are not sent.

Downloads stream raw bytes (also for non-success bodies), remain open through
downstream iteration, and close on completion, disconnect or failure. Because each one
holds a pooled connection for as long as its reader takes, at most
`LEGACY_DOWNLOAD_MAX_CONCURRENT` (default **32**, per pod) run at once; beyond that a
download is refused with **503** + `Retry-After: 5` before anything is sent upstream
(`legacy_outcome: busy`). The proxy pool holds that many plus 32, so the other
operations, which buffer their bodies and so are never pinned by a slow reader, keep
headroom. A caller who holds every slot can still deny *downloads* to others (there is no
per-IP share); the cap bounds the damage to the download path. Upstream
Content-Encoding is preserved with raw bytes so compression cannot invalidate
Content-Length, Content-Range or ETag. Accept-Encoding is explicitly `identity`,
but compressed responses are still handled correctly. Metadata responses (all
buffered non-download, non-204/304 paths) buffer opaque raw bytes and recalculate
length; they are capped at `UPSTREAM_MAX_RESPONSE_BYTES` raw bytes (the Page
aggregation table below documents that setting's two consumers and their
differing measurement bases). A buffered body that exceeds the cap is refused
with a sanitized **502** (`legacy_outcome: too_large`), never relayed truncated.
The cap is measured on raw wire bytes, not decoded bytes, so `Content-Encoding`
cannot widen it. Downloads stream lazily and are never buffered or capped. Response
headers are limited to Content-Type/Disposition/Length/Range/Encoding,
Accept-Ranges, ETag, Last-Modified, Cache-Control, Expires, Vary, Location and
Retry-After; Set-Cookie and hop-by-hop headers are stripped.

Each operation logs only `legacy_operation`, `legacy_outcome`, `legacy_status`,
`legacy_duration_ms`, and `legacy_bytes`. The proxy suppresses httpx's full-URL
INFO line for its outbound request. Deployment access-log policy remains owned
by the ingress/server; do not enable logs containing credentials or query values.

Contract evidence: read-only inspection of the live
[legacy OpenAPI](https://api.biosimulations.org/openapi.json) on 2026-09-28 found:
PATCH `application/json` / `UpdateSimulationRun` (status, fileUrl, projectSize,
resultsSize), OAuth scopes `write:SimulationRuns` for PATCH and
`delete:SimulationRuns` for DELETE, and DELETE success 204. Collection summaries
are a JSON array with `read:SimulationRuns`; no pagination parameters are
specified. Validation documents 204/400 and `validateSimulationResultsData`.
Download documents 200/301/404. No PATCH size or Range/conditional support is
specified. The proxy's raw-body policy, 20 MiB bound, optional header forwarding
and permissive query relay are platform compatibility decisions, not claims of
additional upstream support. Platform Auth0 tokens are not guaranteed to have
the legacy audience/scopes; upstream 401/403 remain visible. No live mutation
was performed to verify the documentation.

Frontend rollout is separate: switch direct legacy calls and download links to
the platform base URL after deployment. **Run deletion has already switched**: the
Simulations page sends `DELETE` to `${api_url}/runs/{id}`, because the auth0 plugin now
attaches the Platform token only to Platform API requests and a direct legacy call would
go out unauthenticated (401). That frontend build therefore needs a backend release that
includes this proxy already deployed in the same cluster; deploy the backend first. The current legacy summary consumer
expects an array of metadata; it must adapt to the typed platform summary before
switching that URL. Prefer same-origin download navigation; cross-origin fetch
clients must not assume Content-Disposition is exposed by CORS.

### Page aggregation (public page endpoints)

`GET /projects/{id}/page` and `GET /runs/{id}/page` assemble a page from 3-4 upstream
calls each. Both are public (no authentication) and metered — see Rate Limiting — and both
bound what they will buffer from the upstream API this project does not own.

| Variable | Default | Purpose |
| --- | --- | --- |
| `UPSTREAM_MAX_RESPONSE_BYTES` | `16777216` (16 MiB) | Hard ceiling on one upstream response body, on a different measurement basis per consumer. **Page aggregations** (this section): one upstream JSON body's **decoded** size. Fetches ask for `Accept-Encoding: identity`; a body compressed anyway (gzip/deflate) is inflated in 64 KiB steps in `common/upstream.py`, each checked against the cap before it is kept, so peak memory is the cap plus one step whatever the compression ratio (httpx's own decoder inflated a whole network read first, ~64 MiB from one 64 KiB read). Any other or stacked `Content-Encoding` is a 502. **Legacy runs proxy** (see Legacy runs proxy): buffered non-download responses are counted on **raw** bytes through `aiter_raw()` (`biosim_runs/legacy_api.py`), because the proxy relays raw bytes and never decodes them — so a compressed body is capped on the wire, not after expansion. In both consumers a declared `Content-Length` decides nothing, the stream is abandoned the moment the cap is crossed, and a breach is a sanitized **502** (`too_large` in the logs) — never a truncated payload. Provisional pending the representative-payload measurements the page audit asks for; raise it per cluster if a legitimate response exceeds it. |

`UPSTREAM_MAX_RESPONSE_BYTES` is not page-specific: it is the single shared ceiling for
every buffered upstream body the API relays, including the legacy runs proxy's opaque
responses. Both consumers measure the bytes they actually read, so the one setting is
enforced without either consumer widening the other's semantics.

Every platform-owned upstream fetch -- these pages and the typed `GET /runs/{id}/summary`
and `GET /projects/{id}/summary` -- is built afresh in `common/upstream.py`, never through
the pooled client's request builder. httpx stores every upstream `Set-Cookie` in a client's
jar, and a pooled client serves many callers (this one was shared with the legacy runs proxy
until the proxy got its own), so `client.get()`/`client.stream()` would replay one caller's
session (plus any client default headers, auth or query parameters) on another caller's
anonymous request. A public fetch takes only the client's base URL (query defaults and URL
credentials stripped) and timeout, sends a fixed `Accept`/`Accept-Encoding: identity`/
`User-Agent` set, never follows redirects, and never reads the jar. Clearing the jar instead
would race concurrent requests. Any new consumer of `get_http_client()` must build its
requests the same way.

Both pages also carry a total time budget derived from the shared per-phase httpx timeout
(`common/upstream.UPSTREAM_TIMEOUT_SECONDS`, 30 s) and the assembler's real serial depth —
two hops for the project page (identity → embedded run id → satellites) and one for the run
page (identity ∥ satellites) — plus 10 s of bounded local slack: **70 s** and **40 s**. The
httpx timeout is per phase, not per request: the read timeout restarts on every received
chunk, so a slow-drip body can keep one fetch alive well past 30 s, and these budgets are
the only total deadline on page assembly. A budget below the serial depth would cancel
healthy requests; one far above it (the run page's old flat 60 s, sized for two hops) lets
a slow-drip upstream hold the request longer than the assembly needs.

Each page emits one structured record per request (`page`, `page_outcome`, `page_status`,
`page_duration_ms`, `page_identity_duration_ms`, `page_satellites_duration_ms`) and one
per upstream fetch (`upstream_resource`, `upstream_outcome`, `upstream_duration_ms`,
`upstream_bytes`). `log_config.JsonFormatter` copies only allowlisted fields, so no id,
URL, or payload can reach the logs through `extra=`.

### Authentication (Auth0)

All values are **non-secret** and belong in each overlay's `api.env` ConfigMap, never in a
sealed secret. The Management API credentials at the bottom are the exception — they are
credentials, are unset in every overlay today, and are tracked separately (TODO #23).

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTH_REQUIRED` | `true` | Startup gate. When true, the API refuses to start unless the Auth0 configuration below is complete and well-formed. Set to `false` only to run deliberately without an identity provider. |
| `AUTH0_DOMAIN` | _empty_ | **Bare hostname** of the Auth0 tenant, e.g. `tenant.us.auth0.com`. The issuer (`https://{domain}/`) and JWKS URL (`https://{domain}/.well-known/jwks.json`) are derived from it. A URL or a trailing slash here is a configuration error and is rejected at startup. |
| `AUTH0_AUDIENCE` | _empty_ | The API identifier tokens must carry in `aud`. |
| `AUTH0_ISSUER` | _derived_ | Explicit issuer override. Needed only for a non-Auth0 OIDC provider whose issuer does not follow Auth0's convention (the Keycloak realm used in tests). Must be set together with `AUTH0_JWKS_URI`. |
| `AUTH0_JWKS_URI` | _derived_ | Explicit JWKS URL override. See above. |
| `AUTH0_ROLES_CLAIM` | `https://api.biosimulations.org/roles` | Namespaced claim carrying role names. **Stamped by the Auth0 Post-Login Action** (`auth0/actions/post-login.js`) — without that Action the claim never arrives, every `require_roles` endpoint returns 403, and no admin exists. |
| `AUTH0_EMAIL_CLAIM` | `https://api.biosimulations.org/email` | Namespaced claim carrying the user's email. Also stamped by the Action; `get_current_user` falls back to a plain `email` claim for providers that include one. |
| `AUTH0_EMAIL_VERIFIED_CLAIM` | `https://api.biosimulations.org/email_verified` | Namespaced claim carrying whether that email is verified. Stamped by the same Action. Authorization treats a missing claim as unverified (fail closed). Override only if the Action uses a different namespace. |
| `AUTH0_PERMISSIONS_CLAIM` | `permissions` | Auth0 RBAC access-token claim (array) used by `require_permissions`. Missing/malformed → no permissions (fail closed). Roles never satisfy a permission check. See `docs/auth0-tokens-claims-endpoints.md`. |
| `AUTH0_TRUSTED_ISSUERS` | _empty_ | Optional JSON object mapping `issuer` → `{audiences, jwks_uri}`. When set, token validation uses this **pairing** (an audience trusted for issuer A is not valid for issuer B). When unset, `AUTH0_DOMAIN`/`AUTH0_AUDIENCE` (or `AUTH0_ISSUER`/`AUTH0_JWKS_URI`) remain the single-issuer configuration. |
| `AUTH0_MANAGEMENT_CLIENT_ID` | _empty_ | M2M credentials for the Auth0 Management API: `PATCH`/`DELETE /api/v1/me` (`update:users`/`delete:users`) and `POST /api/v1/me/password-reset` (`create:user_tickets`). **Secret** — sealed-secret path only. Unset in every cluster today, so those endpoints return 503; password reset also requires the M2M application to be authorized for the `create:user_tickets` scope. |
| `AUTH0_MANAGEMENT_CLIENT_SECRET` | _empty_ | See above. |
| `AUTH0_PASSWORD_RESET_CLIENT_ID` | _empty_ | **Non-secret** SPA application client ID used as the `client_id` for the hosted password-change ticket (`POST /api/v1/me/password-reset`). Blank disables the endpoint (503). Not the M2M client ID. Configure New Universal Login and the SPA's Application Login URI in Auth0 before enabling. |
| `AUTH0_AUTH_TIME_CLAIM` | `auth_time` | Namespaced/standard claim carrying the end-user's last **interactive** authentication time. Read into `AuthenticatedUser.auth_time`; used only by the password-reset step-up gate. Missing/malformed → no evidence (fail closed). |
| `AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH` | `false` | Step-up gate on `POST /api/v1/me/password-reset` (decision D-12). When true, the token must carry an `auth_time` no older than the max age below, else **403** and no ticket. Needs the Post-Login Action to stamp the claim, so it ships off; the startup gate WARNs while the reset endpoint is configured without it. **Must be `true` in any overlay that sets `AUTH0_PASSWORD_RESET_CLIENT_ID`** (see Password-reset enablement below). A refreshed token or `iat` is deliberately not accepted as evidence. |
| `AUTH0_PASSWORD_RESET_MAX_AUTH_AGE_SECONDS` | `300` | How fresh that interactive authentication must be. Must be positive (validated at startup). |

**Per-cluster configuration:**

| Overlay | Auth0 configuration | Notes |
| --- | --- | --- |
| `biosim-gke` | tenant + audience set | Production tier. |
| `biosim-rke` | tenant + audience set | Prod tier; also backs the `biosim-rke-frontend-dev` preview frontend. |
| `biosim-local` | _record the Step 2 decision here_ | |

**Failure modes:**

| Condition | Result |
| --- | --- |
| Missing/malformed config, `AUTH_REQUIRED=true` | Pod refuses to start; `kubectl logs` names the variable. |
| Missing/malformed config, `AUTH_REQUIRED=false` | Pod starts; endpoints behind `get_current_user` return **503**. |
| Auth0 unreachable, warm JWKS cache | Tokens still validate for up to 24 h; a WARN is logged per request. |
| Auth0 unreachable, cold JWKS cache | **503** with `Retry-After: 10`. |
| Invalid, expired, or wrongly-audienced token | **401**. |
| Signed token with **no `exp`** claim, or a non-numeric one | **401** (`missing_exp` / `invalid_exp`). python-jose does not require `exp` by default; its `require_exp` option runs only after the JWKS lookup and lets `"exp": null` through, so the presence and type check is explicit and runs first. |
| Signed token whose `sub` has leading/trailing whitespace (or is whitespace-only) | **401**. Identity keys are never rewritten; `owner_sub` comparisons stay exact. |
| Token from a trusted issuer that is not the configured tenant | `GET /api/v1/me`: **200**, JWT-only profile, no Management call or enrichment. `PATCH`/`DELETE /api/v1/me`: **403**, no Management call. |
| `POST /api/v1/me/password-reset` with `AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH=true` and missing/stale/future `auth_time` | **403**, no ticket. |
| Valid token, missing role | **403**. |
| Valid token, missing required permission/scope | **403**. |
| Management API (`PATCH`/`DELETE /api/v1/me`) rate-limited (429) through all retries | **503** with `Retry-After`. |
| Management API 5xx or transport failure through all retries | **502**. |
| Password reset unconfigured (`AUTH0_DOMAIN`, `AUTH0_PASSWORD_RESET_CLIENT_ID`, or Management credentials blank) | **503** `Password reset is unavailable`. |
| Password-reset ticket 429 (Auth0 rate limit) | **503** with `Retry-After` set to Auth0's own `Retry-After` (rounded up, clamped to 30 s), or 10 s when it sends none; issuance is deliberately not retried (non-idempotent). |
| Auth0 token endpoint 429 (any Management-backed route) | **503** with `Retry-After`, for the caller that hit it **and** every caller during the shared refresh cooldown. The cooldown lasts as long as the delay advertised (Auth0's `Retry-After` as above, never under the 5 s cooldown), so concurrent callers are all sent back at the same moment and none re-posts to the token endpoint before then. One token request per throttling event per pod. |
| Password-reset ticket/token non-429 4xx, 5xx, transport, malformed or unsafe URL | **502** generic; upstream body is never logged or returned. |
| Valid token, non-`auth0\|` sub, or issuer ≠ `https://AUTH0_DOMAIN/` | **403** `Password reset is unavailable for this account`. |

**Password-reset enablement — merge-ready is not deploy-enabled.** Ticket issuance needs
`AUTH0_DOMAIN`, `AUTH0_PASSWORD_RESET_CLIENT_ID` and both Management API credentials;
any one blank is a 503. As of 2026-10-02 no overlay sets the reset client ID or the
Management credentials (no `api.env` entry, no `SealedSecret` key, no `secretKeyRef`), so
issuance is off in every cluster. That is the **only** condition under which the `false`
default of `AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH` is acceptable (D-12 in
`docs/auth0-p2-decisions.md`). Enabling issuance in any overlay requires, in the same
change:

1. `AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH=true` in that overlay's `api.env`. Setting the
   client ID without it is a **deployment blocker**: the startup WARN is a backstop, not
   an approval, and `kustomize/README-config.md` checklist step 6 fails on it.
2. A Post-Login Action that stamps the user's last interactive authentication time onto
   the **access token** under the claim `AUTH0_AUTH_TIME_CLAIM` names.
   `auth0/actions/post-login.js` does not do this today. If the tenant will not set a
   reserved OIDC claim name on access tokens, stamp a namespaced claim and point
   `AUTH0_AUTH_TIME_CLAIM` at it.
3. A tested interactive re-authentication step in whatever client calls the endpoint
   (force a fresh sign-in, e.g. `max_age`/`prompt=login`, immediately before requesting a
   ticket). A silently refreshed access token has a new `iat` but no new sign-in: `iat`
   is never accepted as evidence, so without this step every reset is a 403.
4. The Management M2M application authorized for `create:user_tickets`, with its
   credentials delivered through the sealed secret (`kustomize/README-config.md` → "Adding
   a new secret"), and the SPA's client ID in `api.env`.

**Known limitation: custom domains.** Issuance works only for tokens whose issuer is exactly
`https://{AUTH0_DOMAIN}/`, and only accepts a returned ticket URL whose host is exactly
`AUTH0_DOMAIN`. A tenant served under an Auth0 custom domain (tokens issued by the custom
domain, `AUTH0_ISSUER` set to it, `AUTH0_DOMAIN` left as the canonical tenant domain the
Management API needs) therefore gets **403** for every caller, or **502** if Auth0 returns the
ticket on the custom host. This is deliberate (`reset_my_password` in `users/router.py`
keeps the stricter domain-only rule instead of `_configured_tenant_issuers`), and harmless
while issuance is off everywhere. Before enabling reset on a custom-domain tenant, decide
which issuer and ticket host are acceptable and change both checks together.

**Enablement gate (separate from merging, run in an approved environment before
exposing the capability or any UI that calls it):** decode a live access token and confirm
the claim is present and current after an interactive sign-in and unchanged after a silent
refresh; confirm the endpoint returns 403 without fresh evidence; redeem one ticket end to
end on a test account. No live account mutation is authorized by merging. The profile
page's existing "Send Password Reset Link" calls Auth0's public
`/dbconnections/change_password` email flow, not this endpoint, and is unaffected.

**Anonymous `POST /simulations/run` (P1 #9 Option B).** This endpoint stays
reachable without a bearer token, paired with the workflow rate limiter
(P1 #10). The production frontend currently submits runs without an
`Authorization` header, so requiring auth here would break the UI. Authenticated
API clients still have their token's `sub` persisted as `owner_sub`. Anonymous
and frontend-originated runs persist `owner_sub = NULL` until the frontend
attaches tokens. This is an explicit product decision, not an omission.
Revisit when the frontend sends bearer tokens (that is the gate for Option A).

The frontend does **not** call `POST /verify/omex` or `POST /verify/runs`;
those two endpoints require authentication. **`GET /verify/{workflow_id}`
also requires an access token** (breaking change for anonymous Swagger/poll
clients). When the workflow payload includes `owner_sub` (set from the
starter's token on POST), GET is owner-or-admin; in-flight Temporal histories
with `owner_sub` unset remain readable by any authenticated caller. External
consumers of `/verify/*` must send a bearer token. No inventory of those
consumers exists in this repository — treat that as an operational follow-up
before advertising the gated contract as a breaking API change.

**Which token to send:** the Platform API is an OAuth resource server and
accepts **access tokens** only. Do not send an OIDC ID token in
`Authorization`. Full contract: `docs/auth0-tokens-claims-endpoints.md`.

**POST /projects/reindex (P1 #15): keep-and-harden.** The endpoint stays. It is
the only HTTP path that can rebuild the project search index without cluster
access. It remains disabled by default (`PROJECT_REINDEX_TOKEN` unset → 503)
and compares the bearer token with `secrets.compare_digest` on UTF-8 bytes.

### Rate Limiting

All values are **non-secret** and belong in each overlay's `api.env` ConfigMap. The limiter
(`biosim_server/common/ratelimit.py`) is **per-pod, not global** -- there is no Redis or
shared cache in this stack, so each pod enforces the configured number independently.
`kustomize/base/api.yaml` declares 3 replicas, but every deployed overlay (`biosim-gke`,
`biosim-rke`, `biosim-local`) overrides `api` to **1** (`overlays/<cluster>/kustomization.yaml`
→ `replicas:`), so today each configured value **is** the cluster-wide ceiling. If an overlay
runs `N` replicas, the effective ceiling becomes up to `N` times the value when traffic
distributes evenly; to target a global ceiling `G`, configure `G / N`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `RATE_LIMIT_ENABLED` | `true` | Kill-switch. Set `false` to disable rate limiting entirely without a code change -- an incident lever, mirroring `AUTH_REQUIRED`. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window size in seconds. |
| `RATE_LIMIT_AUTHENTICATED_PER_WINDOW` | `30` | Per-pod requests per window for a caller identified by a verified token's `sub`. |
| `RATE_LIMIT_ANONYMOUS_PER_WINDOW` | `5` | Per-pod requests per window for a caller identified only by client IP. |
| `RATE_LIMIT_PASSWORD_RESET_PER_WINDOW` | `5` | Per-pod requests per **password-reset** window, keyed by `(issuer, subject)`. Deliberately separate policy from the workflow ceilings so operators can tune a sensitive account action without changing simulation throughput. |
| `RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS` | `300` | Fixed-window size for the password-reset budget. |
| `RATE_LIMIT_PAGE_PER_WINDOW` | `60` | Per-pod requests per page window for the two public page aggregations, keyed by client IP. More generous than a workflow start because browsing is the ordinary reading path — it still bounds the 3-4-call upstream fan-out. |
| `RATE_LIMIT_PAGE_WINDOW_SECONDS` | `60` | Fixed-window size for the page-aggregation budget. |
| `RATE_LIMIT_LEGACY_PER_WINDOW` | `60` | Provisional shared legacy proxy budget per client IP per process; strictly positive. |
| `RATE_LIMIT_LEGACY_WINDOW_SECONDS` | `60` | Independent legacy fixed-window size in seconds; strictly positive. |

Protects `POST /verify/omex`, `POST /verify/runs`, and `POST /simulations/run` -- the three
endpoints that start a Temporal workflow. All three share ONE budget per caller identity, not
three separate ones.

`POST /compatibility/check` stays unauthenticated (run wizard) but is rate-limited with a
**separate** `compat:` bucket so it cannot starve workflow starts. `archive_url` is restricted
to http/https hosts that resolve to public addresses (SSRF).

`POST /api/v1/me/password-reset` has its own `password-reset:` bucket with its own
ceiling/window (`RATE_LIMIT_PASSWORD_RESET_*`) and a `(issuer, subject)` key, so it can
neither consume nor be consumed by workflow starts, and a same-named subject from a second
trusted issuer cannot burn another principal's quota before the route's issuer eligibility
check rejects it. Quota is charged before eligibility, so a denied principal cannot hammer
the 403 branch unbounded.

`GET /projects/{id}/page` and `GET /runs/{id}/page` share ONE `pages:` bucket keyed on
client IP alone (`page_rate_limit`), with their own ceiling/window (`RATE_LIMIT_PAGE_*`).
Keying on IP even when a bearer token is presented is deliberate: those routes are public
by design, so a caller must not be able to raise the ceiling or reset an exhausted bucket
by rotating tokens. A denied request never reaches upstream.

The six transparent legacy runs proxy operations (`/runs/summary`, `/runs/{id}` GET/PATCH/
DELETE, `/runs/{id}/download`, `/runs/{id}/validate`) share ONE `legacy:` budget keyed
only by client IP, using `legacy_rate_limit`. Caller credentials remain opaque to Platform
and are forwarded for upstream authorization; token rotation never restores quota.
The budget is independent of pages, workflow starts, compatibility checks and password
resets. Each admitted request costs one unit even if later proxy work fails; range/resume
requests count separately. A rejected request returns local **429 + Retry-After** before
reading the PATCH body, claiming a download slot or sending upstream traffic. Active
downloads continue. The independent download concurrency cap still returns **503**, and
an admitted request may relay an upstream **429** and its Retry-After unchanged.

If proxy and owned page/summary traffic share an egress IP and upstream throttling is
IP-based, proxy abuse can impair those owned endpoints too. Separate pools, the 20 MiB
PATCH cap, `UPSTREAM_MAX_RESPONSE_BYTES`, and `LEGACY_DOWNLOAD_MAX_CONCURRENT` do not bound
request rate or isolate upstream IP reputation. This limiter reduces that risk without
guaranteeing upstream availability; deployment egress and upstream policy need operational
verification. The 60 requests/60 seconds defaults are provisional, independently tunable
via `RATE_LIMIT_LEGACY_*`, not a measured upstream capacity. Shared NAT users share quota.
Counters are process-local: replicas/workers multiply the possible allowance, restarts
reset it, and fixed windows allow bursts at boundaries. Distributed abuse remains possible.

Observe local 429s in request/access logs and limiter warnings, alongside upstream
429/5xx/timeouts and owned page/summary failures. Denials never enter `proxy_run`, so its
transfer log does not record them. Avoid logging credentials, raw identity keys or query
strings. Prefer tuning the legacy settings over `RATE_LIMIT_ENABLED=false`, which disables
all budgets and is not a legacy-only rollback.

**Failure mode on exhaustion:** `429 Too Many Requests` with a `Retry-After` header naming
the number of seconds until the current window rolls over.

Anonymous callers are keyed on client IP. `X-Forwarded-For` is trusted only when
the ASGI peer is a private/loopback/link-local address (the ingress → `api`
Service hop). A caller who reaches the process directly cannot spoof the quota
by sending `X-Forwarded-For`. **REQUIRES EXTERNAL ACTION:** confirm the
cluster's ingress-nginx ConfigMap has not set `use-forwarded-headers: "true"`
(the default is `false`, which generates `X-Forwarded-For` from the TCP peer
and does not pass through a client-supplied copy). This repo's Ingress objects
do not override that.

## Testing

- **Framework:** pytest with pytest-asyncio
- **MongoDB:** testcontainers for integration tests
- **Temporal:** In-memory test client/worker
- **Fixtures:** `tests/fixtures/` for mocks and test data

```bash
# Run specific test
pytest tests/biosim_verify/test_hdf5_compare.py -v

# Run with specific marker
pytest -m "not integration"
```

## Deploy

**`biosim-gke` is GitOps-managed by Flux CD — merging a commit is the deploy.**
Flux watches this repo's `main` and reconciles `kustomize/overlays/biosim-gke`
into the cluster, so `kubectl apply` there is wrong: it would be reverted on the
next reconcile. Flux's own config lives in `UCHHPC/k8s-config` under
`gke/fluxcd/` (that repo's README covers install and day-2 operations).

The other overlays (`biosim-rke`, `biosim-local`) are **not** under Flux and are
still applied by hand.

> **Never merge an overlay bump before the image is published.** `api` uses
> `strategy: Recreate` at one replica, so pointing the overlay at a tag that
> doesn't exist in GHCR tears down the running pod and takes the API down —
> there is no old pod left serving. (`frontend` is a RollingUpdate at 2
> replicas and degrades gracefully by comparison.) A release build that fails
> its GHCR push leaves the git tag and the GitHub Release in place, so neither
> is proof the image exists — check the release run's job conclusion.

> **Workflow-restructuring releases require a worker drain.** A release that
> changes the activity sequence inside a Temporal workflow (e.g. PR #52, which
> split the monolithic `submit_biosim_simulation_run_activity` into separate
> submit + poll activities inside `OmexSimWorkflow`) will hit
> `NonDeterministicWorkflowError` if a new worker replays an in-flight
> workflow whose history was recorded against the old code. Drain in-flight
> workflows (wait for them to complete, or `tctl workflow terminate`) BEFORE
> rolling out the new `platform-worker` image. The `platform-api` image is
> safe to deploy at any time — its only Temporal contract is `start_workflow`
> / `query_workflow`. See `docs/workflows-architecture.md` → "Deploy
> considerations" for the full rationale and the longer-term `workflow.patched`
> pattern.
>
> Under Flux this needs an extra step, since merging the overlay bump would
> otherwise roll the worker immediately: `flux suspend kustomization
> platform-biosim-gke` before merging, drain, then `flux resume`.

<!-- -->

> **Do not roll the `api` Deployment during an Auth0 outage.** The JWKS cache is
> per-process and in-memory. A running pod serves cached signing keys for up to 24 hours
> after the identity provider becomes unreachable, so an Auth0 incident is usually
> invisible to users. A restarted pod starts with an empty cache and returns
> `503 Authentication temporarily unavailable` on every authenticated request until Auth0
> is reachable again. `strategy: Recreate` (kustomize/base/api.yaml) means a rollout
> terminates the old pod first, so this is not recoverable mid-rollout. Under Flux,
> merging a `biosim-gke` overlay bump is itself a rollout, so hold deploy PRs too.
>
> **The exception:** if you are told an Auth0 signing key has been **compromised**, roll the
> pods immediately. A restart is the only way to purge a revoked key from the stale cache
> before the 24-hour bound expires.

Release steps (run from repo root unless noted). **Two PRs, in this order** —
the release and the deploy are no longer one commit:

1. **Release PR — bump the version only.** The script updates `version.py`,
   `pyproject.toml`, and the `uv.lock` entry, then commits and tags:
   ```bash
   bash backend/scripts/bump-backend.sh patch      # or minor|major|X.Y.Z
   ```
   It tags the **branch** commit, so delete that tag (`git tag -d
   backend-vX.Y.Z`) and re-tag the merge commit on `main` in the next step.
   Open the PR with just the version files; leave the overlays alone for now.
2. **Tag `main` and push** once the PR is merged:
   ```bash
   git tag backend-vX.Y.Z <merge-commit-sha>
   git push origin backend-vX.Y.Z
   ```

   The `release` workflow (`.github/workflows/release.yaml`) builds + pushes
   `platform-{api,worker}:backend-X.Y.Z` to GHCR (**amd64-only**) and cuts a
   GitHub Release. A `plan` job first checks the tag matches `version.py`.

   > **arm64 / `biosim-local`:** CI publishes amd64 only. If you need an arm64
   > layer at `backend-X.Y.Z` (e.g. for the `biosim-local` overlay on Apple
   > silicon), build it locally instead: `bash kustomize/scripts/build_and_push.sh backend X.Y.Z`
   > (multi-arch). Or keep `biosim-local` pinned to an older multi-arch tag.
3. **Verify the images published.** The job conclusion is the gate:
   ```bash
   gh run list --workflow release.yaml --limit 1
   ```
   The GHCR packages are private, so an anonymous manifest check returns 403
   for published and missing tags alike and proves nothing.
4. **Verify the rendered configuration before deploying.** `strategy: Recreate`
   (`kustomize/base/api.yaml`) terminates the old `api` pod before starting the new one, so
   a bad ConfigMap is a full API outage from the moment Flux reconciles the deploy commit
   (`biosim-gke`) or `kubectl apply` returns (hand-applied overlays) — there is no
   rolling-update safety margin. Since P0 #5 an incomplete Auth0 configuration also makes
   the pod refuse to start, which is the intended behaviour but is not something to discover
   in production.

   Run the checklist in `kustomize/README-config.md` → "Before you apply", or at minimum:

   ```bash
   kubectl kustomize kustomize/overlays/<cluster> \
     | grep -E 'AUTH0_DOMAIN|AUTH0_AUDIENCE|AUTH_REQUIRED'
   ```

   Confirm both Auth0 values are present, that `AUTH0_DOMAIN` is a bare hostname with no
   scheme or trailing slash, and that `AUTH_REQUIRED` is absent or `true` for any
   production-tier cluster.
5. **Deploy PR — bump `newTag: backend-X.Y.Z`** in each overlay you're
   deploying:
   - `kustomize/overlays/biosim-gke/kustomization.yaml` — **merging is the
     deploy**; Flux applies it within a minute
   - `kustomize/overlays/biosim-rke/kustomization.yaml` — apply by hand
   - `kustomize/overlays/biosim-local/kustomization.yaml` — apply by hand

   Edit the `newTag` lines directly; `kustomize edit set image` reorders the
   whole block and adds redundant `newName` entries.
6. **Verify.** For biosim-gke:
   ```bash
   export KUBECONFIG=<path-to-kubeconfig>
   flux get kustomizations -A                              # READY=True at the new revision
   kubectl -n biosim-gke rollout status deploy/api
   curl -s https://api.biosim.biosimulations.org/version
   ```
   To skip the poll interval: `flux reconcile kustomization platform-biosim-gke --with-source`.

   For the hand-applied overlays:
   ```bash
   export KUBECONFIG=<path-to-kubeconfig>
   kubectl kustomize kustomize/overlays/biosim-rke | kubectl apply -f -
   kubectl get pods -n biosim-rke
   ```

7. **Verify authentication end to end** (required after any deploy that touches auth
   configuration, and after any change to the Auth0 tenant or its Post-Login Action):

   ```bash
   # a. The startup gate passed
   kubectl logs -n <namespace> deploy/api | grep 'Auth0 configuration validated'

   # b. An invalid token is a 401 -- not a 500, not a 503
   curl -s -o /dev/null -w '%{http_code}\n' \
     -H 'Authorization: Bearer not-a-jwt' https://<API_HOST>/api/v1/me

   # c. A known-roled user's token still carries the roles claim.
   #    Full procedure, including how to obtain the token: auth0/README.md
   #    -> "Smoke check". This is the check that catches a Post-Login Action
   #    that was deleted, disabled, unbound from the Login flow, or deployed
   #    to the wrong tenant -- none of which produce any other symptom until
   #    a user reports a 403.
   curl -s -H "Authorization: Bearer <TOKEN>" https://<API_HOST>/api/v1/me

   # d. No pod is warning about the roles claim
   kubectl logs -n <namespace> deploy/api --since=10m | grep -i 'roles claim'
   #    expect: no output
   ```

   Also confirm no `AUTH_REQUIRED=false` reached a production overlay:

   ```bash
   grep -rn 'AUTH_REQUIRED' kustomize/config/
   ```

**Rollback (biosim-gke):** revert the deploy commit on `main`. Flux restores the
previous state on the next reconcile. For an urgent hand-patch, `flux suspend
kustomization platform-biosim-gke` first — anything applied by hand while it is
running gets reverted, and anything applied while suspended gets reverted at
`flux resume` unless it is also committed.

### Auth0 tenant decision (TODO #6)

**Decision:** <RATIFY the dev tenant | MIGRATE to a production tenant>
**Date:** `<YYYY-MM-DD>`
**Decided by:** `<name>`

**Tenant:** `<AUTH0_DOMAIN>`
**Audience:** `<AUTH0_AUDIENCE>`
**Claim namespaces:** `https://api.biosimulations.org/roles`, `.../email`
(tenant-independent; must match `auth0/actions/post-login.js`)

**Rationale:** `<why>`

<If ratifying, also record:>

- Accepted rate-limit ceiling: <value, from the Auth0 dashboard's tenant settings>
- Accepted consequence: the `dev-` prefix appears in the issuer and in user-visible
  login URLs.
- Revisit trigger: <e.g. "before public launch", "at N registered users">

<If migrating, also record:>

- Cutover date: `<YYYY-MM-DD>`
- Old tenant retained until: `<YYYY-MM-DD>`
- Applications re-created: SPA client id `<CLIENT_ID>`, M2M for the Action,
  M2M for the Management API (TODO #23) if enabled.

## Important Notes

1. **Temporal Required** - Workers need a running Temporal server at `localhost:7233`
2. **MongoDB Required** - Database must be running for most operations
3. **GCS Credentials** - Set `STORAGE_GCS_CREDENTIALS_FILE` for cloud storage
4. **PyCharm Debug Issue** - See `README.md` for uvloop debugging workaround
5. **Version Source of Truth** - `backend/biosim_server/version.py`

## External Services

- **biosimulations.org API** - Submit and poll simulation jobs
- **simdata.api.biosimulations.org** - Retrieve HDF5 simulation outputs
- **api.biosimulators.org** - Get simulator version info
- **Google Cloud Storage** - OMEX file storage
