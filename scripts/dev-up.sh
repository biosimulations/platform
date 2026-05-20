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

if [[ ! -f .env && -f .env.example ]]; then
  echo "note: no .env found at repo root; copying .env.example -> .env"
  cp .env.example .env
fi

docker compose ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} up -d

echo
echo "Infra is up. Next:"
echo
echo "  # backend api (in another terminal)"
echo "  cd backend && poetry run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000 --reload"
echo
echo "  # backend worker (in another terminal)"
echo "  cd backend && poetry run python -m biosim_server.worker.worker_main"
echo
echo "  # frontend (in another terminal)"
echo "  cd frontend && npm run dev"
echo
echo "When you're done: scripts/dev-down.sh"
