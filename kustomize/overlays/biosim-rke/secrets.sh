#!/usr/bin/env bash

set -eu

# Regenerate this overlay's committed sealed secrets from local plaintext values.
#
#   1. cp secrets.dat.template secrets.dat   # secrets.dat is gitignored
#   2. edit secrets.dat with your real values
#   3. ./secrets.sh                          # rewrites secret-shared.yaml + secret-ghcr.yaml
#   4. review + commit the regenerated secret-*.yaml
#
# Plaintext lives only in secrets.dat (never committed); the sealed output is
# safe to commit. This replaces the old flow of stashing secrets in ~/.ssh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../" && pwd)"

NAMESPACE=biosim-rke
SCRIPTS_DIR="${REPO_ROOT}/kustomize/scripts"
SECRETS_DIR="${SCRIPT_DIR}"

SECRETS_DATA_FILE="${SECRETS_DIR}/secrets.dat"
if [ ! -f "${SECRETS_DATA_FILE}" ]; then
    echo "ERROR: secrets data file not found: ${SECRETS_DATA_FILE}"
    echo "Create it from the template:"
    echo "  cp ${SECRETS_DIR}/secrets.dat.template ${SECRETS_DIR}/secrets.dat"
    echo "  # then edit secrets.dat with your real values"
    exit 1
fi

echo "Loading secrets from: ${SECRETS_DATA_FILE}"
# shellcheck disable=SC1090
source "${SECRETS_DATA_FILE}"

# Assemble optional kubeseal targeting from secrets.dat. GKE typically seals
# offline against a fetched cert (SEALED_SECRETS_CERT); on-cluster controllers
# (RKE/local) are found by name.
KUBESEAL_ARGS=()
[ -n "${SEALED_SECRETS_CERT:-}" ] && KUBESEAL_ARGS+=(--cert "${SEALED_SECRETS_CERT}")
[ -n "${SEALED_SECRETS_CONTROLLER_NAME:-}" ] && KUBESEAL_ARGS+=(--controller-name "${SEALED_SECRETS_CONTROLLER_NAME}")
[ -n "${SEALED_SECRETS_CONTROLLER_NAMESPACE:-}" ] && KUBESEAL_ARGS+=(--controller-namespace "${SEALED_SECRETS_CONTROLLER_NAMESPACE}")

echo ""
echo "=== Generating sealed secrets for ${NAMESPACE} ==="

echo "Generating shared-secrets (Mongo + GCS)..."
"${SCRIPTS_DIR}/sealed_secret_shared.sh" ${KUBESEAL_ARGS[@]+"${KUBESEAL_ARGS[@]}"} \
    "${NAMESPACE}" "${MONGODB_URI}" "${GCS_CREDENTIALS_FILE}" \
    > "${SECRETS_DIR}/secret-shared.yaml"
echo "✓ secret-shared.yaml"

echo "Generating ghcr-secret (GHCR image pulls)..."
"${SCRIPTS_DIR}/sealed_secret_ghcr.sh" ${KUBESEAL_ARGS[@]+"${KUBESEAL_ARGS[@]}"} \
    "${NAMESPACE}" "${GH_USER_NAME}" "${GH_USER_EMAIL}" "${GH_PAT}" \
    > "${SECRETS_DIR}/secret-ghcr.yaml"
echo "✓ secret-ghcr.yaml"

echo ""
echo "=== Done. Review the regenerated secret-*.yaml, then commit them. ==="
