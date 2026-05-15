#!/bin/bash
#
# Build and push platform images. Multi-arch (linux/amd64 + linux/arm64) is
# baked into a single manifest tag per image — kustomize overlays reference
# that one tag and Docker selects the right arch at pull time.
#
# Usage:
#   build_and_push.sh backend [VERSION]
#       Build platform-api + platform-worker at tag backend-VERSION.
#       VERSION defaults to backend/biosim_server/version.py.
#
#   build_and_push.sh frontend [VERSION]
#       Build platform-frontend at tag frontend-VERSION.
#       VERSION defaults to frontend/package.json.
#       Runs `npm ci && npm run build` first (build outside Docker, image
#       is runtime-only). Needs Node 22 on the host.
#
#   build_and_push.sh all VERSION
#       Build all three at the given VERSION. Used for coordinated full
#       releases off main (vX.Y.Z); does NOT write the version into the
#       source files — bump those first.
#
# Requirements:
#   - Docker buildx (default on recent Docker Desktop installs)
#   - A buildx builder that supports linux/amd64 + linux/arm64
#   - Logged in to ghcr.io with push permission

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
PLATFORMS="linux/amd64,linux/arm64"

usage() {
  cat >&2 <<EOF
Usage: $0 backend|frontend|all [VERSION]
   backend [V]   build api + worker at backend-V  (V default: backend version file)
   frontend [V]  build platform-frontend at frontend-V  (V default: package.json)
   all V         build all three at V (coordinated release; V required)
EOF
  exit 1
}

read_backend_version() {
  grep -oE '__version__ = "[^"]+"' "${BACKEND_DIR}/biosim_server/version.py" \
    | awk -F'"' '{print $2}'
}

read_frontend_version() {
  node -p "require('${FRONTEND_DIR}/package.json').version"
}

build_and_push_backend() {
  local version="$1"
  local tag="backend-${version}"
  echo ">>> backend api + worker @ ${tag}"
  for service in api worker; do
    local image="ghcr.io/biosimulations/platform-${service}:${tag}"
    docker buildx build \
      --platform "${PLATFORMS}" \
      -f "${BACKEND_DIR}/Dockerfile.${service}" \
      -t "${image}" \
      --push \
      "${BACKEND_DIR}"
    echo "    pushed ${image}"
  done
}

build_and_push_frontend() {
  local version="$1"
  local tag="frontend-${version}"
  echo ">>> frontend build (npm) @ ${tag}"
  (cd "${FRONTEND_DIR}" && npm ci && npm run build)
  local image="ghcr.io/biosimulations/platform-frontend:${tag}"
  echo ">>> frontend image @ ${tag}"
  docker buildx build \
    --platform "${PLATFORMS}" \
    -t "${image}" \
    --push \
    "${FRONTEND_DIR}"
  echo "    pushed ${image}"
}

[ $# -lt 1 ] && usage

target="$1"
shift
version_arg="${1:-}"

case "${target}" in
  backend)
    v="${version_arg:-$(read_backend_version)}"
    build_and_push_backend "${v}"
    ;;
  frontend)
    v="${version_arg:-$(read_frontend_version)}"
    build_and_push_frontend "${v}"
    ;;
  all)
    if [[ -z "${version_arg}" ]]; then
      echo "error: 'all' requires an explicit VERSION argument" >&2
      usage
    fi
    build_and_push_backend "${version_arg}"
    build_and_push_frontend "${version_arg}"
    ;;
  *)
    usage
    ;;
esac
