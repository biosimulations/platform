#!/usr/bin/env bash
#
# Start the backend locally: Mongo + Temporal, then the API and the worker.
#
# Usage:
#   backend/scripts/run-local.sh           # mongo + temporal, then api + worker
#   backend/scripts/run-local.sh --minio   # also start minio
#
# Follows the Local development section of the repo README. The frontend
# stays a separate command (`cd frontend && npm run dev`). Ctrl-C stops the
# API and worker. Containers stay up; scripts/dev-down.sh tears them down.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
BACKEND="$REPO_ROOT/backend"

MINIO=()
if [[ "${1:-}" == "--minio" ]]; then
  MINIO=(--minio)
elif [[ -n "${1:-}" ]]; then
  echo "usage: backend/scripts/run-local.sh [--minio]" >&2
  exit 1
fi

# dev-up.sh seeds backend/.env on first run and starts compose infra.
# The ${arr[@]+...} form is safe on macOS bash 3.2 under `set -u`.
"$REPO_ROOT/scripts/dev-up.sh" ${MINIO[@]+"${MINIO[@]}"}

cd "$BACKEND"
uv sync

worker_pid=
api_pid=

# Job control gives each background command its own process group, so
# stopping uvicorn --reload also stops the reloader child.
set -m

cleanup() {
  trap - EXIT INT TERM
  local pid
  for pid in "$api_pid" "$worker_pid"; do
    if [[ -n "$pid" ]]; then
      kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    fi
  done
  wait || true
}
trap cleanup EXIT INT TERM

uv run python -m biosim_server.worker.worker_main &
worker_pid=$!

uv run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000 --reload &
api_pid=$!

echo
echo "Backend is running."
echo "  API:    http://localhost:8000/docs"
echo "  Worker: biosim_server.worker.worker_main"
echo "Ctrl-C stops both. Infra containers stay up; scripts/dev-down.sh stops them."

# bash 3.2 (macOS) has no `wait -n`. Exit when either process dies.
while kill -0 "$worker_pid" 2>/dev/null && kill -0 "$api_pid" 2>/dev/null; do
  sleep 1
done
