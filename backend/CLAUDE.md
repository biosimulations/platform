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
poetry install

# Run API server locally
poetry run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000

# Run worker locally
poetry run python -m biosim_server.worker.worker_main

# Run tests
poetry run pytest

# Run tests with coverage
poetry run pytest --cov=biosim_server

# Type checking
poetry run mypy biosim_server

# Single test file
poetry run pytest tests/biosim_runs/test_sim_workflow.py -v
```

## Verification

Run these checks on every changeset before considering work complete (both `biosim_server/` source and the `tests/` suite are in scope):

```bash
# Linting
poetry run ruff check .

# Type checking (strict mode)
poetry run mypy biosim_server tests

# Tests (exclude integration tests that hit external APIs)
poetry run pytest -m "not integration"
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
├── poetry.lock
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

1. **Bump version** in `backend/biosim_server/version.py` and `backend/pyproject.toml`
2. **Update kustomize overlays** — set `newTag` in each overlay's `kustomization.yaml`:
   - `kustomize/overlays/biosim-gke/kustomization.yaml` (amd64)
   - `kustomize/overlays/biosim-rke/kustomization.yaml` (amd64)
   - `kustomize/overlays/biosim-local/kustomization.yaml` (arm64)
3. **Build and push Docker images** (builds api + worker for amd64 + arm64; pushes to `ghcr.io/biosimulations/platform-{api,worker}`):
   ```bash
   bash kustomize/scripts/build_and_push.sh
   ```
4. **Commit, tag, and push**:
   ```bash
   git add -A && git commit -m "bump version to X.Y.Z and deploy"
   git tag vX.Y.Z && git push origin <branch> && git push origin vX.Y.Z
   ```
5. **Apply to cluster** (example for biosim-gke):
   ```bash
   export KUBECONFIG=<path-to-kubeconfig>
   cd kustomize/overlays/biosim-gke
   kubectl kustomize . | kubectl apply -f -
   ```
6. **Verify**:
   ```bash
   kubectl get pods -n biosim-gke
   ```

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
