# Plan: Local Dev Environment + Joint Deploy + Tests

Status: **ready** — all open issues resolved 2026-05-15. Implementation proceeds as three separate PRs off `frontend-backend-int` (Pre-work + Phase 1, then Phase 2, then Phase 3).
Branch: `frontend-backend-int`.

## Context

After Harrison merged the Nuxt 4 webapp into `frontend/` (2026-05-15), the monorepo holds the code for both services but the deploy and local-dev wiring is incomplete. This plan covers three deliverables:

1. A **local dev environment** that runs the full stack without Google Cloud.
2. A **joint deployment** path so frontend and backend release together under one version.
3. **Tests** — backend integration tests against the local-mode backend, and a CI smoke test of the assembled local stack.

## Decisions (already made)

| Decision | Choice |
|---|---|
| Local orchestration | Hybrid: docker-compose for infra (Mongo + Temporal + optional minio); backend and frontend run native via poetry/npm for fast HMR. |
| External APIs in local dev | Call the real public biosimulations.org / simdata / biosimulators APIs. The OMEX flow ferries bytes through the backend (`biosim_service.py:84` uses multipart upload), so local filesystem storage works fine. |
| Frontend packaging | Separate container running the Nuxt Nitro node server. New `platform-frontend` image with its own Deployment + Service. |
| Test scope | Backend integration tests against the local-mode backend + CI smoke test of the assembled local stack. (No frontend unit/E2E tests in this plan.) |
| Local file storage | Both: `FileServiceLocal` (filesystem under `./local_cache/`) is default; opt into `FileServiceMinio` via env var. |
| Joint deploy meaning | Independent versions per service (`backend-vX.Y.Z`, `frontend-vX.Y.Z`); coordinated `vX.Y.Z` tags from `main` rebuild all three images together. |
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
7. **Rename `frontend/package.json` `name`** from `"website"` to `"platform-frontend"` to match the directory and image name.
8. **Add a `version` field to `frontend/package.json`** (none exists today). Seed at `0.1.0` — fresh version stream for the monorepo-aligned frontend. From there, `frontend/scripts/bump-frontend.sh` advances the patch.
9. **Add `frontend/scripts/bump-frontend.sh`** — bumps patch in `package.json`, commits with message like `Bump frontend to X.Y.Z`, and tags `frontend-vX.Y.Z` on the current branch.

## Phase 1 — Local stack for frontend dev

**Goal:** A developer runs `docker compose up -d` for infra, then `poetry run ...` (backend api + worker) and `npm run dev` (frontend) natively. Frontend at `http://localhost:4200` talks to backend at `http://localhost:8000`. Real biosimulations.org runs the actual simulations.

- **`compose.yaml`** at repo root. Services:
  - `mongo` — `mongo:7` on `27017`, named volume
  - `temporal` — `temporalio/temporal` running `server start-dev` (single-binary dev mode, no separate Postgres) on `7233`. No Web UI in this mode; if needed later we can swap to `temporalio/auto-setup`.
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

**Goal:** Frontend and backend share one Ingress and one release pipeline. Each service has an independent version stream (`frontend-vX.Y.Z`, `backend-vX.Y.Z`) so GUI hotfixes can ship without touching the backend; coordinated `vX.Y.Z` tags from `main` rebuild all three images together. Overlays cherry-pick the per-service tag they want deployed.

### Frontend Kubernetes deployment design

**Process model:** single container running Nuxt's Nitro Node server (`node .output/server/index.mjs`) on port `3000`. Nitro serves both SSR HTML and static assets from `.output/public/` in one process — no nginx sidecar, no separate static-content server. The container is stateless and horizontally scalable.

- **`frontend/Dockerfile`** — runtime-only image; **build happens outside Docker** (in CI / on the dev's machine via `npm run build`), and the image just copies the prebuilt artifact and runs it. Mirrors the Angular SSR pattern Harrison uses elsewhere.
  - **Nuxt-specific note:** Nitro emits `.output/` containing the server entrypoint, all static assets, and *bundled* server dependencies under `.output/server/node_modules/`. The output directory is self-contained — no separate `npm ci --omit=dev` step needed (unlike the Angular pattern where prod `node_modules` are copied alongside `dist/`).
  - Dockerfile:
    ```dockerfile
    FROM node:22-alpine
    WORKDIR /app
    COPY .output ./.output
    ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
    EXPOSE 3000
    USER node
    CMD ["node", ".output/server/index.mjs"]
    ```
  - `frontend/.dockerignore` should exclude `node_modules/`, `.nuxt/`, source dirs, etc. — only `.output/` needs to be in the build context.
  - `kustomize/scripts/build_and_push.sh frontend` runs `cd frontend && npm ci && npm run build` **before** `docker build`, so the build is reproducible from a clean checkout and not dependent on the dev's local state.
- **`kustomize/base/frontend.yaml`** — Deployment + Service:
  - **Deployment** `frontend`, default `replicas: 2` (override per overlay). Image `ghcr.io/biosimulations/platform-frontend:latest` (pending registry decision — see follow-up). `imagePullPolicy: Always`. `containerPort: 3000`.
  - **Probes:** `readinessProbe` and `livenessProbe` both HTTP GET `/` on `3000`. (Optionally add a `server/api/healthz.ts` Nitro route returning 200 to avoid full SSR on probes — defer unless probe latency becomes an issue.)
  - **Env from ConfigMap (`frontend-config`):** `BASE_URL`, `API_URL` (browser-facing public backend URL). Optional `NITRO_API_URL` for SSR-time internal calls — see below.
  - **Resources:** request `100m` CPU / `256Mi` mem, limit `500m` / `512Mi` (Nitro SSR is memory-bound; tune after observing).
  - **Service** `frontend`, `port: 80 → targetPort: 3000`, `selector: app=frontend`.
- **`kustomize/base/kustomization.yaml`** — add `frontend.yaml` to the resource list.
- **`kustomize/config/<cluster>/`** — add a `frontend-config` ConfigMap per overlay with `BASE_URL` and `API_URL` pointing at the right public URLs for that cluster.
- **Ingress** — one Ingress resource per overlay with two host rules (subdomain split). For prod that's `biosim.biosimulations.org` → `frontend` Service and `api.biosim.biosimulations.org` → `api` Service. Both hosts get TLS via the existing cert-manager + letsencrypt-prod issuer. The existing `nginx.ingress.kubernetes.io/proxy-body-size: 20m` annotation stays on the api host rule (for OMEX uploads).
- **SSR ↔ backend traffic (server-side):** Nuxt distinguishes `runtimeConfig` (server-only) from `runtimeConfig.public` (shared with browser). Have the frontend resolve a separate server-side API URL — pointed at the cluster-internal `http://api:8000` — so SSR fetches skip the public ingress, TLS, and DNS hop. Browser-side calls still use the public `API_URL`. Wire-up: declare `runtimeConfig.apiUrl` in `nuxt.config.ts`, bound to env `API_URL_INTERNAL`; ConfigMap supplies `API_URL_INTERNAL=http://api:8000`. Where a page does `useRuntimeConfig().public.api_url` for both render paths, switch the SSR-eligible fetches to prefer the server-side value via `import.meta.server`.
- **Static asset caching (optional, defer):** Nuxt emits content-hashed files under `/_nuxt/*` and `/_fonts/*` — safely immutable. If hot-path perf matters later, add an nginx ingress annotation snippet setting `Cache-Control: public, max-age=31536000, immutable` on `^/_nuxt/`. Not in scope for this phase.
- **Versioning model** — services version **independently**:
  - Backend version in `backend/biosim_server/version.py`. Backend git tags: `backend-vX.Y.Z`. Image tags: `backend-X.Y.Z` (rebuilds `platform-api` + `platform-worker`).
  - Frontend version in `frontend/package.json` (currently missing — add it, seed at `0.1.0` as a fresh monorepo-aligned version stream). Frontend git tags: `frontend-vX.Y.Z`. Image tags: `frontend-X.Y.Z`.
  - **Coordinated full releases** use plain `vX.Y.Z` tags, **only on `main`**. A `vX.Y.Z` tag is a coordinated bump: both `version.py` and `package.json` are set to the same `X.Y.Z`, all three images rebuild at that version.
  - **Branch deploys are allowed for the frontend.** A helper script `frontend/scripts/bump-frontend.sh` bumps the patch in `package.json`, commits, and tags `frontend-vX.Y.Z` on whatever branch is current. Build + push consumes that tag. (No CI guard requiring main.)
  - Kustomize overlays' `images:` entries are independent per service — overlays cherry-pick whichever version of each they want.
- **Frontend image registry** — no overlay currently references the frontend image; the root `CLAUDE.md` documents `ghcr.io/biosimulations/platform-frontend` as the intended name (not yet built), and Harrison's prior standalone-repo builds published to `docker.io/biosimulations/frontend`. **Open follow-up:** pick the registry before publishing the first monorepo build (GHCR for consistency with `platform-api` / `platform-worker`, or dockerhub if there's tooling that needs it). Update `build_and_push.sh` and each overlay's `images:` entry to match.
- **`kustomize/scripts/build_and_push.sh`** — parameterize: `build_and_push.sh backend|frontend|all [version]`. Each subcommand reads its own service's version file by default. `all` is used by a coordinated `vX.Y.Z` release; it requires the version argument and writes both files.
- **Overlays** — each `kustomization.yaml` (`biosim-gke`, `biosim-rke`, `biosim-local`) lists `platform-api`, `platform-worker`, `platform-frontend` independently with whatever per-service tag is deployed. Today they all use the legacy `amd64_<version>` tag for backend images and have no frontend entry. Migrate the backend images to `backend-X.Y.Z` and add a `platform-frontend` entry initialized to `frontend-0.1.0`.
- **Consolidate frontend CI** — move `frontend/.github/workflows/ci.yml` to repo root `.github/workflows/frontend-ci.yaml` with `paths: ['frontend/**']`. Delete the in-tree version.

### Frontend dev namespace (on `biosim-rke`)

**Goal:** the frontend developer can deploy any `frontend-vX.Y.Z` tag to a dedicated namespace on the RKE cluster without touching prod, iterating against the real prod backend so API behavior matches what end users see.

- **Cluster:** `biosim-rke` (reuses existing nginx ingress controller, cert-manager, and pull secrets).
- **Namespace:** `frontend-dev`. Created by the overlay's `kustomization.yaml`.
- **Scope:** frontend-only. No api/worker/mongo/temporal in this namespace. `API_URL` points at the prod backend host (`api.biosim.biosimulations.org`). Trade-off accepted: dev frontend submits real jobs to prod; backend API-contract changes still need to be tested via the local stack (Phase 1) or a separate path.
- **Restructure `kustomize/base/`** — split the frontend out into a sub-package so both the prod overlay and the dev overlay can include it without duplicating YAML:
  - New `kustomize/base/frontend/kustomization.yaml` containing the frontend Deployment + Service (the same Deployment + Service described above).
  - `kustomize/base/kustomization.yaml` references `frontend/` as a resource so the existing `biosim-rke` overlay continues to ship the frontend alongside api/worker/mongo.
- **New overlay `kustomize/overlays/biosim-rke-frontend-dev/`:**
  - `kustomization.yaml`:
    - `namespace: frontend-dev`
    - `resources: ['../../base/frontend', 'ingress.yaml', 'secret-ghcr.yaml']` — **only** the frontend sub-base, no api/worker/mongo.
    - `replicas:` override `frontend` → `1`.
    - `images:` `platform-frontend` → whatever `frontend-X.Y.Z` tag the developer wants deployed. Bumped manually (or via a thin helper) when a new build is published.
    - `configMapGenerator` for `frontend-config`: `BASE_URL=https://biosim-dev.biosimulations.org`, `API_URL=https://api.biosim.biosimulations.org`. **No `API_URL_INTERNAL`** in this namespace — there is no in-namespace api Service to point at, so SSR fetches also use the public URL.
  - `ingress.yaml`: new Ingress resource (separate from the prod Ingress), one host rule for `biosim-dev.biosimulations.org` → `frontend` Service on port 80. TLS via the same `letsencrypt-prod` cluster issuer (cert-manager mints a new cert for the dev host).
  - `secret-ghcr.yaml` (or equivalent): pull secret for the image registry. The same pattern the prod overlay uses.
- **Image promotion model:** the dev's `frontend-X.Y.Z` images are the *same* images that can be promoted to prod — only the overlay tag reference changes. Promoting dev → prod means updating `kustomize/overlays/biosim-rke/kustomization.yaml`'s `platform-frontend` tag to the version that's been validated in `frontend-dev`. No separate `-dev` image tag suffix.
- **DNS prerequisite:** `biosim-dev.biosimulations.org` must resolve to the RKE ingress IP. One-time DNS record add.
- **What's deferred:** auto-promotion on merge, per-branch preview environments, dev backend isolation. All can layer on later — the namespace + overlay is the foundation.

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
  - Steps: `docker compose up -d mongo temporal`, install poetry, set up Node 22, build backend + frontend, start them in the background, wait for `:8000/version` and `:4200/`, hit a handful of endpoints (e.g., `GET /version`, `GET /docs`, frontend root render).
  - **Do not run a real simulation** — biosimulations.org submission is minutes-long. Test only the wiring (frontend boots, backend boots, CORS works, runtime config reaches the frontend).
  - Budget: aim for under 5 minutes.

## Resolved decisions (2026-05-15)

1. **Ingress routing in prod** — subdomain split. Frontend on `biosim.biosimulations.org`, backend moved to `api.biosim.biosimulations.org`. Keeps existing backend paths unchanged; lowest-disruption.
2. **Versioning model** — services version independently. Backend → `backend/biosim_server/version.py` + git tag `backend-vX.Y.Z` + image tag `backend-X.Y.Z`. Frontend → `frontend/package.json` (add it, seed at `0.1.0`; resets the prior `3.0.6` stream from Harrison's standalone repo) + git tag `frontend-vX.Y.Z` + image tag `frontend-X.Y.Z`. Coordinated full releases use plain `vX.Y.Z` tags **only from main**, bumping both files to the same version. A `frontend/scripts/bump-frontend.sh` helper bumps patch + commits + tags; branch deploys allowed. Open follow-up: consolidate frontend image registry on GHCR vs. dockerhub.
3. **Temporal image (local dev)** — `temporalio/temporal server start-dev` (single binary, no UI). Swap to `auto-setup` later only if workflow-UI debugging becomes necessary.
4. **Frontend `package.json` name** — rename `"website"` → `"platform-frontend"` (folded into Pre-work step 7).
5. **Phasing** — three separate PRs off `frontend-backend-int`: (a) Pre-work + Phase 1, (b) Phase 2, (c) Phase 3. Phase 1 lands first to unblock Harrison's webapp dev.
