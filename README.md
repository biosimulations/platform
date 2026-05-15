![backend-ci](https://github.com/biosimulations/platform/actions/workflows/ci.yaml/badge.svg)

# Biosimulations Platform

Monorepo for the **biosimulations/platform** stack. Hosts both the backend services and the webapp frontend, deployed together to Kubernetes.

_Production API:_ **[https://biosim.biosimulations.org/docs](https://biosim.biosimulations.org/docs)**

## Layout

| Directory | Purpose |
|-----------|---------|
| [`backend/`](./backend) | Python FastAPI + Temporal worker services. See [`backend/README.md`](./backend/README.md). |
| [`frontend/`](./frontend) | Nuxt 4 / Nuxt UI webapp (npm). See [`frontend/README.md`](./frontend/README.md). |
| [`kustomize/`](./kustomize) | Shared Kubernetes manifests and per-cluster overlays. |
| `.github/workflows/` | CI pipelines. Root workflow covers backend; frontend ships its own under `frontend/.github/workflows/`. |

## Local development

Bring up the local infra (Mongo + Temporal; minio optional) and run the backend + frontend natively for fast HMR / reloads.

```bash
# 1. Infra in containers
scripts/dev-up.sh                 # mongo + temporal
scripts/dev-up.sh --minio         # also bring up minio (S3-compatible storage)

# 2. Backend API (terminal A)
cd backend && poetry install
poetry run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Backend worker (terminal B)
cd backend && poetry run python -m biosim_server.worker.worker_main

# 4. Frontend dev server (terminal C)
cd frontend && npm install && npm run dev   # http://localhost:4200

# Tear down
scripts/dev-down.sh               # stop containers, keep volumes
scripts/dev-down.sh --wipe        # stop and delete volumes
```

**Config:** `dev-up.sh` copies `.env.example` → `.env` on first run. By default the backend uses `STORAGE_BACKEND=local` (filesystem cache under `./local_cache`); enable minio with `scripts/dev-up.sh --minio` and switch `STORAGE_BACKEND=minio` in `.env`. The backend calls the real public biosimulations.org APIs by default — overrideable via `BIOSIMULATIONS_API_BASE_URL` and friends.

**Per-service docs:** [`backend/README.md`](./backend/README.md) + [`backend/CLAUDE.md`](./backend/CLAUDE.md); [`frontend/README.md`](./frontend/README.md) + [`frontend/CLAUDE.md`](./frontend/CLAUDE.md).

## Build & deploy

Images are published to `ghcr.io/biosimulations/platform-*` (`platform-api`, `platform-worker`; `platform-frontend` planned). Tags use the form `<arch>_<version>`, e.g. `amd64_0.4.0`.

```bash
# Build and push backend images for amd64 + arm64 (reads version from backend/biosim_server/version.py)
bash kustomize/scripts/build_and_push.sh

# Apply an overlay
export KUBECONFIG=<path-to-kubeconfig>
kubectl kustomize kustomize/overlays/biosim-gke | kubectl apply -f -
```

## License

See [LICENSE](./LICENSE).