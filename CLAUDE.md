# CLAUDE.md - Platform Monorepo Guide

This is the **biosimulations/platform** monorepo, hosting both the Python backend and the Nuxt (Vue 3) frontend webapp, deployed together to Kubernetes.

## Layout

```
platform/
├── backend/          # Python FastAPI + Temporal services (see backend/CLAUDE.md)
├── frontend/         # Nuxt 4 / Nuxt UI webapp (pnpm). Merged 2026-05; deploy wiring still TBD.
├── kustomize/        # Shared Kubernetes manifests + overlays
│   ├── base/         # Deployments, services for api / worker / mongodb (frontend not yet added)
│   ├── config/       # Per-cluster ConfigMaps
│   ├── overlays/     # biosim-gke, biosim-rke, biosim-local
│   └── scripts/      # build_and_push.sh, sealed_secret_*
├── .github/workflows/  # Path-filtered CI (backend only at the root; frontend has its own)
├── README.md
├── LICENSE
└── CLAUDE.md         # this file
```

## Per-service guides

- **Backend** — see `backend/CLAUDE.md` for architecture, commands, and deploy steps
- **Frontend** — no `frontend/CLAUDE.md` yet. Stack: Nuxt 4 + Nuxt UI + `@nuxtjs/seo`. Dev server: `pnpm dev` (port 4200). Runtime config reads `BASE_URL` and `API_URL` from env.

## Versioning

Backend version lives in `backend/biosim_server/version.py`. The frontend `package.json` is not yet coupled to that version. Once the frontend is wired into the shared image pipeline, both should release together under a single `vX.Y.Z` git tag.

## Image registry

Images published under `ghcr.io/biosimulations/platform-*`:
- `platform-api`
- `platform-worker`
- `platform-frontend` — **not yet built** (no `frontend/Dockerfile`, not in `build_and_push.sh`)

Tags are of the form `<arch>_<version>`, e.g., `amd64_0.4.0`, `arm64_0.4.0`.

## Build & push

`bash kustomize/scripts/build_and_push.sh` builds and pushes the backend api + worker images for amd64 and arm64. The version is read from `backend/biosim_server/version.py` unless overridden as the first argument. The frontend is not yet included.

## CI

- Root `.github/workflows/ci.yaml` — runs the backend test suite, gated on `paths: ['backend/**', '.github/workflows/**']`.
- `frontend/.github/workflows/ci.yml` — runs `pnpm lint` + `pnpm typecheck` on every push (carried over from Harrison's standalone repo; not yet consolidated under the root `.github/workflows/` directory or path-filtered).

## Conventions

- Per-service code lives in its own top-level directory (`backend/`, `frontend/`).
- Shared infrastructure (kustomize, CI workflows, license, top-level README/CLAUDE) lives at the repo root.
- Each service owns its own `Dockerfile` and is built from its own directory as the build context.