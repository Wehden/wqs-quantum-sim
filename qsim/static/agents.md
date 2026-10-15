# wqsim API guide for agents

Base URL: https://simulation.example.invalid
Developer guide: https://simulation.example.invalid/
This guide: https://simulation.example.invalid/agents.md
Guide updated: 2026-10-14. Check `GET /api/capabilities` on your installation before selecting a new operation.

## Scope and credentials

wqsim runs independent Clifft 0.11.0, Google qsim 0.22.1 and Qiskit Aer 0.17.2 CPU simulation jobs. The caller manages call sequences, batches, dependencies, and quantum state between requests. Submitting a job does not create a persistent quantum session.

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

All three format endpoints require `Content-Type: application/json` and `Idempotency-Key`. Clifft remains the default engine; explicitly select qsim or Aer for supported QASM operations. Unsupported engine combinations fail rather than switch engines.

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
| POST | `/api/jobs/qasm2` | Basis probabilities by default; explicit qsim sampling also supported. Idempotency key required. |
| POST | `/api/jobs/qasm3` | qsim subset with explicit operation; idempotency key required. |
| POST | `/api/jobs` | General submission. Optional idempotency key for compatibility; without it, each call creates new work. |
| GET | `/api/jobs` | All saved jobs for this identity. |
| GET | `/api/jobs/{id}` | Job status, error, and completed summary. |
| GET | `/api/jobs/{id}/events` | Resumable SSE stream. |
| GET | `/api/jobs/{id}/result` | Successful job's NPZ archive. |
| POST | `/api/jobs/{id}/cancel` | Record cancellation; returns 202. |
| DELETE | `/api/jobs/{id}` | Delete a terminal, cleaned job; returns 204. |

The general submission route accepts `input_format` (`stim`, `qasm2` or `qasm3`) and `operation` (`sample` or `basis_probabilities`). Defaults are `stim` and `sample`. Probability queries require `bitstrings` and optionally `bit_order`. QASM3 requires an explicit operation even on the general route. QASM sampling requires explicit `engine="qsim"` or `engine="aer"`. Prefer the explicit format endpoints for new clients. Unknown JSON fields are rejected.

## Limits and errors

- Circuit text: 1–65,536 characters; job name: 1–80 characters.
- Sampling: 1–10,000 shots; integer seed from 0 through 9,223,372,036,854,775,807.
- Probability queries: 1–256 bitstrings, each at most 4,096 characters.
- Clifft limits: at most 4,096 qubits, active width 22, and 1,000,000 sampled output cells.
- New QASM sampling: `shots × (total classical bits + measurement events)` must not exceed 1,000,000; repeated writes count as separate native events even though only the final value is returned.
- Google qsim: 1–26 declared qubits, two CPU threads, single precision. Both engines use a 2 CPU / 4 GiB worker with a 3 GiB process memory limit.
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

Select `"engine": "aer"` and `"operation": "sample"` on `/api/jobs/qasm2` or `/api/jobs/qasm3`. Check `/api/capabilities` first. Aer 0.17.2 runs CPU statevector simulation in double precision, imported by Qiskit 2.5.2 and QASM3 importer 0.6.0. This is an explicit engine choice; requests never switch simulators automatically.

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
