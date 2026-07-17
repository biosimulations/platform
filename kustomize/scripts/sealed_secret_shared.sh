#!/usr/bin/env bash

# This script is used to create a sealed secret for the gcs storage
# this script should take 3 arguments as input:
#   namespace
#   mongodb_uri
#   storage_gcs_credentials_file
#   and an optional --cert <filename.pem> argument to specify the certificate file for kubeseal
# Example: ./sealed_secret_shared.sh [--cert <filename.pem>] <namespace> <mongodb_uri> <storage_gcs_credentials_file> > output.yaml

# note that for GKE, the cert file is needed and can be extracted by running:
# kubeseal --fetch-cert > filename.pem
# or
# kubectl get secret -n kube-system -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml \
#     | grep tls.crt | awk '{print $2}' | base64 --decode > filename.pem

# Initialize variables
CERT_ARG=""
CONTROLLER_NAME="sealed-secrets-controller"
CONTROLLER_NAMESPACE="sealed-secrets"

# Parse optional arguments
while [[ "$1" == --* ]]; do
  case "$1" in
    --cert)
      CERT_ARG="$2"
      shift 2
      ;;
    --controller-name)
      CONTROLLER_NAME="$2"
      shift 2
      ;;
    --controller-namespace)
      CONTROLLER_NAMESPACE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Validate the number of positional arguments
if [ "$#" -ne 3 ]; then
    echo "Illegal number of parameters"
    echo "Usage: ./sealed_secret_shared.sh [--cert <filename.pem>] <namespace> <mongodb_uri> <storage_gcs_credentials_file>"
    exit 1
fi

SECRET_NAME="shared-secrets"
NAMESPACE=$1
MONGODB_URI=$2
STORAGE_GCS_CREDENTIALS_FILE=$3

# Create the generic secret and seal it.
# When --cert is given, kubeseal seals offline against that cert (needed for GKE).
# Otherwise it fetches the cert from the named controller in-cluster.
kubectl create secret generic ${SECRET_NAME} --dry-run=client \
      --from-literal=mongodb-uri="${MONGODB_URI}" \
      --from-file=gcs_credentials.json="${STORAGE_GCS_CREDENTIALS_FILE}" \
      --namespace="${NAMESPACE}" -o yaml \
      | kubeseal --controller-name="${CONTROLLER_NAME}" --controller-namespace="${CONTROLLER_NAMESPACE}" \
                 --format yaml ${CERT_ARG:+--cert=$CERT_ARG}