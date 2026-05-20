# CLAUDE.md - Platform Monorepo Guide

This is the **biosimulations/platform** monorepo, hosting both the Python backend and the Nuxt (Vue 3) frontend webapp, deployed together to Kubernetes.

## Layout

```
platform/
├── backend/          # Python FastAPI + Temporal services (see backend/CLAUDE.md)
├── frontend/         # Nuxt 4 / Nuxt UI webapp, npm (see frontend/CLAUDE.md)
├── kustomize/        # Shared Kubernetes manifests + overlays
│   ├── base/         # api.yaml, worker.yaml, mongodb.yaml, frontend/ (sub-package)
│   ├── config/       # Per-cluster ConfigMaps
│   ├── overlays/     # biosim-gke, biosim-rke, biosim-local, biosim-rke-frontend-dev
│   └── scripts/      # build_and_push.sh, sealed_secret_*
├── docs/             # frontend-backend-integration-plan.md (implemented)
├── .github/workflows/  # backend-ci, frontend-ci, smoke (all run on every PR)
├── compose.yaml      # Local dev infra: mongo, temporal, optional minio
├── lefthook.yml      # Local pre-commit/pre-push hooks (lint, typecheck, ruff, mypy)
├── README.md
├── LICENSE
└── CLAUDE.md         # this file
```

## Per-service guides

- **Backend** — see `backend/CLAUDE.md` for architecture, commands, and deploy steps
- **Frontend** — see `frontend/CLAUDE.md` for stack, commands, and deploy steps
- **Integration history** — `docs/frontend-backend-integration-plan.md` records the design decisions behind the monorepo wiring (subdomain split, versioning, local stack, smoke tests). Implemented across PRs #39–#42.

## Versioning

Services version **independently**:

- **Backend** — `backend/biosim_server/version.py`. Git tag `backend-vX.Y.Z`. Image tag `backend-X.Y.Z` (rebuilds `platform-api` + `platform-worker`).
- **Frontend** — `frontend/package.json`. Git tag `frontend-vX.Y.Z`. Image tag `frontend-X.Y.Z`. `frontend/scripts/bump-frontend.sh` bumps + commits + tags from any branch.
- **Coordinated releases** — plain `vX.Y.Z` tags, **only on `main`**. Bump both version files to the same `X.Y.Z`, rebuild all three images at that version.

Overlays' `images:` entries are independent per service — cherry-pick whichever version of each you want deployed.

## Image registry

All images published under `ghcr.io/biosimulations/platform-*`:
- `platform-api` — tagged `backend-X.Y.Z`
- `platform-worker` — tagged `backend-X.Y.Z`
- `platform-frontend` — tagged `frontend-X.Y.Z`

Each image is a multi-arch manifest (`linux/amd64` + `linux/arm64`) at a single tag — kustomize references one tag, Docker selects the right arch at pull time.

## Build & push

`bash kustomize/scripts/build_and_push.sh <backend|frontend|all> [VERSION]`

- `backend [V]` — builds `platform-api` + `platform-worker` at `backend-V`. V defaults to `backend/biosim_server/version.py`.
- `frontend [V]` — builds `platform-frontend` at `frontend-V`. V defaults to `frontend/package.json`. Runs `npm ci && npm run build` first; the image is runtime-only. Needs Node 22 on the host.
- `all V` — coordinated release; V required. Does NOT write versions into source files — bump those first.

## Local development

```
docker compose up -d            # mongo + temporal (add --profile minio for S3 testing)
# then run backend and frontend natively (see backend/CLAUDE.md, frontend/CLAUDE.md)
```

`compose.yaml` runs infra only; app code runs native (poetry/npm) for fast HMR. Each service has its own `.env.example` (`backend/.env.example`, `frontend/.env.example`) — `dev-up.sh` seeds the per-service `.env` files on first run.

## CI

All three workflows run on every PR (path filters were removed — cross-service refactors and lockfile changes were getting missed).

- `.github/workflows/ci.yaml` (`backend-ci`) — backend test suite, ruff, mypy.
- `.github/workflows/frontend-ci.yaml` — `npm run lint` + `npm run typecheck`.
- `.github/workflows/smoke.yaml` — joint local-stack smoke. Boots compose infra + backend + frontend, verifies they talk to each other. Does not run a real biosimulations.org simulation.

`lefthook.yml` runs the lint/typecheck/ruff/mypy checks locally on pre-commit and pre-push so most failures are caught before CI. Install with `lefthook install` after `brew install lefthook`.

## Ingress / hosts (prod)

Subdomain split: `biosim.biosimulations.org` → frontend; `api.biosim.biosimulations.org` → backend. SSR traffic inside the cluster uses `API_URL_INTERNAL=http://api:8000` to skip the public ingress.

## Frontend dev namespace

`kustomize/overlays/biosim-rke-frontend-dev/` deploys frontend-only into the `frontend-dev` namespace on the RKE cluster at `biosim-dev.cam.uchc.edu`, pointed at the RKE prod-tier backend (`api.biosim.cam.uchc.edu`). Use case is WIP-preview / share-a-build / production-build SSR validation — not personal iteration (run `npm run dev` against a deployed API for that).

## Conventions

- Per-service code lives in its own top-level directory (`backend/`, `frontend/`).
- Shared infrastructure (kustomize, compose, CI workflows, lefthook, license, top-level README/CLAUDE) lives at the repo root.
- Each service owns its own `Dockerfile` and is built from its own directory as the build context.
