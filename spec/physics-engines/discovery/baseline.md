# Compatibility baseline

Status: TASK-001 complete. Captured against main `83ffac2ebc55423cc804b62342337399b17373c3` on 2026-10-25. This is the pre-physics compatibility baseline for PHY-001, PHY-002, PHY-003, PHY-004 and PHY-015. Runtime code, dependencies, credentials and production state are unchanged.

## Frozen contracts

`tests/test_physics_compatibility_baseline.py` adds 29 cases using only synthetic users and circuits:

- Nine literal canonical UTF-8 requests and independent SHA-256 values cover Clifft Stim sampling/probabilities, Clifft QASM2 probabilities, qsim QASM2/QASM3 sampling/probabilities, and Aer QASM2/QASM3 sampling. Minimal and explicitly defaulted requests retain the same canonical bytes and durable reservation identity. Legacy Clifft requests omit `engine`; legacy Stim/QASM2 requests omit empty `bindings`.
- The full 18-cell engine/format/operation matrix freezes current generic admission. All nine unsupported combinations return the sanitized 422 detail envelope and create neither a job nor an idempotency reservation. Circuit parsing remains a worker responsibility; API admission alone does not prove valid circuit execution.
- All nine admitted cells also execute their actual native worker subprocess. Fixed one-qubit X circuits produce deterministic samples of one, or basis probabilities `[0, 1]` within absolute tolerance `1e-6`. Results retain `status`, `summary` and base64 `artifact`; NPZ loading uses `allow_pickle=False`. Clifft sampling retains `measurements`, `detectors`, `observables`, `exp_vals`; QASM sampling retains `classical_memory` with uint8 shape `(1000, 1)` and schema `qasm-samples/1`; probabilities retain `bitstrings` and `probabilities`.
- Public job fields remain `id`, `name`, `status`, `created`, `updated`, `error`, `summary`. Location/replay headers, schema reload and exact 409/410/503 errors are exercised. During a durable maintenance hold, the original owner can replay an existing request; another owner cannot use that reservation to bypass admission. A retained reservation for a deleted job returns 410 and creates no replacement work.
- Capability `engines` remains a mapping. Installed Clifft `0.12.0` and Qiskit `2.6.0` match the public metadata. The capability matrix, client-managed sequences/batches/state and complete-result SSE behavior are frozen.

Existing focused tests also retain coverage for concurrent owner-scoped retries, historical pre-engine/pre-binding hashes and dedicated-route replay, SSE ordering/resume, release holds and actual callback/result round trips. These assertions supplement those tests rather than replace or weaken them.

## Verification receipt

Environment: pinned CI runner image, with registration entrypoint bypassed. No Docker socket, cluster configuration or credential mounts were passed. The copied source gets a fresh frozen dependency environment with worker, controller and backup extras, using `/usr/bin/python3.12`. Native children retain their existing resource limits.

Local image identity:

```text
sha256:17fec9e20e50023882509aae2c52b536fead249c1e5bab4948daca712d678562
```

The run used an existing local tag resolving to that image identity. The equivalent reproduction command below uses its immutable local image ID and streams a source copy including the uncommitted test, excluding local Git metadata, virtual environments and private operational artifacts:

```bash
set -o pipefail
tar --exclude=.git --exclude=.venv --exclude=var --exclude=test-results -cf - . |
  docker run --rm -i --entrypoint bash \
    sha256:17fec9e20e50023882509aae2c52b536fead249c1e5bab4948daca712d678562 -euc '
      mkdir -p /tmp/wqs-baseline
      tar -xf - -C /tmp/wqs-baseline
      cd /tmp/wqs-baseline
      export UV_PYTHON=/usr/bin/python3.12
      export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
      uv run --frozen --extra worker --extra controller --extra backup pytest -q \
        tests/test_service.py tests/test_transactions.py \
        tests/test_release_control.py tests/test_physics_compatibility_baseline.py
    '
```

Result: **96 passed, 1 warning in 10.24 seconds**. Existing focused baseline: 67 passing cases. Newly passing baseline cases: 29. Regressions: none. Unchanged failures: none. Flaky/environment failures: none in this run. The one warning is FastAPI/Starlette's existing TestClient `httpx` deprecation; no test was skipped or weakened. The known host native-library static-TLS issue was avoided by executing the prescribed pinned CI environment, without a product workaround.

This receipt verifies current CPU engine compatibility. It does not establish any new physics backend, GPU suitability, cluster capacity, release acceptance or deployment readiness.

## Changed files

- `tests/test_physics_compatibility_baseline.py`: missing-coverage tests for frozen canonical requests, admission, native result members, lifecycle errors and capability metadata. No existing assertions changed.
- `spec/physics-engines/discovery/baseline.md`: this evidence and reproduction record.

The two-file write lease and `interface_policy=none` are respected. There are no new production symbols. TASK-002, TASK-003 and TASK-004 can now consume this compatibility baseline.
