#!/usr/bin/env bash
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
workers=${WQS_TEST_WORKERS:-2}
case "$workers" in
  1|2) ;;
  *) echo 'WQS_TEST_WORKERS must be 1 or 2' >&2; exit 2 ;;
esac
age_dir=$(bash tools/ci/install-age.sh)
export PATH="$age_dir:$PATH"
uv sync --frozen --extra worker --extra controller --extra backup
uv pip check
# Verify the backup extra independently of the simulator worker environment.
backup_env=$(mktemp -d)
trap 'rm -rf "$backup_env"' EXIT
UV_PROJECT_ENVIRONMENT="$backup_env" uv sync --frozen --extra backup --no-dev
"$backup_env/bin/python" -c 'import numpy; from tools.backup import create, native_load, restore'
mkdir -p test-results
pytest_args=(-q --junitxml=test-results/pytest.xml)
if [ "$workers" = 2 ]; then
  # Each module stays in one process; process-global monkeypatches remain local.
  # A crashed worker is a failure, never silently retried.
  pytest_args+=(-n 2 --dist=loadfile --max-worker-restart=0)
fi
uv run --frozen --extra worker --extra controller --extra backup pytest "${pytest_args[@]}"
node --test tests/test_admin_console.cjs
