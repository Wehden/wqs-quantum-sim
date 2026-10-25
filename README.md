# wqsim

CPU quantum simulation service built with FastAPI, SkyPilot, and Kubernetes.
Submit a circuit, follow its durable job status, and download measurement samples
or selected basis probabilities. Clifft, Google qsim and Qiskit Aer share the same API,
invitation authentication, and worker pool.

## Supported inputs

| Engine | Input | Operation |
| --- | --- | --- |
| Clifft 0.12.0 | Stim with supported Clifft extensions | Measurement sampling |
| Clifft 0.12.0 | Unitary OpenQASM 2 | Selected basis probabilities |
| Google qsim 0.22.1 | Legacy unitary OpenQASM 2 through Cirq | Selected basis probabilities |
| Google qsim 0.22.1 | OpenQASM 2 with classical measurements | Sampling |
| Google qsim 0.22.1 | Tested OpenQASM 3 subset through Cirq 1.7.0 | Sampling or selected basis probabilities |
| Qiskit Aer 0.17.2 | Bounded conditional OpenQASM 2/3 | Sampling |

Clifft is the default. Set `"engine": "qsim"` on a QASM request to use Google's
full state-vector simulator. Its single-precision results should be compared
with numerical tolerances. This integration does not expose every upstream
feature or accept executable Python. Check `/api/capabilities` for the operations
enabled on your installation.

Google QASM2 sampling and QASM3 support bounded custom gates. Sampling preserves
mid-circuit measurement, reset and repeated writes. Conditions remain unsupported on qsim.
Legacy QASM2 probability queries keep their existing restrictions, including no
classical registers or custom gates. QASM3 inputs support sized float/angle
declarations (32 or 64) with finite numeric `bindings`; widths do not change
Python-float binding or qsim single-precision execution.

For example, send this body to `POST /api/jobs/qasm3` with bearer authentication
and a persisted `Idempotency-Key`. The selected probabilities are approximately
`[0.75, 0.25]`:

<!-- example: qasm3-probabilities -->
```json
{
  "engine": "qsim",
  "operation": "basis_probabilities",
  "name": "Bound rotation probabilities",
  "circuit": "OPENQASM 3.0; include \"stdgates.inc\"; input float[64] theta; qubit[1] q; ry(theta) q[0];",
  "bindings": {
    "theta": 1.0471975511965976
  },
  "bitstrings": [
    "0",
    "1"
  ],
  "bit_order": "big"
}
```

QASM3 requires an explicit operation. Sampling uses the versioned
`qasm-samples/1` result and a complete `classical_memory` NPZ array. See the
guides below for ordering, syntax restrictions and request examples.

See the [developer guide](qsim/static/index.html), [agent API guide](qsim/static/agents.md),
and [deployment guide](docs/deployment.md). Example addresses use reserved domains
and must be configured for your installation.

## Classical conditions with Qiskit Aer

Select `"engine": "aer"` and `"operation": "sample"` on `/api/jobs/qasm2` or `/api/jobs/qasm3`. Check `/api/capabilities` first. Aer 0.17.2 runs CPU statevector simulation in double precision, imported by Qiskit 2.6.0 and QASM3 importer 0.6.0. This is an explicit engine choice; requests never switch simulators automatically.

QASM2 accepts whole-register equality to an in-range integer followed by one standard gate statement, including register broadcast. QASM3 accepts a scalar bit compared with `true`/`false`, or a whole bit array compared with an in-range integer, followed by flat `if`/`else` blocks. Those blocks may contain standard gates, measurements and reset. Use `if(c==true)` for a scalar QASM3 bit; the selected importer does not accept `if(c==1)` for that type.

Classical bits start at zero. Conditions see the latest write; skipped measurements preserve the destination's previous value. Register integer significance follows OpenQASM: bit zero is the least significant bit. The result uses the existing `qasm-samples/1` contract, with declaration-order registers and ascending bit indices, full final memory in NPZ, and bounded JSON counts/preview. Each shot starts fresh. Both examples below return `{"1":128}`.

Send this example to `POST /api/jobs/qasm2`:

<!-- example: aer-qasm2 -->
```json
{
  "engine": "aer",
  "operation": "sample",
  "name": "Conditional correction",
  "circuit": "OPENQASM 2.0; include \"qelib1.inc\"; qreg q[1]; creg c[1]; if(c==0) x q; measure q->c;",
  "shots": 128,
  "seed": 42
}
```

Send this example to `POST /api/jobs/qasm3`:

<!-- example: aer-qasm3 -->
```json
{
  "engine": "aer",
  "operation": "sample",
  "name": "Conditional branches",
  "circuit": "OPENQASM 3.0; include \"stdgates.inc\"; qubit q; bit c; if(c==false) { x q; } else { reset q; } c=measure q;",
  "shots": 128,
  "seed": 42
}
```

Aer currently rejects probability queries, Stim input, custom gate declarations, nonempty parameter bindings, nested conditions, loops, classical arithmetic/assignment, aliases/slices, timing/pulse instructions and external includes. Existing qsim custom-gate/binding capabilities remain available with `engine="qsim"`.

Aer standard gate names: `id`, `x`, `y`, `z`, `h`, `s`, `sdg`, `t`, `tdg`, `sx`, `sxdg`, `rx`, `ry`, `rz`, `p`, `u1`, `u2`, `u3`, `u`, `U`, `cx`, `CX`, `cy`, `cz`, `ch`, `swap`, `cp`, `crx`, `cry`, `crz`, `rxx`, `ryy`, `rzz`, `ccx`, `cswap`, where supplied by the selected format's standard include. Arguments must be finite numeric literals or supported constant expressions using `pi`, `tau`, `e` and `+`, `-`, `*`, `/`; no exponent or function expressions. Native import may reject constants/gate spellings absent from that format's standard library. Declarations must precede operations and use literal sizes.

Aer shares the 26-declared-qubit ceiling and 3 GiB process limit. It uses one native CPU thread within the existing worker profile. The operation budget counts both branches, and the sampling-cell budget includes all possible measurement events, even when skipped. No active-job deadline or fixed concurrency cap is added. This API returns complete samples, not incremental sample streaming.

Upstream: [Qiskit Aer](https://github.com/Qiskit/qiskit-aer) and [QASM3 importer](https://qiskit.github.io/qiskit-qasm3-import/).

## Dev bootstrap

Python 3.12 on Linux AMD64 is the supported target. Install
[uv](https://docs.astral.sh/uv/) and Node.js 22; dependencies are locked in `uv.lock`.

```sh
uv sync --frozen --extra worker --extra controller
bash tools/ci/check.sh
```

The checks run all three native simulator engines, API authorization and idempotency,
SSE replay, worker lifecycle logic, and JavaScript console tests. They do not
launch cloud or Kubernetes resources. See [CI](docs/ci.md) for local and hosted lanes.

## Run the API

```sh
uv run python scripts/invite.py developer --output var/developer.key
uv run uvicorn qsim.api:app --host 127.0.0.1 --port 8080
```

The API alone queues jobs. Execution requires a configured SkyPilot controller
and a worker image reachable by the cluster. Do not start the controller until
its deployment settings are supplied.

Submit with your invitation key in the `Authorization: Bearer` header.
`POST /api/jobs/stim`, `/api/jobs/qasm2` and `/api/jobs/qasm3` require an `Idempotency-Key`.
Reusing a key with the same normalized body returns the original job;
a changed request returns HTTP 409.

`GET /api/jobs/{id}/events` streams durable progress and the final result summary.
Resume using `Last-Event-ID`. Download the NumPy archive from
`GET /api/jobs/{id}/result`. Streaming does not provide incremental samples.
Clients manage batches, dependencies, and quantum state between requests.

## Architecture

- The API persists owner-scoped jobs, idempotency records, and events in SQLite.
- A single controller submits jobs through SkyPilot launch/exec and reuses
  compatible idle workers. Capacity failures queue and retry.
- Each simulation runs in a fresh bounded subprocess. Circuit input is data.
- An optional private operator console handles invitation review and resource
  observations. It trusts its protected network; it must not be publicly exposed.

The current worker profile requests 2 CPUs and 4 GiB. Simulation subprocesses
have a 3 GiB address-space limit and no active wall-time or CPU-time deadline. Clifft supports active
width up to 22; Google qsim and Aer support up to 26 declared qubits. There is no fixed
active-job or saved-job count cap. New QASM sampling bounds the combined final
classical-memory and native measurement-event cells to 1,000,000. Custom gates
are limited to 65,536 expanded operations and nesting depth 32. Exponent `^`
expressions are rejected on the new paths. Completed jobs expire after 30 days.

## Operations and configuration

Keep deployment addresses, credentials, kubeconfigs, generated manifests, and
operational reports in ignored `local/` or `var/` directories. The repository
contains source and example configuration only, with no live deployment inventory.

Use [the deployment guide](docs/deployment.md) for settings, public/private route
boundaries, and backup considerations. See [CONTRIBUTING.md](CONTRIBUTING.md) for
checks before submitting changes and [SECURITY.md](SECURITY.md) for reporting issues.

## License and acknowledgments

This service uses the [MIT license](LICENSE). Dependencies retain their own
licenses. Thanks to [Unitary Foundation's Clifft](https://github.com/unitaryfoundation/clifft),
[Google qsim](https://github.com/quantumlib/qsim) (Apache 2.0),
[Qiskit Aer](https://github.com/Qiskit/qiskit-aer) (Apache 2.0),
[Cirq](https://github.com/quantumlib/Cirq), and [SkyPilot](https://github.com/skypilot-org/skypilot).
