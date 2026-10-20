#!/usr/bin/env bash
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
age_bin=$(bash tools/ci/install-age.sh)
export PATH="$age_bin:$PATH"
uv sync --frozen --extra worker --extra controller
uv pip check
# Verify the backup extra independently of the simulator worker environment.
backup_env=$(mktemp -d)
trap 'rm -rf "$backup_env"' EXIT
UV_PROJECT_ENVIRONMENT="$backup_env" uv sync --frozen --extra backup --no-dev
"$backup_env/bin/python" -c 'from tools.backup import create, native_load, restore'
mkdir -p test-results
uv run --frozen --extra worker --extra controller pytest -q --junitxml=test-results/pytest.xml
node --test tests/test_admin_console.cjs
