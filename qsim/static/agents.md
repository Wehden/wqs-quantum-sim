# wqsim API guide for agents

Base URL: https://simulation.example.invalid
Developer guide: https://simulation.example.invalid/
This guide: https://simulation.example.invalid/agents.md
API contract: 0.3. Updated: 2026-10-11.

## Scope and credentials

wqsim runs independent Clifft 0.11.0 and Google qsim 0.22.1 CPU simulation jobs. The caller manages call sequences, batches, dependencies, and quantum state between requests. Submitting a job does not create a persistent quantum session.

Use an API key supplied by the user or their secret store:

```http
Authorization: Bearer <API_KEY>
```

Use HTTPS. Never place keys in URLs, generated documentation, or logs. Jobs belong to an API identity; keys for another identity cannot read them. No login or cookies are needed for the job API.

If no key is available, direct the user to https://simulation.example.invalid/#keys. Access requires approval; submitting a request does not issue a key. Do not repeatedly submit access requests to obtain credentials.

## Choose the input and operation

| Input | Endpoint | Operation | Required JSON fields |
| --- | --- | --- | --- |
| Stim with supported Clifft extensions | `POST /api/jobs/stim` | Fixed-shot measurement sampling | `circuit` |
| OpenQASM 2 | `POST /api/jobs/qasm2` | Exact probabilities of requested computational-basis strings | `circuit`, `bitstrings` |

Both endpoints require `Content-Type: application/json` and `Idempotency-Key`.

Stim supports measurement instructions in the circuit. OpenQASM 2 supports Clifft's unitary `qelib1.inc` subset. It does not accept classical registers, measurements, resets, conditions, or custom gate definitions. Do not send measurement-bearing QASM to the probability endpoint or silently remove its measurement instructions. Use an appropriate Stim circuit when measurement sampling is required.

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

Each bitstring must contain only `0` and `1` and match the circuit's qubit count. All strings must have equal length. `bit_order="big"` maps the first character to qubit 0; `"little"` maps the last character to qubit 0. The default is `big`. This endpoint does not accept `shots` or `seed`.

## Google qsim engine

On `POST /api/jobs/qasm2`, set `"engine":"qsim"` to select Google qsim 0.22.1 via Cirq. Omitted `engine` or `"engine":"clifft"` selects Clifft. Changing the engine requires a new idempotency key. The general `/api/jobs` endpoint also accepts this engine with `input_format="qasm2"` and `operation="basis_probabilities"`.

Use the same circuit, bitstrings, bit order, and result handling as the QASM example above. qsim supports 1–26 declared qubits (including idle qubits), in declaration order with numeric indices. Its unitary QASM importer is Cirq’s; gate support may differ from Clifft. Classical registers, measurements, reset, conditions, custom gates, Stim input, and executable Python are not accepted. This initial integration exposes basis probabilities only.

qsim is a full state-vector CPU simulator with single-precision arithmetic. Probabilities are computed from amplitudes without sampling, but differ from Clifft at floating-point precision; compare with a suitable numerical tolerance, not bit-for-bit equality. `summary.engine` identifies the engine/version and qsim summaries include `precision="single"`. Both engines use the same durable queue, SSE progress/final summary, and NPZ result download. There is no incremental amplitude or measurement stream.

Project and license: https://github.com/quantumlib/qsim (Apache 2.0).

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

**Incremental live measurement samples are unavailable.** Clifft 0.11's CPU sampler returns complete arrays and exposes no incremental callback. This API streams progress and the final result summary; it does not reseed smaller sample batches to imitate streaming. Query `GET /api/capabilities` for current support.

## Retrieve and retain results

After success, `GET /api/jobs/{id}/result` returns a NumPy `.npz` archive. Supply the bearer header even when following `result_url`. Load it with `numpy.load(..., allow_pickle=False)`.

A result URL is relative to the wqsim base URL and is not a signed anonymous download. Save artifacts that must outlive service retention. Completed jobs expire after 30 days. `DELETE /api/jobs/{id}` removes a terminal job after its compute cleanup finishes; an early delete returns `409`.

## Endpoint reference

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/api/me` | Authenticated identity and engine. |
| GET | `/api/capabilities` | Supported formats, operations, and streaming capabilities. |
| POST | `/api/jobs/stim` | Durable Stim sampling submission; idempotency key required. |
| POST | `/api/jobs/qasm2` | Durable unitary QASM probability query; idempotency key required. |
| POST | `/api/jobs` | General submission. Optional idempotency key for compatibility; without it, each call creates new work. |
| GET | `/api/jobs` | All saved jobs for this identity. |
| GET | `/api/jobs/{id}` | Job status, error, and completed summary. |
| GET | `/api/jobs/{id}/events` | Resumable SSE stream. |
| GET | `/api/jobs/{id}/result` | Successful job's NPZ archive. |
| POST | `/api/jobs/{id}/cancel` | Record cancellation; returns 202. |
| DELETE | `/api/jobs/{id}` | Delete a terminal, cleaned job; returns 204. |

The general submission route accepts `input_format` (`stim` or `qasm2`) and `operation` (`sample` or `basis_probabilities`). Defaults are `stim` and `sample`. Probability queries require `bitstrings` and optionally `bit_order`; QASM requires `basis_probabilities`. Prefer the explicit format endpoints for new clients. Unknown JSON fields are rejected.

## Limits and errors

- Circuit text: 1–65,536 characters; job name: 1–80 characters.
- Sampling: 1–10,000 shots; integer seed from 0 through 9,223,372,036,854,775,807.
- Probability queries: 1–256 bitstrings, each at most 4,096 characters.
- Clifft limits: at most 4,096 qubits, active width 22, and 1,000,000 sampled output cells.
- Google qsim: 1–26 declared qubits, two CPU threads, single precision. Both engines use a 2 CPU / 4 GiB worker with a 3 GiB process memory limit.
- Execution: simulations run concurrently as cluster capacity permits. Placement failures stay queued and retry automatically. Idle compute is reused for subsequent requests, with a fresh simulation process for each job. Cold starts still add latency. The simulation deadline is 300 seconds; each dispatch attempt has a 20-minute deadline. Queue wait can add time.
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

Job-level failures are reported in the job record and event stream, not necessarily as a failing HTTP status on the original submission.
