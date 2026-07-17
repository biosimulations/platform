#!/usr/bin/env bash
#
# Start local-development infrastructure (Mongo + Temporal, optionally minio).
#
# Usage:
#   scripts/dev-up.sh           # mongo + temporal
#   scripts/dev-up.sh --minio   # mongo + temporal + minio
#
# Backend api/worker and the frontend dev server still run natively on your
# machine — see the commands printed below.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

PROFILE_ARGS=()
if [[ "${1:-}" == "--minio" ]]; then
  PROFILE_ARGS=(--profile minio)
fi

# Per-service .env files (gitignored). Backend reads via python-dotenv;
# frontend reads via Nuxt's built-in dotenv at dev-server startup.
seed_env() {
  local dir="$1"
  if [[ ! -f "${dir}/.env" && -f "${dir}/.env.example" ]]; then
    echo "note: no ${dir}/.env found; copying ${dir}/.env.example -> ${dir}/.env"
    cp "${dir}/.env.example" "${dir}/.env"
  fi
}
seed_env backend
seed_env frontend

# `${arr[@]+"${arr[@]}"}` form is safe on macOS's default bash 3.2 — plain
# `"${arr[@]}"` of an empty array errors as "unbound variable" under `set -u`.
docker compose ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} up -d

echo
echo "Infra is up. Next:"
echo
echo "  # backend api (in another terminal)"
echo "  cd backend && uv run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000 --reload"
echo
echo "  # backend worker (in another terminal)"
echo "  cd backend && uv run python -m biosim_server.worker.worker_main"
echo
echo "  # frontend (in another terminal)"
echo "  cd frontend && npm run dev"
echo
echo "When you're done: scripts/dev-down.sh"
