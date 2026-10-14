#!/usr/bin/env bash
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
uv sync --frozen --extra worker --extra controller
uv pip check
mkdir -p test-results
uv run --frozen --extra worker --extra controller pytest -q --junitxml=test-results/pytest.xml
node --test tests/test_admin_console.cjs

# Exercise the deployable package, not only the host virtual environment.
image="wqs-worker-ci:${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$"
trap 'docker image rm "$image" >/dev/null 2>&1 || true' EXIT
docker build --target worker --tag "$image" --file deploy/Dockerfile .
bash tests/check_worker_image.sh "$image"
