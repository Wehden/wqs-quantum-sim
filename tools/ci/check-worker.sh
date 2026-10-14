#!/usr/bin/env bash
set -euo pipefail

# Exercise the deployable package, not only the host virtual environment.
image="wqs-worker-ci:${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$"
trap 'docker image rm "$image" >/dev/null 2>&1 || true' EXIT
docker build --target worker --tag "$image" --file deploy/Dockerfile .
bash tests/check_worker_image.sh "$image"
