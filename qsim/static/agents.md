# wqsim API guide for agents

Base URL: https://simulation.example.invalid
Developer guide: https://simulation.example.invalid/
This guide: https://simulation.example.invalid/agents.md
Guide updated: 2026-11-08. Check `GET /api/capabilities` on your installation before selecting a new operation.

## Scope and credentials

wqsim runs independent CPU circuit and physical-model jobs. Circuit engines are Clifft 0.12.0, Google qsim 0.22.1 and Qiskit Aer 0.17.2. QuTiP, scqubits and JoSIM provide the gated physics matrix described below. The caller manages call sequences, batches, dependencies, and quantum state between requests. Submitting a job does not create a persistent quantum session.

Use an API key supplied by the user or their secret store:

```http
Authorization: Bearer <API_KEY>
```

Use HTTPS. Never place keys in URLs, generated documentation, or logs. Jobs belong to an API identity; keys for another identity cannot read them. No login or cookies are needed for the job API.

If no key is available, direct the user to https://simulation.example.invalid/#keys. Access requires approval; submitting a request does not issue a key. Do not repeatedly submit access requests to obtain credentials.

## Choose the input and operation

| Engine | Input / endpoint | Operation | Required JSON fields |
| --- | --- | --- | --- |
| Clifft (default) | Stim, `POST /api/jobs/stim` | Sampling | `circuit` |
| Clifft or Google qsim | QASM2, `POST /api/jobs/qasm2` | Basis probabilities (default) | `circuit`, `bitstrings` |
| Google qsim | QASM2, `POST /api/jobs/qasm2` | Sampling | `engine:"qsim"`, `operation:"sample"`, `circuit` |
| Google qsim | QASM3, `POST /api/jobs/qasm3` | Sampling or basis probabilities | `engine:"qsim"`, explicit `operation`, `circuit`; `bitstrings` for probabilities |
| Qiskit Aer | QASM2 or QASM3, corresponding format endpoint | Conditional sampling | `engine:"aer"`, `operation:"sample"`, `circuit` |

All explicit circuit and physics submission endpoints require `Content-Type: application/json` and `Idempotency-Key`. Clifft remains the default engine; explicitly select qsim or Aer for supported QASM operations. Unsupported engine combinations fail rather than switch engines.

The existing QASM2 probability operation remains unitary-only for both engines: no classical registers, measurement, reset, conditions or custom gate definitions. Google qsim QASM2 sampling and QASM3 use a broader tested Cirq subset described below. Never remove measurements to turn a sampling request into a probability query.

### Stim request

```http
POST /api/jobs/stim
Authorization: Bearer <API_KEY>
Content-Type: application/json
Idempotency-Key: <PERSISTED_REQUEST_UUID>
```

```json
{
  "name": "Bell pair",
  "circuit": "H 0\nCX 0 1\nM 0 1",
  "shots": 1000,
  "seed": 42
}
```

`name` defaults to `Untitled circuit`. `shots` defaults to 1000 and `seed` to 42. The result archive contains `measurements`, `detectors`, `observables`, and `exp_vals`. The summary includes counts and a sample preview. Counts are limited to the 256 most common outcomes; inspect `counts_truncated` and use the full archive when necessary.

### OpenQASM 2 request

```http
POST /api/jobs/qasm2
Authorization: Bearer <API_KEY>
Content-Type: application/json
Idempotency-Key: <PERSISTED_REQUEST_UUID>
```

```json
{
  "name": "Bell probabilities",
  "circuit": "OPENQASM 2.0; include \"qelib1.inc\"; qreg q[2]; h q[0]; cx q[0],q[1];",
  "bitstrings": ["00", "01", "10", "11"],
  "bit_order": "big"
}
```

The expected probabilities are `[0.5, 0, 0, 0.5]`. The summary and NPZ archive contain `bitstrings` and `probabilities` in the requested order. These are probabilities of the selected strings, not a renormalized distribution over the selection.

Each bitstring must contain only `0` and `1` and match the circuit's qubit count. All strings must have equal length. `bit_order="big"` maps the first character to qubit 0; `"little"` maps the last character to qubit 0. The default is `big`. The probability operation does not accept `shots` or `seed`.

## Google qsim engine

On `POST /api/jobs/qasm2`, set `"engine":"qsim"` to select Google qsim 0.22.1 via Cirq. Omitted `engine` or `"engine":"clifft"` selects Clifft. Changing the engine requires a new idempotency key. The general `/api/jobs` endpoint also accepts this engine with `input_format="qasm2"` and `operation="basis_probabilities"`.

The existing QASM2 probability path accepts the same circuit, bitstrings and bit order shown above. It does not accept classical registers or custom gates. qsim supports 1–26 declared qubits, including idle qubits, in declaration order with numeric indices. It uses single-precision state-vector simulation: selected probabilities come from amplitudes, with floating-point error but no shot noise. `summary.engine` identifies the version; probability results report `precision="single"`.

### QASM2 sampling

Send this body to `POST /api/jobs/qasm2`. Explicitly select `sample`; omitted operation still means basis probabilities.

<!-- example: qasm2-sample -->
```json
{
  "engine": "qsim",
  "operation": "sample",
  "name": "Classical bit placement",
  "circuit": "OPENQASM 2.0; include \"qelib1.inc\"; qreg q[1]; creg c[3]; x q[0]; measure q[0] -> c[1];",
  "shots": 128,
  "seed": 42
}
```

Expected counts: `{"010":128}`. Supported sampling includes terminal and mid-circuit measurement, reset, repeated classical writes, and bounded custom gates. Each shot starts fresh. Classical conditions remain unsupported, including a branch that would not execute.

### QASM3 sampling and parameter binding

Send this body to `POST /api/jobs/qasm3`. This endpoint requires an explicit operation. The example binds a custom gate rotation and samples `10` on every shot.

<!-- example: qasm3-sample -->
```json
{
  "engine": "qsim",
  "operation": "sample",
  "name": "Bound custom gate",
  "circuit": "OPENQASM 3.0; include \"stdgates.inc\"; input angle[64] theta; gate turn(a) t { rx(a) t; } qubit[2] q; bit[2] c; turn(theta) q[0]; c = measure q;",
  "bindings": {
    "theta": 3.141592653589793
  },
  "shots": 128,
  "seed": 42
}
```

Supported input declarations are `float[32]`, `float[64]`, `angle[32]` and `angle[64]`. Supply exactly the declared names in `bindings`, using finite JSON numbers. Submission accepts at most 256 bindings, with names of 1–256 characters; oversized mappings are rejected before a job is stored. Missing or extra names, booleans, strings, null, infinities and NaN are rejected. Values normalize to finite Python floats; declaration widths do not promise width-specific arithmetic, and qsim still runs in single precision. Equivalent integer/float values and signed zero share normalized identity. Changed binding values require a new idempotency key. QASM2 has no `bindings` field; custom gate-local parameters there must resolve from circuit expressions.

### QASM3 basis probabilities

Send this unitary request to `POST /api/jobs/qasm3`. Expected probabilities are approximately `[0.75,0.25]`, with absolute tolerance `1e-6` for this example.

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

QASM3 probability queries accept supported custom gates and unused classical declarations. Measurement, reset and conditions are rejected; samples are never substituted for probabilities. Do not include `shots` or `seed` in probability requests, or `bitstrings`/`bit_order` in QASM sample requests.

### QASM result and syntax contracts

Sampling returns `result_schema="qasm-samples/1"`. The NPZ contains `classical_memory`, a `uint8` array shaped `[shots,total_classical_bits]`. `summary.registers` lists `{name,size,offset}` in declaration order; each register uses ascending bit indices. Count keys follow that same left-to-right column order. Unwritten bits remain zero and the last measurement written to a bit supplies its final value. These are final classical values, not measurement-event history.

The complete artifact retains all shots. JSON retains at most 256 counts, sorted by descending count with lexicographic ties, and at most the first 20 shots in `preview`. Each collection has a 512 KiB JSON budget; only complete count keys and shot rows are included. Inspect `counts_truncated` and `preview_truncated`, including when a collection is empty for a very wide register. Counts can be reconstructed from the artifact. A sampling circuit must contain a measurement.

The broader qsim paths use Cirq 1.7.0. Includes are limited to packaged `qelib1.inc` and `stdgates.inc`; file/network includes, executable Python, conditions and unsupported control flow are rejected. On these new paths, exponent expressions using `^` are rejected. Custom gates are bounded by 65,536 expanded operations and nesting depth 32. Numeric literals must be finite with absolute value at most `1e100`; derived gate values must also remain valid and finite. Unresolved parameters are rejected.

Query capabilities for installation-specific availability. Progress/final-result SSE and NPZ downloads use the same durable job lifecycle for every engine. Incremental samples remain unavailable. Project and license: https://github.com/quantumlib/qsim (Apache 2.0).

## Durable submission and safe retries

1. Generate a new idempotency key for each intended job. Persist the key and exact request body before sending.
2. Submit the request. HTTP `202` returns a job record containing `id` and `status`. Save the ID. `Location` gives its status URL; `Idempotency-Replayed` is `false` for new work and `true` for a replay.
3. If the response is lost or times out, retry the same endpoint, body, and idempotency key. Do not generate a new key for a transport retry.

Idempotency keys are scoped to the API identity. Allowed characters are ASCII letters, digits, `.`, `_`, `:`, and `-`; length is 1–128. Reusing a key with the same normalized request returns the same job. Reusing it with a changed request returns `409`. Explicit default values and omitted defaults are normalized; circuit text is not semantically normalized.

Acceptance means durable queueing. Compilation and execution may subsequently fail. Retrying a failed or cancelled job with the same key returns that job; intentionally running it again requires a new key.

Reservations persist while the job exists and for at least 30 days from submission. During that interval, retrying a deleted job returns `410`. After reservation expiry an old key can create new work. Keep client retry windows within the reservation guarantee. Submission deduplication does not guarantee successful or exactly-once remote execution after every possible failure.

On `429`, back off and retry the same intended request with the same key. On transient `5xx` or network errors, use bounded exponential backoff. Do not automatically create replacement jobs for `409`, `410`, or terminal failures.

## Watch progress

Poll `GET /api/jobs/{id}` every few seconds, or use the event stream below. Every request requires bearer authentication.

State progression is normally `QUEUED` → `LAUNCHING` → `RUNNING` → `SUCCEEDED`. `FAILED` and `CANCELLED` are also terminal states. A job may fail or be cancelled before reaching every intermediate state. Inspect `error` for failures. Terminal success includes `summary`.

A client timeout or closed stream does not cancel a job. Explicitly call `POST /api/jobs/{id}/cancel` when cancellation is intended. Cancellation is recorded immediately; resource cleanup can finish later.

### Resumable server-sent events

```http
GET /api/jobs/<JOB_ID>/events
Authorization: Bearer <API_KEY>
Last-Event-ID: <LAST_PROCESSED_SEQUENCE>
```

Omit `Last-Event-ID` to replay all retained events. The response uses `text/event-stream`. Parse complete SSE frames, ignoring comment lines beginning with `:`. Each persisted event has a per-job integer `id` and JSON `data`.

| Event | Meaning |
| --- | --- |
| `status` | A queued, launching, or running job snapshot. |
| `result` | Success: final job summary and a relative authenticated `result_url`. Download the complete arrays separately. |
| `terminal` | Failure or cancellation; inspect `status` and `error`. |
| `reconnect` | Reopen the same stream using the last processed event ID. No new job is required. |
| `error` | Stream-level problem, such as a revoked key or deleted job; inspect its code and resolve it before reconnecting. |

Persist the cursor only after processing an event. Deduplicate by `(job_id, event_id)` if application processing may repeat. Resume after a disconnect with `Last-Event-ID`; acknowledged events are excluded. A cursor beyond the job's latest sequence returns `400`.

Streams send idle heartbeat comments and close at completion or after at most five minutes. A slow or broken connection may close without a final control event. Reconnect with the saved cursor. A completed stream resumed after its final event closes without replaying that event. Job deletion removes its event history. Older jobs migrated into event tracking may have only an initial current-state snapshot.

**Incremental live measurement samples are unavailable for all engines.** This API streams progress and the final result summary; it does not reseed smaller sample batches to imitate streaming. Query `GET /api/capabilities` for current support.

## Retrieve and retain results

After success, `GET /api/jobs/{id}/result` returns a NumPy `.npz` archive. Supply the bearer header even when following `result_url`. Load it with `numpy.load(..., allow_pickle=False)`.

A result URL is relative to the wqsim base URL and is not a signed anonymous download. Save artifacts that must outlive service retention. Completed jobs expire after 30 days. `DELETE /api/jobs/{id}` removes a terminal job after its compute cleanup finishes; an early delete returns `409`.

## Endpoint reference

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/api/me` | Authenticated identity and engine. |
| GET | `/api/capabilities` | Supported formats, operations, and streaming capabilities. |
| POST | `/api/jobs/stim` | Durable Stim sampling submission; idempotency key required. |
| POST | `/api/jobs/qasm2` | Basis probabilities by default; explicit qsim or Aer sampling also supported. Idempotency key required. |
| POST | `/api/jobs/qasm3` | qsim probabilities/sampling or Aer conditional sampling; explicit operation and idempotency key required. |
| POST | `/api/jobs` | General submission. Optional idempotency key for compatibility; without it, each call creates new work. |
| GET | `/api/jobs` | All saved jobs for this identity. |
| GET | `/api/jobs/{id}` | Job status, error, and completed summary. |
| GET | `/api/jobs/{id}/events` | Resumable SSE stream. |
| GET | `/api/jobs/{id}/result` | Successful job's NPZ archive. |
| POST | `/api/jobs/{id}/cancel` | Record cancellation; returns 202. |
| DELETE | `/api/jobs/{id}` | Delete a terminal, cleaned job; returns 204. |

For circuit submissions, the `engine` field accepts `clifft` (default), `qsim`, or `aer`; Aer supports QASM2/QASM3 sampling only. The general submission route accepts `input_format` (`stim`, `qasm2` or `qasm3`) and `operation` (`sample` or `basis_probabilities`). Defaults are `stim` and `sample`. Probability queries require `bitstrings` and optionally `bit_order`. QASM3 requires an explicit operation even on the general route. QASM sampling requires explicit `engine="qsim"` or `engine="aer"`. Prefer the explicit format endpoints for new clients. Unknown JSON fields are rejected.

## Limits and errors

- Circuit text: 1–65,536 characters; job name: 1–80 characters.
- Sampling: 1–10,000 shots; integer seed from 0 through 9,223,372,036,854,775,807.
- Probability queries: 1–256 bitstrings, each at most 4,096 characters.
- Clifft limits: at most 4,096 qubits, active width 22, and 1,000,000 sampled output cells.
- New QASM sampling: `shots × (total classical bits + measurement events)` must not exceed 1,000,000; repeated writes count as separate native events even though only the final value is returned.
- Google qsim: 1–26 declared qubits, two CPU threads, single precision. All engines use a 2 CPU / 4 GiB worker with a 3 GiB process memory limit.
- Execution: simulations run concurrently as cluster capacity permits. Placement failures stay queued and retry automatically. Idle compute is reused for subsequent requests, with a fresh simulation process for each job. Cold starts still add latency. Active simulations have no elapsed-time or CPU-time deadline. Explicit cancellation remains available; idle cleanup waits for work to finish. Queue wait can add time.
- Per identity: no saved-job cap; eight open event streams. No fixed active-job concurrency limit.
- Service totals: 64 event streams. No fixed saved-job or idempotency-history count cap.
- Post-selection and experimental GPU execution are not exposed.

| HTTP status | Client action |
| --- | --- |
| 202 | Save the accepted job ID; watch its state. |
| 400 | Correct the event cursor. |
| 401 | Obtain a valid API key; do not loop on a revoked key. |
| 404 | Verify the job ID and API identity. |
| 409 | Inspect the message: idempotency conflict, unavailable result, or cleanup still pending. |
| 410 | The reserved job has been deleted or expired; decide explicitly whether new work is intended. |
| 413 | Reduce the request size. |
| 422 | Correct fields or format-specific requirements. |
| 429 | Respect capacity limits and back off. |
| 503 | Retry transient service unavailability with bounded backoff. |

Envelope errors reject submission with HTTP 422. Circuit parsing, binding and native execution occur after acceptance; failures become FAILED jobs with safe prefixes such as `invalid_circuit`, `invalid_binding`, `unsupported_feature`, `resource_limit` or `execution_failed`. Do not treat HTTP 202 as successful simulation.

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


## Physics-model API (schema v1)

Physics routes are implemented for the CPU matrix below. Production activation is gated by exact-release acceptance. Check authenticated `GET /api/capabilities` on your deployment; an omitted engine is unavailable, and new physics submissions return `503` while activation is off. A source merge or image build does not establish production availability.

The physics matrix is CPU-only, float64/complex128, with no GPU profile or automatic backend substitution. Check capabilities before new work. The caller owns sequences, sweeps and state handoffs. No Python, symbolic coefficient strings, native options or resource selectors are accepted.

| Engine | Submit | Generic input format | Authenticated schema |
| --- | --- | --- | --- |
| QuTiP 5.3.1 Lindblad | `POST /api/jobs/lindblad` | `lindblad_model` | `GET /api/schemas/lindblad/1.json` |
| scqubits 4.3.1 | `POST /api/jobs/scqubits` | `scqubits_model` | `GET /api/schemas/scqubits/1.json` |
| Patched JoSIM v2.7 | `POST /api/jobs/josim` | `josim_netlist` | `GET /api/schemas/josim/1.json` |

Use an integer `schema_version: 1`, explicit operation and saved idempotency key.
The generic route uses matching engine/format fields and retains an optional
idempotency header; explicit routes require it. Structural schemas are
necessary but physical/resource checks still apply. Names default to
`Untitled simulation`. Physics requests have no `shots` field.

### Lindblad operation matrix

| Operation | Solver | Use it for | Numeric NPZ arrays |
| --- | --- | --- | --- |
| `evolve` | `se` | Pure-state Schrödinger evolution without dissipation | `times`, optional `expectations`, optional `state_times`/`states` |
| `evolve` | `me` | Lindblad density-matrix evolution with or without collapse channels | Same names; retained `states` are density matrices |
| `evolve` | `mc` | Seeded serial quantum-jump trajectories with a pure input and nonzero collapse | Same names; retained `states` are ensemble density matrices |
| `propagator` | `se` | Hilbert-space propagators | `times`, `propagators` |
| `propagator` | `me` | Density/Liouville propagators | `times`, `propagators` |
| `spectrum` | Omit solver | Static Hamiltonian energies and optional eigenvectors | `eigenvalues`, optional `eigenvectors` |
| `steadystate` | Omit solver | A unique stationary density matrix and optional observables | Optional `steady_state`, optional `expectations` |

Time and T1/Tphi are ns, Hamiltonian/frequency values are linear GHz and phases are radians. The service converts GHz by `2*pi` once; collapse rates are `1/ns`. Finite complex values use numbers or `{"re":...,"im":...}`. Modes are oscillator, duffing or explicit Hermitian matrix; order follows `model.modes`. JSON drive envelopes are constant, square, gaussian, flattop_cosine or linearly sampled; DRAG and positive square rise are absent in v1.

SE rejects dissipation and mixed inputs. ME preserves density-state semantics even without collapse. MC requires pure input and nonzero collapse; seed defaults to 42 and 1–1,000 trajectories run serially. Seed/trajectories apply only to MC. Repeated seeds on the same pinned image reproduce the tested result; no cross-version/backend bitwise promise is made. State output is controlled by `store_states=none|final|all`: SE `[R,D]`, ME/MC ensemble density `[R,D,D]`, with `state_times`. Propagators have `[T,D,D]` or `[T,D²,D²]` shape, begin at identity and act from the first requested time; ME uses column-stacked density. Spectra return GHz eigenvalues and optional vectors; nonunique stationary solutions fail.

POST this body to `/api/jobs/lindblad`; expectation is one and phase is `exp(-2*pi*i*0.017*t)`:

<!-- example: physics-lindblad-evolve-se -->
```json
{
  "engine": "lindblad",
  "input_format": "lindblad_model",
  "schema_version": 1,
  "operation": "evolve",
  "model": {
    "modes": [
      {
        "name": "q",
        "kind": "oscillator",
        "dimension": 2,
        "frequency": 0.017
      }
    ]
  },
  "solver": "se",
  "times": [
    0.0,
    0.5,
    1.0
  ],
  "initial_state": {
    "kind": "product",
    "modes": {
      "q": {
        "kind": "fock",
        "index": 1
      }
    }
  },
  "observables": [
    {
      "name": "n",
      "operator": {
        "kind": "product",
        "factors": {
          "q": "n"
        }
      }
    }
  ],
  "store_states": "all"
}
```

The developer page's physics section contains marked examples for ME/MC evolution, both propagators, spectrum and steady state. Derive new bodies using `/api/schemas/lindblad/1.json`; do not add controls from a different operation.

### scqubits and numeric handoff

Supported classes are `Transmon`, `TunableTransmon`, `Fluxonium`, `FluxQubit`. `spectrum` returns ascending GHz `eigenvalues` and optional eigenvectors; `matrix_elements` adds a complex square `matrix_elements` for one allowed operator; `export_for_lindblad` returns shifted energies, diagonal Hamiltonian and selected operators in one shared eigensystem. Use strict numeric class fields from `/api/schemas/scqubits/1.json`. Cutoff convergence is the client's responsibility. Charge offsets and flux fractions are dimensionless; phi operators use radians.

POST to `/api/jobs/scqubits`:

<!-- example: physics-scqubits-export -->
```json
{
  "engine": "scqubits",
  "input_format": "scqubits_model",
  "schema_version": 1,
  "operation": "export_for_lindblad",
  "evals_count": 4,
  "model": {
    "qubit": "Transmon",
    "params": {
      "EJ": 15.0,
      "EC": 0.3,
      "ng": 0.0,
      "ncut": 30
    }
  },
  "operators": [
    "n_operator"
  ]
}
```

Read the completed NPZ with `allow_pickle=False`, parse scalar Unicode `metadata_json`, and use its `lindblad_request` for a separate job with a new key. Preserve the exported operator basis. Removed ground energy is recorded in metadata; do not multiply the Hamiltonian by `2*pi` again. Up to six ordered supported operators may be exported, including an empty list. Export JSON's metadata budget may refuse a level count accepted for a spectrum.

### JoSIM transient subset

The service uses patched JoSIM v2.7, not every upstream JoSIM feature. Accept only self-contained numeric R/L/B circuits, DC/PWL current/voltage sources, one positive numeric JJ model with RTYPE=1, one `.TRAN`, explicit unique `.PRINT DEVV|DEVI|PHASE` selections and terminal `.END`. No includes, subcircuits, expressions, noise, external paths or arbitrary options. Output `times` are SI seconds and `traces` follow declared order; `trace_names` and `trace_units` give volts/amperes/radians. Preserve actual row counts without padding.

POST the tested MIT-licensed SFQ fixture to `/api/jobs/josim`:

<!-- example: physics-josim-sfq -->
```json
{
  "engine": "josim",
  "input_format": "josim_netlist",
  "schema_version": 1,
  "operation": "transient",
  "netlist": "* Source: https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/test/ex_jtl_basic.cir\n* MIT License\n* \n* Copyright (c) 2020 Johannes Delport\n* \n* Permission is hereby granted, free of charge, to any person obtaining a copy\n* of this software and associated documentation files (the \"Software\"), to deal\n* in the Software without restriction, including without limitation the rights\n* to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n* copies of the Software, and to permit persons to whom the Software is\n* furnished to do so, subject to the following conditions:\n* \n* The above copyright notice and this permission notice shall be included in all\n* copies or substantial portions of the Software.\n* \n* THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n* IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n* AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n* LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n* OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n* SOFTWARE.\n* Fixed single-pulse JTL fixture adapted from JoSIM v2.7 ex_jtl_basic (MIT).\nB01        3          7          jmitll     area=2.16\nB02        6          8          jmitll     area=2.16\nIB01       0          1          pwl(0      0 5p 280u)\nL01        4          3          2p        \nL02        3          2          2.425p    \nL03        2          6          2.425p    \nL04        6          5          2.031p    \nLP01       0          7          0.086p    \nLP02       0          8          0.096p    \nLPR01      2          1          0.278p    \nLRB01      7          9          0.086p    \nLRB02      8          10         0.086p    \nRB01       9          3          5.23      \nRB02       10         6          5.23      \nROUT       5          0          2         \nVIN        4          0          pwl(0 0 100p 0 102.5p 827.13u 105p 0)\n.model jmitll jj(rtype=1, vg=2.8mV, cap=0.07pF, r0=160, rN=16, icrit=0.1mA)\n.tran 0.25p 250p\n.print DEVV B01\n.print PHASE B01\n.print DEVV B02\n.print PHASE B02\n.print DEVI ROUT\n.end\n"
}
```

### Shared physics boundaries

The CPU profile requests 2 CPUs and 4 GiB, with a fresh nonroot numerical child per job, a 3-GiB address-space limit and 12-MiB output-file limit. Physics native threads are serial. Admission accounts for native workspace and retained inputs/results before execution; charged allocation must fit 2 GiB, with 512 MiB reserved within that charge for imports/native overhead. Passing a dimension ceiling alone does not guarantee admission.

| Boundary | Current schema-v1 envelope |
| --- | --- |
| HTTP body / name | 16 MiB / 1–80 characters; physics name defaults to `Untitled simulation` |
| Lindblad model | At most 8 ordered modes, total Hilbert dimension 256; dense ME/steady-state dimension at most 32 |
| Lindblad controls | At most 256 static terms, 32 named matrices, 32 observables, 16 effective collapse channels and 16 scalar drive channels |
| Lindblad grids / MC | At most 65,536 requested times/combined coefficient knots; 1–1,000 serial MC trajectories |
| scqubits | Raw basis at most 512, returned levels at most 32; export JSON/array limits may admit fewer levels |
| JoSIM | 1–65,536 ASCII bytes, 256 logical statements, 32 devices, 16 node identifiers, at most 8 unique selected traces and 4,096 predicted output rows |
| JoSIM transient | `0 < step <= stop <= 1e-6` seconds; DC/PWL sources, at most 64 PWL time/value pairs |
| Physics output | At most 6 MiB numeric arrays, 64 KiB UTF-8 metadata, planned NPZ at most 9 MiB and complete callback at most 12 MiB |

There is no new elapsed-time/CPU-time deadline, global job cap or client resource override. Jobs queue when compatible capacity is unavailable. GPU execution is not enabled for this release. Installed CUDA packages or a GPU present in the cluster do not change that matrix.

Structural, finite-value, physical and allocation refusals return `422` before creating a job. New physics errors include `detail[].pointer`, an escaped RFC 6901 JSON Pointer; an empty pointer refers to the whole request. `413` rejects an oversized HTTP body. `401`/`404` retain authentication and owner isolation. `503` means physics is not enabled, release maintenance holds new work, or service configuration is unavailable. Check the response before deciding whether to retry.

After acceptance, inspect terminal status/error. Bounded prefixes include `physical_validation`, `invalid_model`, `resource_limit`, `execution_policy_mismatch`, `native_nonconvergence`, `nonunique_stationary_state`, `invalid_result`, `native_failure` and `execution_failed`. A solver failure does not produce partial success. For an otherwise nonterminal job, acknowledged native completion without a committed final callback becomes a bounded failure instead of silently rerunning computation. Existing terminal states remain immutable. `202` alone is not successful simulation.

Reuse the existing durable polling/SSE/cancellation/deletion flow. SSE gives
progress and the final result summary, not partial samples/trajectories/traces.
The result's `metadata_json` identifies versions, units, backend and work.
Matching retries replay before changed current policy; do not turn a failed job
into new work unless the user intends a new submission/key.
