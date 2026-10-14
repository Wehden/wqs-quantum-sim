# Design: broader circuit support

Status: approved by the owner on 2026-10-13 following System2 persona review. The recorded capability experiments and open decisions remain prerequisites for affected implementation. Proposed capabilities are not implemented or deployed by this approval. Task plan approved on 2026-10-13; execution starts with capability evidence.

Approved amendment (2026-10-14): [classical conditions via an explicit Aer engine](classical-conditions/design.md). Its bounded engine/dependency changes supplement the baseline below. The owner approved [the implementation task plan](classical-conditions/tasks.md) on the same date; execution is in progress.

## Overview

Extend the current data-only circuit service through existing library importers and simulator adapters. Keep durable jobs, SkyPilot placement, warm workers, owner isolation and the default Clifft engine. Do not introduce another simulator as a silent fallback. IBM/Qiskit supplies candidate import/binding tooling and local reference behavior; its hardware support does not establish support in Clifft or Google qsim.

This pass inspected application source and installed library source only. It did not run capability experiments, install dependencies, change application code or update deployment configuration. Capability experiments are required before final integration selection and implementation approval.

## Architecture (components, responsibilities, and boundaries)

### D1: Integration selection

Prefer the installed Cirq importer for Google qsim before adding dependencies. Its parser source accepts QASM 2/3 declarations, custom gates, input declarations, measurements, reset and some conditions. That is parser evidence, not a guarantee that qsim executes those constructs correctly. qsimcirq source contains repeated measurement records and terminal/nonterminal sampling paths. Its handling of classical controls requires an executable probe.

Clifft 0.11.0's installed `compile` documentation describes its QASM2 path as unitary-only. Preserve that path for existing probability requests. Evaluate published Clifft companion integrations with Qiskit/Cirq for wider support; no compatible companion adapter has been selected or installed in this design pass.

| Integration | Existing evidence | Proposed decision |
| --- | --- | --- |
| Clifft 0.11.0 native QASM2 | Service uses compile + basis_probabilities; installed documentation says unitary subset | Keep legacy path; extended support gated on maintained adapter evidence |
| qsimcirq 0.22.1 + installed Cirq | Service uses Cirq parser + compute_amplitudes; library source includes sampling and repeated records | First candidate for QASM sampling, custom gates and a QASM3 subset |
| Qiskit QASM2/QASM3 tooling | Candidate named in context; not a selected runtime dependency | Add only for a demonstrated gap, with pinned version/license and compatible engine adapter |
| Local reference simulators | Analytic fixtures and established independent simulators permitted | Correctness references only; no runtime fallback |

Discovery Needed: the implementation owner must record exact installed Cirq version from `uv.lock`, licenses from the selected distributions, runnable positive/negative probes, and adapter versions in a capability report. Inspect `qsim/worker.py`, Clifft companion integration APIs and the pinned `cirq.contrib.qasm_import`/`qsimcirq` sources. If an engine cannot preserve a feature through maintained tooling, mark it unsupported and defer that cell; do not build a classical interpreter to satisfy it.

Component responsibilities:

- Existing `qsim/models.py`: strict request schemas and cheap structural validation.
- Existing `qsim/api.py`: authentication, supported engine/format/operation checks, enqueue and legacy canonicalization. It must not compile untrusted circuits.
- Proposed `qsim/qasm_adapter.py`: one internal module isolating importer APIs, capability-specific lowering, binding, register metadata and normalization of engine results. Library circuit objects stay inside the worker process; this module defines no general-purpose circuit IR.
- Existing `qsim/worker.py`: credential-free child execution, resource checks, native engine calls and result packaging. Native-library imports remain lazy so the API process does not load simulator dependencies.
- Existing store/controller/streams: unchanged placement and durable lifecycle responsibilities. No new scheduler, job pool, event bus or state session.

Worker packaging is part of this feature: the current image copies only `qsim/worker.py` and launches it as a standalone script. Proposed minimal package layout copies only `qsim/__init__.py`, `qsim/worker.py` and the new `qsim/qasm_adapter.py` into `/opt/qsim/qsim/`, sets worker working directory `/opt/qsim`, and invokes `/opt/qsim-venv/bin/python -m qsim.worker`. Change the worker's child launch to `python -m qsim.worker simulate ...` with that same package-resolving working directory; source-based tests use the repository root. Do not copy service settings, auth, store or controller into the worker image. The adapter must import without native libraries or side effects; its static `SUPPORTED_COMBINATIONS` metadata is the shared API/worker allowlist, while importer/simulator imports occur only inside worker-called functions. Feature admission defaults remain closed until the matching image is deployed. This changes fixed packaging and entry points, not request-controlled imports.

## Data Flow (step-by-step; include a Mermaid sequence diagram when useful)

1. Authenticate and validate the request envelope, operation fields and published support combination.
2. Canonicalize using legacy rules for legacy inputs; commit request and idempotency identity to the existing store.
3. Let the existing controller place or reuse a SkyPilot worker. Capacity shortage remains queued.
4. In the credential-free child, parse using the selected maintained importer, bind standard parameters, check feature support, expansion and output bounds, then execute the requested engine.
5. Normalize results and persist a complete summary/artifact through the existing authenticated callback. Parser/execution failures become safe terminal failures.
6. Clients retrieve owner-authorized results or resume status/final events. Client disconnection has no effect on active execution.

## Public Interfaces (APIs, CLIs, schemas, config)

### D2: API compatibility

Proposed additive change: extend `/api/jobs/qasm2` with `operation`, defaulting to `basis_probabilities`; add `sample` with `shots` and `seed`. Preserve every existing probability field/default and require `Idempotency-Key`. Add `/api/jobs/qasm3` only when a verified subset exists; its operation must be explicit. Extend generic `/api/jobs` consistently while preserving its existing optional-key behavior. No new CLI is required.

| Operation | Required fields | Defaults and constraints |
| --- | --- | --- |
| Legacy QASM2 basis_probabilities | circuit, bitstrings | Existing engine=clifft, bit_order=big; legacy validation and result shape unchanged |
| New QASM sample | circuit, operation=sample | engine=clifft remains default; unsupported default combination is rejected with a supported choice, never silently rerouted; shots=1000, seed=42, bounds unchanged |
| New QASM3 basis_probabilities | circuit, operation, bitstrings | Only enabled for a verified engine; existing basis-bit ordering contract |

For sampling, reject bitstrings/bit_order. For probability operations, reject new sampling-only fields. Do not change already accepted generic-route fields merely because their values are unused by an old probability operation. Unknown fields remain rejected. Versioned bindings are described in D5.

### D3: Sampling and classical results

New QASM sampling results use `result_schema="qasm-samples/1"`; existing Stim/probability schemas do not change. Proposed summary fields are engine/version, importer/version, input_format, operation, shots, seed, qubits, elapsed_seconds, `registers`, `counts`, `counts_truncated`, and a preview of at most the first 20 shots. Keep at most 256 count entries, ordered by descending count with lexicographic count-key ties; `counts_truncated` explicitly reports omitted entries. These are proposed new-schema rules, not changes to legacy result ordering. Register entries are `{name, size, offset}` in source declaration order. Classical bits flatten by register declaration order and ascending bit index; a count key follows that same left-to-right column order, explicitly not a library-specific integer display convention.

NPZ contains `classical_memory`, a uint8 `[shots,total_classical_bits]` array. Every classical bit starts zero for each shot; repeated writes replace the final value. All shots are present in the artifact even when the JSON counts/preview are capped. Reconstruct counts from this array and the ordered metadata. Keep native measurement-event history internal initially; exposing it later requires a separate versioned event-index/mask contract. Never substitute a measurement-event matrix for final memory.

Use importer-provided register/write mapping, or a narrowly documented adapter mapping from its existing representation. If repeated writes or skipped conditional measurements cannot be reconstructed without executing a new classical runtime, reject that construct and mark it unsupported. QASM sampling with no measurements is rejected in the first subset rather than returning a misleading empty sample. Unused classical declarations alone do not make an otherwise unitary probability circuit non-unitary; allow them only where the selected importer safely preserves quantum register ordering.

### D4: Dynamic-circuit semantics

Use only engine-supported measurement, reset and condition execution. Conditions use language-defined values at their point of execution, including QASM2 register integer bit significance. Reset must work on entangled inputs; each shot begins independently. Test both branches and repeated classical writes. No conversion to postselection, deferred measurement, branch enumeration or host-language conditional interpreter is selected. Unsupported control flow, loops, timing, pulses, runtime-sized arrays and external functions fail explicitly. This is a subset boundary, not a promise that supported syntax automatically executes on both engines.

### D5: Import, gates and binding

Custom gates use the selected importer's standard definitions and decomposition. Approve only packaged includes (`qelib1.inc` and, for a verified QASM3 importer, `stdgates.inc`); arbitrary include search paths and network resolvers are disabled. Enforce expansion accounting around library-native decomposition, not by parsing source with regular expressions.

Proposed first binding contract: QASM3 `input float`/`input angle` declarations supported by the chosen importer plus `bindings: {name: finite_number}`. Reject missing and extra names, bools, strings, nulls, NaN and infinities; reject other input types until separately specified. Bind through library-native resolvers; no textual substitution. Concrete supported declaration syntax remains blocked on OR-001/002 evidence. QASM2 accepts finite numeric/constants and gate-local parameters, not unresolved global placeholders. Clients may bind Qiskit/Cirq circuits locally and export valid QASM2 without adding a new submission format.

### D6: Capabilities and errors

Extend the authenticated capabilities response additively, preserving old keys. New per-engine entries enumerate verified format/operation/features, exact engine/importer versions, result schema and limits. Planned/unverified features are not advertised as executable. Build/runtime checks must agree with the matrix; the API cannot advertise features absent from the deployed worker image.

Envelope validation and unsupported top-level combinations fail HTTP 422 before job creation. Parser, binding, engine-feature and resource failures found inside the worker become FAILED jobs. Keep the existing `Completion.error` string, callback persistence, job-status and event contracts for this increment. Use stable safe category prefixes such as `invalid_circuit`, `unsupported_feature`, `invalid_binding`, `resource_limit` and `execution_failed` within that string; no new top-level callback field is added to the strict Completion model. A structured `error_code` field is deferred until its schema, persistence, public status and event mapping are designed end to end. Do not expose raw library exception text, paths, source lines containing user code, tokens or infrastructure names. Return an actionable feature name and supported alternative where known.

### D8: Streaming

Retain existing SSE progress/final-result events and Last-Event-ID replay. Keep incremental_samples=false for this release. Native callback or chunking support is not proven; restarting independent seeded batches is not assumed to reproduce one native run. Incremental streaming requires a later approved contract for chunk identity, seed derivation, replay and final artifact reconciliation (OR-005).

### Boundary artifact schemas

[interfaces.json](interfaces.json) and [module-boundaries.json](module-boundaries.json) describe this feature's intended boundaries, not all exports or imports of the repository. They include existing modules and one explicitly proposed module. `interfaces.json` has `version` and `modules`; each module has `description`, `public_exports` (`name`, `kind`, `signature`) and `internal_only`. `module-boundaries.json` has `version` and `boundaries`; each entry has `module`, `description`, `allowed_imports_from` and `forbidden_imports_from`. Signatures for proposed symbols are design contracts, not claims that code exists. No enforcement tooling is installed by these files.

## Data Model & Storage (including migrations and idempotency)

### D7: Durable jobs and results

Keep existing request JSON, SQLite lifecycle and NPZ storage. No SQL migration or artifact rewrite is selected. New-operation fields live in request/summary JSON. Legacy requests must produce the identical canonical bytes used today: preserve default clifft omission and existing normalized defaults, while excluding newly added default-only fields from the legacy hash projection. A genuinely new operation/format/binding includes all its semantic fields in its own deterministic projection. Do not hash a new model_dump indiscriminately. Test saved historical request/hash pairs and explicit-vs-omitted legacy defaults.

Bindings are sorted by name in canonical JSON after strict numeric validation; a changed bound value conflicts under a reused owner/key. Define one canonical numeric representation when implementing the validated binding schema, and test equivalent accepted spellings; this cannot retroactively change legacy normalization. Store original accepted request and normalized identity consistently. Idempotency promises one durable job identity, not exactly-once native execution.

New artifacts must be complete and pass shape/dtype/shot/metadata consistency checks before success. Existing terminal-state guards reject conflicting callbacks. Retain previous artifact readers. These additive changes are reversible only while no new-format jobs depend on an older worker; rollout/backout below accounts for that.

## Concurrency, Ordering, and Consistency (if relevant)

Do not alter dispatch concurrency or placement. Circuits and per-shot state remain local to one job; no result from another owner/job influences execution. Measurement ordering is importer/engine ordering preserved by a tested adapter, not lexicographic sorting of keys. Within final-memory results, declaration order and numeric bit index are stable even for q_10 or multi-register circuits. Completion must refer to the matching durable job and execution credentials; client retries reuse existing lifecycle protections.

## Failure Modes & Recovery (timeouts, retries, circuit breakers, degraded modes)

Reject unsupported syntax/features instead of omitting operations. Resource exhaustion is a job failure with no partial success. No retry on another simulator. Existing controller reconciliation and callback retry behavior remain; no new exactly-once or automatic replay guarantee is introduced. Transport/callback timeouts are distinct from active-simulation lifetime. If results cannot be delivered after existing retries, preserve existing recovery behavior and report the lifecycle limitation rather than claim successful delivery. No new circuit breaker or active-job deadline is introduced.

## Security Model (authn/z, permissions, secrets handling, injection defenses)

### D9: Lifecycle, limits and security

Preserve authentication and owner checks. Parsing, binding, decomposition and simulation execute in the credential-free child under existing 3 GiB address-space and 12 MiB file-output limits. Do not pass API/callback tokens or controller environment into this child. Retain 65,536-character circuit size, 1–10,000 shots, seed range, engine qubit bounds and existing 1,000,000-cell sampling bound in their existing scopes.

Proposed new QASM output accounting includes `shots * total_classical_bits` in the 1,000,000-cell envelope; a future event-history array must share that envelope, not evade it. A numeric expansion/depth budget and pre-expansion enforcement strategy remain OR-004: measure representative library expansion and select a bounded library traversal before enabling custom-gate expansion. A post-expansion count alone does not prevent allocation exhaustion. Reject loops/dynamic sizes in the first QASM3 subset. Do not introduce time-based compilation or simulation expiry as a substitute for structural bounds.

Explicit cancellation and idle cleanup remain; idle cleanup cannot terminate active work. Includes use only packaged approved content; native file/network lookup must be disabled or confined before feeding untrusted input. A parser that cannot meet that condition is not eligible. No new deployment permission, hardware credential or production replay is required for this specification.

## Observability (signals, dashboards, alerts; what you will measure)

Record safe failure category, engine/importer version, operation, format and result schema in authorized job provenance. Separate validation/compile/execution durations for new adapters when measurement is available; retain total elapsed behavior for legacy results. Do not log circuits, bindings, tokens or private hostnames in new public diagnostics. Existing console lifecycle and resource views remain adequate; no new monitoring platform or SLO is selected. Release evidence compares representative unchanged-unitary timing against baseline with an agreed threshold (OR-007).

## Rollout Plan (staged rollout, feature flags, backout)

### D10: Verification and release

1. Review requirements and this provisional design with the owner; resolve blocking contracts for the first subset. Record versioned capability probes and negative cases before final integration selection.
2. Implement the smallest verified subset in PRs: existing-tooling custom gates/QASM sampling first where supported; bring QASM3 forward if the same tested importer supports it. Dynamic controls do not block independent features.
3. Default new capabilities disabled until their worker image and tests are approved. Use the explicit `qsim.qasm_adapter.SUPPORTED_COMBINATIONS` allowlist shared by API validation and worker validation; its module-level code is dependency-free metadata/types only, with native imports restricted to functions. Do not use independent feature flags per syntax token.
4. Release worker support before enabling API admission; ensure warm-worker reuse selects the compatible worker image. Preserve engines/defaults. Deploy only through the approved private baseline with action-specific authorization.
5. Backout by disabling admission of new combinations while preserving capable workers until already accepted new-format jobs finish or are explicitly cancelled. Do not route their requests to old workers or kill active work to downgrade. Roll back application/image only after no pending/running request requires the new schema. Preserve stored artifacts and readers.

Specification edits stay local. Implementation runs `bash tools/ci/check.sh`, Standpunkt first/GitHub fallback, automatic review without manual retriggers and no more than two substantive remediation rounds. Report CI failure; owner merges. No deployment occurred during this design pass.

## Alternatives Considered (at least 2, with pros/cons)

1. **Existing Cirq importer and native Clifft paths (preferred first experiment).** Minimal dependencies and changes; installed parser already has broader grammar. Risks: private parser API, imperfect register mapping and qsim dynamic-operation gaps. Isolate behind one adapter and pinned contract tests.
2. **Qiskit as a common frontend.** Familiar standards tooling, gate decomposition and binding; useful independent reference semantics. Costs: new runtime dependency and translation to the actual selected engines; Qiskit import success alone cannot prove lossless backend execution. Adopt only for a measured integration gap.
3. **Do nothing / smaller change.** Keep probabilities and allow only demonstrated custom gates in the current importer, without sampling or QASM3. Lowest risk and useful independent release, but does not satisfy the complete roadmap. Retain as a valid first increment if broader semantics remain blocked.

## Open Design Questions

- OR-001: Which pinned combinations actually execute terminal/nonterminal measurement, reset, controls, repeated writes and QASM3? The installed parser's broader grammar is only source evidence.
- OR-002: Approve proposed QASM3 numeric bindings and extra-name rejection after importer probes; fix numeric canonicalization before implementation.
- OR-003: Owner approval of final-memory-only initial QASM results, declaration-order flattening and no-measurement rejection. Verify importer metadata supports reconstruction without a new classical interpreter.
- OR-004: Select measured expansion/depth budgets and pre-allocation enforcement; verify final-memory size/output accounting. Until resolved, affected custom-gate/dynamic features remain disabled.
- OR-005: Incremental samples remain unsupported; no initial-release blocker.
- OR-006: Establish fixture-specific numeric/statistical tolerances before judging engine results.
- OR-007: Establish benchmark cases and regression threshold before release; no invented performance promise.

## Simplicity Budget (maximum new modules, maximum new public interfaces, dependency addition policy, and a required "do nothing / smaller change" alternative that was evaluated)

Maximum one new runtime module (`qsim/qasm_adapter.py`); extend existing models/API/worker. Maximum one new HTTP route (`/api/jobs/qasm3`), one new result schema and one bindings object. No new database tables, scheduler or service. Prefer zero runtime dependencies; at most one justified importer/integration package family for the selected missing path, with transitive footprint and licenses reviewed. A required second new architectural module or package family triggers design review rather than silent growth. The smaller-change alternative is recorded above. Test-only local reference dependencies are separately pinned and must not pull hardware credentials into CI.

## Rejected Abstractions (abstractions considered and explicitly rejected with rationale)

- Universal compiler IR, plugin registry and arbitrary backend chain: unnecessary for two fixed engines; obscures unsupported semantics.
- Handwritten dynamic-circuit interpreter, source substitution and regex-based compiler: duplicates established tooling and risks incorrect collapse/control behavior.
- Automatic engine fallback: violates explicit engine choice and provenance.
- Server-managed optimizers, batches and persistent state: outside the client's requested responsibility boundary.
- Chunked sample streams without verified seed/replay semantics: misleading completeness and reproducibility guarantees.

## Verification Strategy (mapping to requirements and test strategy)

| Design anchors | Requirements | Required evidence |
| --- | --- | --- |
| D1 | REQ-001,009 | V01 versioned importer/engine probes; source/docs alone insufficient |
| D2,D7 | REQ-002,012–014,017 | V02/V07 legacy hash fixtures, defaults, saved results, owners, callbacks and retries |
| D3 | REQ-003,004 | V03 final-memory reconstruction, unused bits, multiple registers, repeated writes, asymmetric bit-order cases |
| D4 | REQ-005,006 | V04 entangled reset, both branches, mid-circuit collapse, fresh shots, unsupported controls |
| D5 | REQ-007,008 | V05 binding/domain/include and bounded gate expansion probes |
| D6 | REQ-010,011,016,022 | V01/V06 advertised matrix matches execution; safe categorized errors, nonunitary probability rejection |
| D8 | REQ-015 | V08 existing replay/final events; incremental remains false |
| D9 | REQ-018–021 | V09/V10 no active deadlines/caps, cancellation, active-vs-idle, capacity queue, bounds and include isolation |
| D10 | REQ-023,024 | V11 both-engine CI, examples, automatic review policy, owner merge and authorized smoke checks |

Use deterministic asymmetric basis-state fixtures to catch bit-order mistakes Bell symmetry hides. Compare exact probabilities against analytic values with precision-specific tolerances selected before results. For random sampling, predeclare distribution criteria and use independent reference execution; equal seeds across engines need not match samples. Build and run the actual worker image in validation: assert the adapter module is present, the module entry point resolves, the credential-free child imports it, and both engines execute representative cases. Assert importing allowlist metadata in the API does not import native libraries. Record all capability experiment versions and inputs as review evidence. This document pass provides source-based design review only, not runtime validation or a green CI claim.
