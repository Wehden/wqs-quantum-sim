#!/usr/bin/env bash
set -euo pipefail
image="wqs-deployer-check:${GITHUB_SHA:-local}-$$"
trap 'docker image rm "$image" >/dev/null' EXIT
docker build -f tools/cd/runner.Dockerfile -t "$image" .
# No registration, cluster credential, Docker socket or network in this check.
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --entrypoint bash "$image" -euc '
    test "$(id -u)" = 1001
    python3 -c "import sys; assert sys.version_info[:2] == (3, 12)"
    python3 -c "import numpy; assert numpy.__version__ == \"2.5.4\""
    python3 -c "import boto3, bech32, jsonschema, cryptography"
    test "$(age --version)" = v1.2.1
    gh --version
    docker --version
    kubectl version --client=true
    bash -n /usr/local/bin/wqs-deployment-runner
  '
