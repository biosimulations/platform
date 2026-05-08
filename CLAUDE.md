# CLAUDE.md - Platform Monorepo Guide

This is the **biosimulations/platform** monorepo, hosting both the Python backend and the TypeScript/Express frontend webapp behind a single Kubernetes deployment.

## Layout

```
platform/
├── backend/          # Python FastAPI + Temporal services (see backend/CLAUDE.md)
├── frontend/         # Webapp + Express server (added in Phase 2)
├── kustomize/        # Shared Kubernetes manifests + overlays
│   ├── base/         # Deployments, services for api / worker / mongodb (and frontend, when added)
│   ├── config/       # Per-cluster ConfigMaps
│   ├── overlays/     # biosim-gke, biosim-rke, biosim-local
│   └── scripts/      # build_and_push.sh, sealed_secret_*
├── .github/workflows/  # Path-filtered CI per service
├── README.md
├── LICENSE
└── CLAUDE.md         # this file
```

## Per-service guides

- **Backend** — see `backend/CLAUDE.md` for architecture, commands, and deploy steps
- **Frontend** — see `frontend/CLAUDE.md` (added in Phase 2)

## Versioning

Single shared monorepo version. Bumping `backend/biosim_server/version.py` (and the matching frontend version file once added) plus a `vX.Y.Z` git tag releases both services together.

## Image registry

All images published under `ghcr.io/biosimulations/platform-*`:
- `platform-api`
- `platform-worker`
- `platform-frontend` (added in Phase 2)

Tags are of the form `<arch>_<version>`, e.g., `amd64_0.4.0`, `arm64_0.4.0`.

## Build & push

`bash kustomize/scripts/build_and_push.sh` builds and pushes the backend api + worker images for amd64 and arm64. The version is read from `backend/biosim_server/version.py` unless overridden as the first argument.

## CI

`.github/workflows/ci.yaml` runs the backend test suite, gated on `paths: ['backend/**', '.github/workflows/**']`. Frontend CI will be added alongside the frontend code in Phase 2.

## Conventions

- Per-service code lives in its own top-level directory (`backend/`, `frontend/`).
- Shared infrastructure (kustomize, CI workflows, license, top-level README/CLAUDE) lives at the repo root.
- Each service owns its own `Dockerfile` and is built from its own directory as the build context.