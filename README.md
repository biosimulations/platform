![backend-ci](https://github.com/biosimulations/platform/actions/workflows/ci.yaml/badge.svg)

# Biosimulations Platform

Monorepo for the **biosimulations/platform** stack. Hosts both the backend services and the webapp frontend, deployed together to Kubernetes.

_Production API:_ **[https://biosim.biosimulations.org/docs](https://biosim.biosimulations.org/docs)**

## Layout

| Directory | Purpose |
|-----------|---------|
| [`backend/`](./backend) | Python FastAPI + Temporal worker services. See [`backend/README.md`](./backend/README.md). |
| [`frontend/`](./frontend) | Nuxt 4 / Nuxt UI webapp (pnpm). See [`frontend/README.md`](./frontend/README.md). |
| [`kustomize/`](./kustomize) | Shared Kubernetes manifests and per-cluster overlays. |
| `.github/workflows/` | CI pipelines. Root workflow covers backend; frontend ships its own under `frontend/.github/workflows/`. |

## Quickstart

Backend:

```bash
cd backend
poetry install
poetry run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000
poetry run python -m biosim_server.worker.worker_main
```

See [`backend/README.md`](./backend/README.md) and [`backend/CLAUDE.md`](./backend/CLAUDE.md) for full backend docs.

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