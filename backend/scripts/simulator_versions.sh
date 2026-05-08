#!/bin/bash

PARENT_OF_SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"

echo ${PARENT_OF_SCRIPT_DIR}

curl -s "https://api.biosimulators.org/simulators" | jq -c '.[] | {id, name, version, image_url: .image.url, image_digest: .image.digest, created: .biosimulators.created, updated: .biosimulators.updated }' | grep "sha256" > simulator_versions.ndjson