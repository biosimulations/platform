# CLAUDE.md - Platform Backend Development Guide

This guide covers the backend service. For monorepo orientation (frontend, kustomize, deployment), see the root `CLAUDE.md`. For the integrated local-dev workflow (Mongo + Temporal in containers, backend + frontend native), see the **Local development** section of the root `README.md`.

All commands below assume the working directory is `backend/` (i.e., `cd backend` from the repo root first).

## Project Overview

The platform backend is a distributed microservices application for biosimulation verification and comparison. It runs biological simulations across multiple simulators (AMICI, COPASI, PySCES, Tellurium, VCell) and compares outputs to verify model correctness.

**Version:** 0.4.0
**Python:** 3.13
**Production URL:** https://biosim.biosimulations.org/docs

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
```

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

```
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

```
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
|----------|--------|---------|
| `/compatibility/check` | POST | Check OMEX archive compatibility with simulators |
| `/simulations/run` | POST | Run simulations for an OMEX archive across selected simulators |
| `/simulations/runs` | POST | List simulation runs (owner-scoped, paginated, sortable, filterable) |
| `/simulations/{processing_id}` | GET | Get status of a simulation run |
| `/verify/omex` | POST | Verify OMEX file across simulators |
| `/verify/{workflow_id}` | GET | Get verification results |
| `/verify/runs` | POST | Compare existing biosimulation runs |
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
|---|---|---|
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
|---|---|---|
| `BIOSIMULATIONS_API_BASE_URL` | `https://api.biosimulations.org` | `BiosimServiceRest` — submit and poll simulation jobs |
| `SIMDATA_API_BASE_URL` | `https://simdata.api.biosimulations.org` | `BiosimServiceRest` — fetch HDF5 outputs |
| `BIOSIMULATORS_API_BASE_URL` | `https://api.biosimulators.org` | Simulator version metadata |

### Infrastructure

| Variable | Default | Purpose |
|---|---|---|
| `TEMPORAL_SERVICE_URL` | `localhost:7233` | Temporal workflow server |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGODB_DATABASE` | `biosimulations` | MongoDB database name |

### CORS

| Variable | Default | Purpose |
|---|---|---|
| `CORS_EXTRA_ORIGINS` | _empty_ | Comma-separated list of additional CORS origins appended to the built-in allowlist in `api/main.py`. **Required** for every deployment so the deployed frontend host (e.g. `https://biosim.biosimulations.org`) is allowed. The built-in list only covers local-dev loopbacks and cross-org trusted services — deploy-specific URLs are not hardcoded by design. |

### Authentication (Auth0)

All values are **non-secret** and belong in each overlay's `api.env` ConfigMap, never in a
sealed secret. The Management API credentials at the bottom are the exception — they are
credentials, are unset in every overlay today, and are tracked separately (TODO #23).

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_REQUIRED` | `true` | Startup gate. When true, the API refuses to start unless the Auth0 configuration below is complete and well-formed. Set to `false` only to run deliberately without an identity provider. |
| `AUTH0_DOMAIN` | _empty_ | **Bare hostname** of the Auth0 tenant, e.g. `tenant.us.auth0.com`. The issuer (`https://{domain}/`) and JWKS URL (`https://{domain}/.well-known/jwks.json`) are derived from it. A URL or a trailing slash here is a configuration error and is rejected at startup. |
| `AUTH0_AUDIENCE` | _empty_ | The API identifier tokens must carry in `aud`. |
| `AUTH0_ISSUER` | _derived_ | Explicit issuer override. Needed only for a non-Auth0 OIDC provider whose issuer does not follow Auth0's convention (the Keycloak realm used in tests). Must be set together with `AUTH0_JWKS_URI`. |
| `AUTH0_JWKS_URI` | _derived_ | Explicit JWKS URL override. See above. |
| `AUTH0_ROLES_CLAIM` | `https://api.biosimulations.org/roles` | Namespaced claim carrying role names. **Stamped by the Auth0 Post-Login Action** (`auth0/actions/post-login.js`) — without that Action the claim never arrives, every `require_roles` endpoint returns 403, and no admin exists. |
| `AUTH0_EMAIL_CLAIM` | `https://api.biosimulations.org/email` | Namespaced claim carrying the user's email. Also stamped by the Action; `get_current_user` falls back to a plain `email` claim for providers that include one. |
| `AUTH0_EMAIL_VERIFIED_CLAIM` | `https://api.biosimulations.org/email_verified` | Namespaced claim carrying whether that email is verified. Stamped by the same Action. Authorization treats a missing claim as unverified (fail closed). Override only if the Action uses a different namespace. |
| `AUTH0_MANAGEMENT_CLIENT_ID` | _empty_ | M2M credentials for `PATCH`/`DELETE /api/v1/me`. **Secret** — sealed-secret path only. Unset in every cluster today, so those endpoints return 503. |
| `AUTH0_MANAGEMENT_CLIENT_SECRET` | _empty_ | See above. |

**Per-cluster configuration:**

| Overlay | Auth0 configuration | Notes |
|---|---|---|
| `biosim-gke` | tenant + audience set | Production tier. |
| `biosim-rke` | tenant + audience set | Prod tier; also backs the `biosim-rke-frontend-dev` preview frontend. |
| `biosim-local` | _record the Step 2 decision here_ | |

**Failure modes:**

| Condition | Result |
|---|---|
| Missing/malformed config, `AUTH_REQUIRED=true` | Pod refuses to start; `kubectl logs` names the variable. |
| Missing/malformed config, `AUTH_REQUIRED=false` | Pod starts; endpoints behind `get_current_user` return **503**. |
| Auth0 unreachable, warm JWKS cache | Tokens still validate for up to 24 h; a WARN is logged per request. |
| Auth0 unreachable, cold JWKS cache | **503** with `Retry-After: 10`. |
| Invalid, expired, or wrongly-audienced token | **401**. |
| Valid token, missing role | **403**. |
| Management API (`PATCH`/`DELETE /api/v1/me`) rate-limited (429) through all retries | **503** with `Retry-After`. |
| Management API 5xx or transport failure through all retries | **502**. |

**Anonymous `POST /simulations/run` (P1 #9 Option B).** This endpoint stays
reachable without a bearer token, paired with the workflow rate limiter
(P1 #10). The production frontend currently submits runs without an
`Authorization` header, so requiring auth here would break the UI. Authenticated
API clients still have their token's `sub` persisted as `owner_sub`. Anonymous
and frontend-originated runs persist `owner_sub = NULL` until the frontend
attaches tokens. This is an explicit product decision, not an omission.
Revisit when the frontend sends bearer tokens (that is the gate for Option A).

The frontend does **not** call `POST /verify/omex` or `POST /verify/runs`;
those two endpoints now require authentication. External/Swagger consumers of
`/verify/*` must send a bearer token. No inventory of those consumers exists
in this repository — treat that as an operational follow-up before advertising
the gated contract as a breaking API change.

**POST /projects/reindex (P1 #15): keep-and-harden.** The endpoint stays. It is
the only HTTP path that can rebuild the project search index without cluster
access. It remains disabled by default (`PROJECT_REINDEX_TOKEN` unset → 503)
and compares the bearer token with `secrets.compare_digest` on UTF-8 bytes.

### Rate Limiting

All values are **non-secret** and belong in each overlay's `api.env` ConfigMap. The limiter
(`biosim_server/common/ratelimit.py`) is **per-pod, not global** -- `api` runs 3 replicas
(`kustomize/base/api.yaml:8`) and there is no Redis or shared cache in this stack. Each pod
enforces the configured number independently; the effective global ceiling is up to
`replica_count` (currently 3) times the configured per-pod value if traffic distributes
evenly. To target a global ceiling `G`, configure the per-pod value as `G / 3`.

| Variable | Default | Purpose |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | Kill-switch. Set `false` to disable rate limiting entirely without a code change -- an incident lever, mirroring `AUTH_REQUIRED`. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window size in seconds. |
| `RATE_LIMIT_AUTHENTICATED_PER_WINDOW` | `30` | Per-pod requests per window for a caller identified by a verified token's `sub`. |
| `RATE_LIMIT_ANONYMOUS_PER_WINDOW` | `5` | Per-pod requests per window for a caller identified only by client IP. |

Protects `POST /verify/omex`, `POST /verify/runs`, and `POST /simulations/run` -- the three
endpoints that start a Temporal workflow. All three share ONE budget per caller identity, not
three separate ones.

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

Backend release steps (run from repo root unless noted):

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

<!-- -->

> **Do not roll the `api` Deployment during an Auth0 outage.** The JWKS cache is
> per-process and in-memory. A running pod serves cached signing keys for up to 24 hours
> after the identity provider becomes unreachable, so an Auth0 incident is usually
> invisible to users. A restarted pod starts with an empty cache and returns
> `503 Authentication temporarily unavailable` on every authenticated request until Auth0
> is reachable again. `strategy: Recreate` (kustomize/base/api.yaml) means a rollout
> terminates the old pod first, so this is not recoverable mid-rollout.
>
> **The exception:** if you are told an Auth0 signing key has been **compromised**, roll the
> pods immediately. A restart is the only way to purge a revoked key from the stale cache
> before the 24-hour bound expires.

1. **Bump version + tag** (bumps `version.py` + `pyproject.toml`, commits, tags `backend-vX.Y.Z`):
   ```bash
   bash backend/scripts/bump-backend.sh patch      # or minor|major|X.Y.Z
   ```
2. **Update kustomize overlays** — set `newTag: backend-X.Y.Z` in each overlay's `kustomization.yaml`:
   - `kustomize/overlays/biosim-gke/kustomization.yaml`
   - `kustomize/overlays/biosim-rke/kustomization.yaml`
   - `kustomize/overlays/biosim-local/kustomization.yaml` (see arm64 note below)

   Commit these alongside the bump (the script commits only the version files).
3. **Push the branch + tag** to build and publish images via CI:
   ```bash
   git push origin <branch>
   git push origin backend-vX.Y.Z
   ```
   The `release` workflow (`.github/workflows/release.yaml`) builds + pushes
   `platform-{api,worker}:backend-X.Y.Z` to GHCR (**amd64-only**) and cuts a
   GitHub Release. Watch it under the repo's Actions tab.

   > **arm64 / `biosim-local`:** CI publishes amd64 only. If you need an arm64
   > layer at `backend-X.Y.Z` (e.g. for the `biosim-local` overlay on Apple
   > silicon), build it locally instead: `bash kustomize/scripts/build_and_push.sh backend X.Y.Z`
   > (multi-arch). Or keep `biosim-local` pinned to an older multi-arch tag.

4. **Verify the rendered configuration before applying.** `strategy: Recreate`
   (`kustomize/base/api.yaml`) terminates the old `api` pod before starting the new one, so
   a bad ConfigMap is a full API outage from the moment `kubectl apply` returns — there is
   no rolling-update safety margin. Since P0 #5 an incomplete Auth0 configuration also makes
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

5. **Apply to cluster** (example for biosim-gke) once images are published:
   ```bash
   export KUBECONFIG=<path-to-kubeconfig>
   cd kustomize/overlays/biosim-gke
   kubectl kustomize . | kubectl apply -f -
   ```
6. **Verify**:
   ```bash
   kubectl get pods -n biosim-gke
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

#### Auth0 tenant decision (TODO #6)

**Decision:** <RATIFY the dev tenant | MIGRATE to a production tenant>
**Date:** <YYYY-MM-DD>
**Decided by:** <name>

**Tenant:** `<AUTH0_DOMAIN>`
**Audience:** `<AUTH0_AUDIENCE>`
**Claim namespaces:** `https://api.biosimulations.org/roles`, `.../email`
(tenant-independent; must match `auth0/actions/post-login.js`)

**Rationale:** <why>

<If ratifying, also record:>

- Accepted rate-limit ceiling: <value, from the Auth0 dashboard's tenant settings>
- Accepted consequence: the `dev-` prefix appears in the issuer and in user-visible
  login URLs.
- Revisit trigger: <e.g. "before public launch", "at N registered users">

<If migrating, also record:>

- Cutover date: <YYYY-MM-DD>
- Old tenant retained until: <YYYY-MM-DD>
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
