#!/usr/bin/env bash
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
age_dir=$(bash tools/ci/install-age.sh)
export PATH="$age_dir:$PATH"
uv sync --frozen --extra worker --extra controller --extra backup
uv pip check
mkdir -p test-results
uv run --frozen --extra worker --extra controller --extra backup pytest -q --junitxml=test-results/pytest.xml
node --test tests/test_admin_console.cjs
