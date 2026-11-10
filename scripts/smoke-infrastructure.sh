#!/usr/bin/env bash
# Shared by CI and the local old/new-compose regression check.
# COMPOSE_FILE / COMPOSE_PROJECT_NAME can select an isolated stack.
set -euo pipefail

fail_logs() {
  docker compose ps -a >&2 || true
  docker compose logs --tail=200 mongo temporal >&2 || true
}
trap 'status=$?; fail_logs; exit "$status"' ERR

for budget in "${SMOKE_MONGO_WAIT_SECONDS:-30}" "${SMOKE_TEMPORAL_WAIT_SECONDS:-60}" "${SMOKE_BACKEND_WAIT_SECONDS:-60}"; do
  case "$budget" in ''|*[!0-9]*|0) echo 'smoke wait budgets must be positive integers' >&2; exit 2 ;; esac
done

wait_mongo() {
  local deadline=$((SECONDS + ${SMOKE_MONGO_WAIT_SECONDS:-30}))
  while ((SECONDS < deadline)); do
    if docker compose exec -T mongo mongosh --quiet \
      --eval 'if (db.runCommand({ping: 1}).ok !== 1) quit(1)' >/dev/null 2>&1; then
      echo 'mongo ready'
      return 0
    fi
    sleep 1
  done
  echo 'mongo did not become ready' >&2
  return 1
}

wait_temporal() {
  local deadline=$((SECONDS + ${SMOKE_TEMPORAL_WAIT_SECONDS:-60}))
  while ((SECONDS < deadline)); do
    if health=$(docker compose exec -T temporal temporal operator cluster health \
      --address localhost:7233 --command-timeout 2s 2>/dev/null) && \
      printf '%s\n' "$health" | grep -Eq '(^|[[:space:]:])SERVING([[:space:]]|$)'; then
      echo 'temporal SERVING'
      return 0
    fi
    sleep 1
  done
  echo 'temporal did not become SERVING' >&2
  return 1
}

case "${1:-infra}" in
  infra)
    wait_mongo
    wait_temporal
    probe="smoke-persist-${GITHUB_RUN_ID:-local-$(date +%s)-$$}"
    docker compose exec -T temporal temporal workflow start --address localhost:7233 \
      --type SmokePersistence --task-queue smoke --workflow-id "$probe"
    docker compose up -d --force-recreate temporal
    wait_temporal
    docker compose exec -T temporal temporal workflow describe --address localhost:7233 --workflow-id "$probe"
    docker compose exec -T temporal temporal workflow terminate --address localhost:7233 --workflow-id "$probe"
    echo 'temporal persistence probe survived container recreation'
    ;;
  backend)
    deadline=$((SECONDS + ${SMOKE_BACKEND_WAIT_SECONDS:-60}))
    while ((SECONDS < deadline)); do
      if curl -fsS --connect-timeout 1 --max-time 2 "${API_URL:-http://localhost:8000}/ready" | \
        jq -e '.checks.temporal == true' >/dev/null; then
        echo 'backend reports checks.temporal == true'
        exit 0
      fi
      sleep 1
    done
    echo 'backend did not connect to Temporal' >&2
    fail_logs
    exit 1
    ;;
  *) echo 'usage: smoke-infrastructure.sh [infra|backend]' >&2; exit 2 ;;
esac
