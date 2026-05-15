#!/usr/bin/env bash
#
# Stop local-development infrastructure. Volumes are preserved by default;
# pass --wipe to discard them too.
#
# Usage:
#   scripts/dev-down.sh          # stop containers, keep mongo + minio data
#   scripts/dev-down.sh --wipe   # also delete volumes

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# Need --profile minio here too, otherwise compose won't tear down the minio
# service if it happens to be running.
if [[ "${1:-}" == "--wipe" ]]; then
  docker compose --profile minio down -v
else
  docker compose --profile minio down
fi
