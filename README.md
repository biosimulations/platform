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
| `.github/workflows/` | CI pipelines. `ci.yaml` runs backend tests; `frontend-ci.yaml` runs frontend lint + typecheck; `smoke.yaml` runs the joint local-stack smoke. All three run on every PR (no path filters). |

## Local development

Bring up the local infra (Mongo + Temporal; minio optional) and run the backend + frontend natively for fast HMR / reloads.

```bash
# 1. Infra in containers
scripts/dev-up.sh                 # mongo + temporal
scripts/dev-up.sh --minio         # also bring up minio (S3-compatible storage)

# 2. Backend API (terminal A)
cd backend && uv sync
uv run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Backend worker (terminal B)
cd backend && uv run python -m biosim_server.worker.worker_main

# 4. Frontend dev server (terminal C)
cd frontend && npm install && npm run dev   # http://localhost:4200

# Tear down
scripts/dev-down.sh               # stop containers, keep volumes
scripts/dev-down.sh --wipe        # stop and delete volumes
```

**Config:** each service has its own `.env`. `dev-up.sh` seeds them on first run by copying `backend/.env.example` → `backend/.env` and `frontend/.env.example` → `frontend/.env`. Backend reads via `python-dotenv` (walks up from CWD); frontend reads via Nuxt's built-in dotenv at dev-server startup. By default the backend uses `STORAGE_BACKEND=local` (filesystem cache under `./local_cache`); enable minio with `scripts/dev-up.sh --minio` and switch `STORAGE_BACKEND=minio` in `backend/.env`. The backend calls the real public biosimulations.org APIs by default — overrideable via `BIOSIMULATIONS_API_BASE_URL` and friends. The frontend's `.env` uses Nuxt's `NUXT_PUBLIC_*` runtime-override names so local dev exercises the same code path as the deployed ConfigMaps.

**Per-service docs:** [`backend/README.md`](./backend/README.md) + [`backend/CLAUDE.md`](./backend/CLAUDE.md); [`frontend/README.md`](./frontend/README.md) + [`frontend/CLAUDE.md`](./frontend/CLAUDE.md).

### Windows

Two supported paths — pick by how much parity you need with the rest of the team.

- **Git Bash** (ships with [Git for Windows](https://git-scm.com/download/win)). Run the same `scripts/dev-up.sh` / `scripts/dev-down.sh` from Git Bash; Docker Desktop on Windows puts `docker` on `PATH` and the scripts use only Windows-side tokens, so there's no MSYS path-translation pain. Lefthook finds Git Bash automatically; the `nvm.sh` line in `lefthook.yml` no-ops and the hook falls back to whatever Node is on `PATH` (must be 22). Install Python + uv + Node 22 with your usual Windows installers. **One gotcha:** if `*.sh` files get checked out with CRLF endings, bash fails with `$'\r': command not found` — run `git config --global core.autocrlf input` before cloning, or convert in-place with `dos2unix scripts/*.sh`.
- **WSL2** (Ubuntu). Strict parity with macOS/Linux teammates and with what `backend-ci`/`frontend-ci` run on `ubuntu-latest`. Worth the ~30 min setup if you'll be touching `kustomize/scripts/build_and_push.sh`, debugging hook portability, or running anything Linux-flavored. Two footguns: keep the repo inside the WSL filesystem (e.g. `~/code/platform`, not `/mnt/c/...`) for usable file-watch and I/O perf, and use Docker Desktop with the WSL2 integration enabled so `docker` inside WSL talks to the same daemon as the host.

PowerShell-native scripts aren't provided — Git Bash covers the same ground without the maintenance overhead of parallel `.ps1` versions.

### Commit hooks

Lefthook runs lint on every commit (frontend `eslint`, backend `ruff`) and type checks on every push (frontend `npm run typecheck`, backend `mypy`). One-time setup per workstation:

```bash
brew install lefthook    # macOS — or `go install github.com/evilmartians/lefthook@latest`
lefthook install         # activate the hooks in this clone
```

Config lives in [`lefthook.yml`](./lefthook.yml). Skip a hook with `git commit --no-verify` / `git push --no-verify` if you really need to — branch protection on `main` runs the same checks in CI before merge, so a local bypass can't ship.

## Build & deploy

Images are published to `ghcr.io/biosimulations/platform-{api,worker,frontend}` as multi-arch manifests (linux/amd64 + linux/arm64). Backend tags use `backend-X.Y.Z` (version from `backend/biosim_server/version.py`); frontend tags use `frontend-X.Y.Z` (version from `frontend/package.json`).

```bash
# Build and push backend api + worker
bash kustomize/scripts/build_and_push.sh backend

# Build and push the frontend (runs npm ci && npm run build first)
bash kustomize/scripts/build_and_push.sh frontend

# Coordinated full release — both services at the same version
bash kustomize/scripts/build_and_push.sh all 0.5.0

# Apply an overlay
export KUBECONFIG=<path-to-kubeconfig>
kubectl kustomize kustomize/overlays/biosim-gke | kubectl apply -f -
```

## License

See [LICENSE](./LICENSE).