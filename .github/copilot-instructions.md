# Copilot instructions for `biosimulations/platform`

## Repository shape and architecture

This is a monorepo for a Biosimulations platform deployed to Kubernetes:

- `backend/` is a Python 3.13 FastAPI service plus Temporal workers. The API starts workflows; workers execute simulator, storage, and comparison activities. MongoDB stores metadata and run state, while GCS, local storage, or MinIO stores OMEX files and results.
- `frontend/` is a Nuxt 4/Vue 3 SSR application. It submits and polls simulation workflows through the backend, and separately reads the public biosimulations.org project database API.
- `compose.yaml` runs development infrastructure only (MongoDB and Temporal, with optional MinIO). Run the backend API, Temporal worker, and frontend natively for reload/HMR.
- `kustomize/` contains the shared Kubernetes base and cluster overlays. The three production processes are the API, Temporal worker, and Nuxt Nitro frontend.

The normal local flow is:

```bash
scripts/dev-up.sh              # MongoDB + Temporal
# optionally: scripts/dev-up.sh --minio
cd backend && uv run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000 --reload
cd backend && uv run python -m biosim_server.worker.worker_main
cd frontend && npm run dev     # http://localhost:4200
```

The backend needs MongoDB and Temporal (`localhost:27017` and `localhost:7233`) for most runtime and integration behavior. Local development defaults to filesystem storage; use the MinIO profile and `STORAGE_BACKEND=minio` when testing S3-compatible storage.

## Build, test, and lint commands

Run commands from the service directory unless noted.

### Backend

```bash
cd backend
uv sync
uv run pytest                         # full pytest suite
uv run pytest -m "not integration"    # avoid tests calling external APIs
uv run pytest tests/biosim_verify/test_hdf5_compare.py -v  # one file
uv run pytest tests/path/to/test_file.py::test_name -v      # one test
uv run ruff check .
uv run mypy biosim_server tests
uv run python -m scripts.generate_openapi  # after route/model changes
```

Tests use `pytest-asyncio`; integration fixtures can use testcontainers MongoDB and an in-memory Temporal client. Tests marked `integration` may call external APIs; `integration_local` is intended for the local-stack path without external network calls.

### Frontend

Use Node 22 or newer locally. The current CI workflow runs Node 24. npm and `frontend/package-lock.json` are authoritative.

```bash
cd frontend
npm ci
npm run dev
npm run lint
npm run typecheck
npm run test:auth                    # auth token destination tests
npm run build
npm run preview
node --experimental-strip-types --test tests/api-auth.test.mjs  # one auth test file
```

There is no general frontend unit/E2E suite; validate UI changes with the dev server. `npm run build` can fail on macOS/arm64 because Nuxt Image's optional `ipx` dependency may be skipped; use `npm run dev` for local iteration and rely on Linux CI for the production build when this platform-specific issue occurs.

### Cross-service checks

The root CI runs backend tests, frontend lint/typecheck/auth tests, and the joint smoke workflow. To reproduce the smoke setup locally, build the frontend, start MongoDB and Temporal with `docker compose up -d mongo temporal`, then start the backend API and Nitro server and check `/version`, `/docs`, CORS, and the frontend root.

## Conventions and integration boundaries

- Keep backend I/O asynchronous: FastAPI handlers, Motor MongoDB calls, HTTP clients, storage, and Temporal interactions are async.
- Put new backend routes/models in the appropriate domain package and regenerate the committed OpenAPI artifact with `uv run python -m scripts.generate_openapi`; do not edit the generated spec by hand. The generator enables the RBAC demo route so output is stable regardless of shell environment.
- Temporal workflow code must remain deterministic. Workflow restructuring can make in-flight histories incompatible with a newly deployed worker; drain affected workflows before rolling out a worker image. API-only changes do not require a worker drain.
- Backend service construction is centralized in `biosim_server/dependencies.py`; reuse its dependency getters for storage, databases, the Biosimulations client, and Temporal rather than creating process-local services in route modules.
- Backend authentication uses OAuth access tokens, not OIDC ID tokens. Authentication and authorization behavior is controlled by Auth0 settings; fail-closed behavior for missing claims is intentional. `/simulations/run` remains anonymous-capable because the current frontend does not send a bearer token, while verification endpoints require authentication.
- Frontend runtime configuration must use Nuxt's naming conventions: `NUXT_PUBLIC_*` for browser/server public values and `NUXT_*` for server-only values. SSR calls should prefer the internal backend URL (`NUXT_API_URL`/the configured server-only value); browser calls use the public API URL. Do not use bare `API_URL` for runtime-only container overrides.
- Nuxt pages are file-based routes under `frontend/app/pages`. Prefer `useFetch`/`useAsyncData` for SSR-friendly data fetching and `$fetch` for client-only event handlers. Direct loads and refreshes exercise SSR, so test affected routes directly, not only through in-app navigation.
- Use Nuxt UI/Tailwind tokens and existing Iconify/Lottie/AOS conventions instead of introducing a parallel styling or animation system. Zod is the existing form-validation library.
- Keep service versions independent: backend source version is `backend/biosim_server/version.py` plus `backend/pyproject.toml`; frontend version is `frontend/package.json`. Image tags are `backend-X.Y.Z` and `frontend-X.Y.Z`.
- `kustomize/overlays/biosim-gke` is Flux-managed: merging an overlay image-tag change on `main` deploys it. Do not use `kubectl apply` there. Publish and verify the image first, then make the deploy PR. `biosim-rke` and `biosim-local` are hand-applied.
- Secrets belong in the overlay `secrets.dat` workflow and sealed-secret outputs; never commit plaintext `secrets.dat` values. Deployment ConfigMaps carry non-secret runtime settings, while credentials use sealed secrets.

## CI and release facts

- `.github/workflows/ci.yaml` installs the frozen backend lockfile and runs pytest.
- `.github/workflows/frontend-ci.yaml` installs from `frontend/package-lock.json`, then runs lint, typecheck, and `test:auth`.
- `.github/workflows/smoke.yaml` boots MongoDB and Temporal, builds the frontend, starts both services, and verifies wiring only; it deliberately does not submit a real simulation.
- `.github/workflows/release.yaml` publishes images but does not deploy them. Version tags are `backend-vX.Y.Z`, `frontend-vX.Y.Z`, or coordinated `vX.Y.Z` from `main`. The local `kustomize/scripts/build_and_push.sh` is the multi-arch fallback.
