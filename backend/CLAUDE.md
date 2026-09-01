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
| `/projects/{id}/summary` | GET | Project detail envelope (passthrough to biosimulations.org) |
| `/projects/{id}/detail` | GET | Summary + the run's files/specification (+ log via `?include=log`) |
| `/runs/{id}/summary` | GET | Run summary (passthrough) |
| `/files/{id}` | GET | Files in a run's archive (passthrough) |
| `/specifications/{id}` | GET | SED-ML specification (passthrough) |
| `/logs/{id}` | GET | Execution log (passthrough) |
| `/results/{id}/{outputId}` | GET | Results for one output (passthrough, lazy) |
| `/ontologies/KISAO/{id}` | GET | KISAO algorithm term (passthrough, cached) |
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
4. **Deploy PR — bump `newTag: backend-X.Y.Z`** in each overlay you're
   deploying:
   - `kustomize/overlays/biosim-gke/kustomization.yaml` — **merging is the
     deploy**; Flux applies it within a minute
   - `kustomize/overlays/biosim-rke/kustomization.yaml` — apply by hand
   - `kustomize/overlays/biosim-local/kustomization.yaml` — apply by hand

   Edit the `newTag` lines directly; `kustomize edit set image` reorders the
   whole block and adds redundant `newName` entries.
5. **Verify.** For biosim-gke:
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

**Rollback (biosim-gke):** revert the deploy commit on `main`. Flux restores the
previous state on the next reconcile. For an urgent hand-patch, `flux suspend
kustomization platform-biosim-gke` first — anything applied by hand while it is
running gets reverted, and anything applied while suspended gets reverted at
`flux resume` unless it is also committed.

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
