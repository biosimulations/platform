# Plan: Local Dev Environment + Joint Deploy + Tests

Status: **draft** — open issues at the bottom still need decisions.
Branch: `frontend-backend-int`.

## Context

After Harrison merged the Nuxt 4 webapp into `frontend/` (2026-05-15), the monorepo holds the code for both services but the deploy and local-dev wiring is incomplete. This plan covers three deliverables:

1. A **local dev environment** that runs the full stack without Google Cloud.
2. A **joint deployment** path so frontend and backend release together under one version.
3. **Tests** — backend integration tests against the local-mode backend, and a CI smoke test of the assembled local stack.

## Decisions (already made)

| Decision | Choice |
|---|---|
| Local orchestration | Hybrid: docker-compose for infra (Mongo + Temporal + optional minio); backend and frontend run native via poetry/pnpm for fast HMR. |
| External APIs in local dev | Call the real public biosimulations.org / simdata / biosimulators APIs. The OMEX flow ferries bytes through the backend (`biosim_service.py:84` uses multipart upload), so local filesystem storage works fine. |
| Frontend packaging | Separate container running the Nuxt Nitro node server. New `platform-frontend` image with its own Deployment + Service. |
| Test scope | Backend integration tests against the local-mode backend + CI smoke test of the assembled local stack. (No frontend unit/E2E tests in this plan.) |
| Local file storage | Both: `FileServiceLocal` (filesystem under `./local_cache/`) is default; opt into `FileServiceMinio` via env var. |
| Joint deploy meaning | Shared monorepo version; single `git tag vX.Y.Z` rebuilds all three images. |
| CI smoke trigger | Every PR (all paths). |

## Existing building blocks (no work needed)

- `backend/biosim_server/common/storage/file_service.py` — `FileService` ABC.
- `backend/tests/fixtures/file_service_local.py` — complete filesystem `FileService` impl (to be promoted).
- `backend/biosim_server/biosim_runs/biosim_service.py` — `BiosimService` abstraction; URLs already configurable via `config.py:38-39`.
- `backend/tests/fixtures/database_fixtures.py` + `temporal_fixtures.py` — testcontainers Mongo + in-memory Temporal.
- `backend/biosim_server/api/main.py:45` — CORS already permits `http://localhost:4200`, so the frontend can call the backend directly with no reverse proxy.
- `backend/Dockerfile.api`, `backend/Dockerfile.worker` — exist.
- `kustomize/base/` — has `api.yaml`, `worker.yaml`, `mongodb.yaml`.

## Pre-work — code prep

Small, independent, low-risk changes that unblock the rest.

1. **Promote `FileServiceLocal`** out of `tests/fixtures/` into `backend/biosim_server/common/storage/file_service_local.py` so it's a first-class implementation.
2. **Add a storage-backend selector** in `backend/biosim_server/config.py` (e.g., `STORAGE_BACKEND=gcs|local|minio`, default `gcs`). Wire `backend/biosim_server/dependencies.py:74` to construct the right `FileService` based on the setting.
3. **Implement `FileServiceMinio`** in `backend/biosim_server/common/storage/file_service_minio.py` using `aioboto3` (S3-compatible) against `STORAGE_ENDPOINT_URL` (already in config). Most of the GCS code can be adapted.
4. **Confirm `BiosimService` URL config** — they're already configurable via `BIOSIMULATIONS_API_BASE_URL` and `SIMDATA_API_BASE_URL`. Just document defaults.
5. **Fix the 3 hardcoded URLs in the frontend** to use `useRuntimeConfig().public.api_url`:
   - `frontend/app/pages/simulations/run.vue:123`
   - `frontend/app/pages/simulations/run.vue:185`
   - `frontend/app/pages/simulations/check-status/[processing_id].vue:26`
6. **Fix `frontend/app/pages/biosim-db.vue:209`** — it reads `runtimeConfig.public.api_base` which isn't declared in `nuxt.config.ts`. Either declare it (it's a different base — the public biosimulations.org API, not the platform backend) or rename for clarity.

## Phase 1 — Local stack for frontend dev

**Goal:** A developer runs `docker compose up -d` for infra, then `poetry run ...` (backend api + worker) and `pnpm dev` (frontend) natively. Frontend at `http://localhost:4200` talks to backend at `http://localhost:8000`. Real biosimulations.org runs the actual simulations.

- **`compose.yaml`** at repo root. Services:
  - `mongo` — `mongo:7` on `27017`, named volume
  - `temporal` — `temporalio/temporal` running `server start-dev` (single-binary dev mode, no separate Postgres) on `7233`
  - `minio` — `minio/minio` on `9000` (API) + `9001` (console), named volume. Off by default via compose `profile: ["minio"]` — enabled with `--profile minio`.
- **`.env.example`** at repo root:
  ```
  STORAGE_BACKEND=local
  STORAGE_LOCAL_CACHE_DIR=./local_cache
  TEMPORAL_SERVICE_URL=localhost:7233
  MONGODB_URI=mongodb://localhost:27017
  # frontend
  BASE_URL=http://localhost:4200
  API_URL=http://localhost:8000
  ```
- **`scripts/dev-up.sh` / `scripts/dev-down.sh`** thin wrappers (start compose, print the two native commands the developer still has to run).
- **Docs:** add a "Local development" section to root `README.md` and cross-link from `backend/CLAUDE.md` and `frontend/CLAUDE.md`.

## Phase 2 — Joint deployment

**Goal:** Single `git tag vX.Y.Z` builds and ships `platform-api`, `platform-worker`, `platform-frontend` at the same version. One overlay update per release.

- **`frontend/Dockerfile`** — multi-stage:
  - Stage 1 (`node:22-alpine`): `pnpm install --frozen-lockfile && pnpm build`
  - Stage 2 (`node:22-alpine`): copy `.output/`, `CMD ["node", ".output/server/index.mjs"]`, expose `3000`
- **`kustomize/base/frontend.yaml`** — Deployment + Service for `platform-frontend`. Env: `BASE_URL`, `API_URL` from ConfigMap.
- **`kustomize/base/kustomization.yaml`** — add `frontend.yaml` to the resource list.
- **Ingress routing** — see open issue #1.
- **Version coupling** — single source of truth at `backend/biosim_server/version.py`. See open issue #2.
- **`kustomize/scripts/build_and_push.sh`** — extend to build/push `platform-frontend` for amd64 + arm64. Version still read from `backend/biosim_server/version.py`.
- **Overlays** — each `kustomization.yaml` (`biosim-gke`, `biosim-rke`, `biosim-local`) gets a new `images:` entry for `platform-frontend` with the matching `<arch>_<version>` tag.
- **Consolidate frontend CI** — move `frontend/.github/workflows/ci.yml` to repo root `.github/workflows/frontend-ci.yaml` with `paths: ['frontend/**']`. Delete the in-tree version.

## Phase 3 — Tests

### Backend integration tests against local-mode backend
- Add `backend/tests/integration_local/` with tests that:
  - Use `FileServiceLocal` (no GCS).
  - Use real testcontainers MongoDB + in-memory Temporal (already wired via existing fixtures).
  - Hit the FastAPI app via `httpx.AsyncClient` for the full request lifecycle of `/simulations/run` and `/compatibility/check`.
  - **Mock `BiosimService`** so tests don't actually call biosimulations.org (keeps tests fast and deterministic — this is a deliberate split from "real APIs in local dev").
- Mark with `@pytest.mark.integration_local` so they can be selected separately from the existing `@pytest.mark.integration` (which already means "hits external APIs").

### CI smoke test on every PR
- New workflow `.github/workflows/smoke.yaml`:
  - Trigger: `on: pull_request` (all paths).
  - Steps: `docker compose up -d mongo temporal`, install poetry + pnpm, build backend + frontend, start them in the background, wait for `:8000/version` and `:4200/`, hit a handful of endpoints (e.g., `GET /version`, `GET /docs`, frontend root render).
  - **Do not run a real simulation** — biosimulations.org submission is minutes-long. Test only the wiring (frontend boots, backend boots, CORS works, runtime config reaches the frontend).
  - Budget: aim for under 5 minutes.

## Open issues / decisions needed

1. **Ingress routing in prod** — `/api/*` rewrite to the backend Service, or separate `api.biosim.*` subdomain? The current backend is at `biosim.biosimulations.org/...` (no `/api` prefix), so a subdomain split is the lower-disruption option.
2. **Version coupling mechanism** — build-time read from `backend/biosim_server/version.py` (auto, no drift) vs. `scripts/bump-version.sh` (explicit, simpler)?
3. **Temporal image** — `temporalio/temporal server start-dev` (single binary, no UI by default) vs. `temporalio/auto-setup` (full stack with UI on 8233)? UI is useful for debugging workflows.
4. **`frontend/package.json` `name`** — change `"website"` → `"platform-frontend"` while we're touching it?
5. **Phasing** — implement all three phases in order on this `frontend-backend-int` branch, or split into separate PRs (Phase 1, then 2, then 3)? Phase 1 is the most user-visible win for unblocking Harrison's webapp dev; the rest can follow.
