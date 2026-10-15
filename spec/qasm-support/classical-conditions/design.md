# Design amendment: classical conditions

Status: **approved by the owner on 2026-10-14**. Task plan approved and implementation authorized on the same date. Date: 2026-10-14. Parent: [approved QASM design](../design.md), D1/D3/D4/D6/D9/D10; [REQ-006](../requirements.md). Source baseline: `2ed33601e9fdd5ab8ba04ef98c831486dbf9205c`. This amendment does not enable a capability or authorize deployment.

## Overview

Recommend adding **explicit `engine="aer"` sampling** through the existing QASM2/QASM3 job routes. Use Qiskit's maintained importers and native Aer classical execution. Keep Clifft as the default and retain the existing qsim and Clifft paths, identities and capability restrictions.

This changes the parent design's two-engine scope and selects a new runtime dependency family. The owner approved this scope on 2026-10-14. TASK-011 has added the pinned Qiskit family to the worker environment and verified combined capabilities. API admission and deployment remain closed. The original investigation changed no application code.

The [requirements clarification](requirements-delta.md) maps the proposal to the existing requirements. The [evidence record](../research/classical-conditions/evidence.md) contains versions, circuits and limitations. Twelve local Aer probes passed. Existing Cirq/qsim probes establish why enabling conditions in the current adapter is insufficient.

## Architecture (components, responsibilities, and boundaries)

Select Qiskit 2.5.2, qiskit-aer 0.17.2 and qiskit-qasm3-import 0.6.0 as the investigated candidate pins. Their licenses are Apache-2.0. Freeze the entire compatible dependency set during implementation; the isolated research lock is evidence, not a replacement for the project's lockfile.

- Models/API: add the explicit engine and a dependency-free capability entry. Native libraries remain absent from the API import path.
- New internal `qsim/aer_adapter.py`: QASM safety preflight, maintained importer calls, native-circuit validation, execution and final-memory normalization. Import native libraries only in child-called functions. It must not interpret classical branches itself.
- Existing worker: dispatch explicitly to this adapter and use the existing summary/artifact/callback path.
- Existing SkyPilot controller: continue scheduling ordinary isolated worker jobs. No Aer-specific scheduler, service or remote IBM account.

Use `AerSimulator(method="statevector", device="CPU", precision="double")`. Do not use automatic method selection or a remote backend. Set native threads from the worker's allocated CPU budget; never let Aer assume all host CPUs are available. Thread and memory settings are deployment-owned, not client-supplied. The exact resource plumbing must be confirmed before implementation task approval.

## Data Flow (step-by-step; include a Mermaid sequence diagram when useful)

1. Authenticate and validate the existing envelope, explicit engine, format and sample operation.
2. Persist the durable job with the existing owner/idempotency contract.
3. Dispatch through the existing SkyPilot worker pool, selecting a compatible image.
4. In the credential-free child, preflight source limits, import through Qiskit, validate the bounded native circuit, then transpile for the fixed Aer backend.
5. Aer evaluates conditions and maintains classical state for each shot. Request complete shot memory.
6. Convert final memory to the existing declaration-order NPZ matrix and JSON summary. Persist through existing callbacks and owner-scoped result retrieval.

## Public Interfaces (APIs, CLIs, schemas, config)

No new HTTP route or result schema. Add `aer` to the engine enumeration, including generic submissions and capabilities. Require `operation="sample"` for Aer; default Clifft behavior stays unchanged. Aer Stim and basis-probability requests reject before job creation.

Proposed initial subset:

| Format | Conditions | Body |
| --- | --- | --- |
| QASM2 | Standard `if(register == integer)` | One standard gate statement, including bounded register broadcast |
| QASM3 | `bit == true/false` or full `bit[n] == integer`; optional `else` | Flat blocks of supported standard gates, measurement and reset |

Require constants to fit the compared register width; reject out-of-range constants without truncation. Allow only top-level declarations and explicitly supported finite numeric gate arguments. Reject nested conditions initially. Permit ordinary measurements/reset outside branches. Conditional QASM3 measurement preserves the previous destination value when its branch is skipped.

Initially reject Aer custom gate declarations and nonempty bindings. Those capabilities remain available through the already supported qsim combinations. This limit avoids claiming that successful parsing proves safe bounded custom expansion or binding compatibility in the new importer. Reject loops, switch, classical arithmetic/assignment, register slicing/aliasing, externally defined functions, timing, pulse operations, opaque gates, annotations and hardware-qubit syntax. The supported gate allowlist must match the current tested numeric gate set; unknown native instructions fail closed.

One-bit QASM3 values use boolean comparisons. The investigated importer rejects `bit == 1`; public examples must use `bit == true`. This is an explicit importer subset, not a claim that other standard forms are invalid OpenQASM.

Capability metadata must identify these engine-specific restrictions and actual Qiskit/Aer/importer versions. The existing qsim/Clifft condition capability remains false. `incremental_samples` remains false.

### Boundary artifact schemas

[interfaces.json](interfaces.json) and [module-boundaries.json](module-boundaries.json) are complete proposed amendment-boundary artifacts. They supplement the approved parent's artifacts; no runtime exports exist by virtue of these files.

## Data Model & Storage (including migrations and idempotency)

No table or artifact migration. Include explicit `engine="aer"` in canonical identity; preserve every historical Clifft/qsim hash fixture. Reusing an owner/key with a different engine must conflict. Do not replay an old job on Aer.

Retain `qasm-samples/1`: declaration-order registers, ascending bit index, zero-initialized classical memory, last write wins. Aer raw shot memory encodes classical bit positions in hexadecimal. Map positions through `QuantumCircuit.find_bit` and original register metadata, rather than interpreting display strings or reversing multi-register text. QASM3 scalar bits are one-bit register entries. Validate the mapping before and after transpilation; reject any unexpected reordering or aliasing.

Results contain exactly `shots` rows of uint8 memory. Preserve 256 count entries, 20 preview rows, their byte budgets, truncation indicators and complete NPZ data. Provenance names Aer and its fixed CPU method. Equal seeds need repeatability within a pinned configuration, not identical samples across engines. All allowed seed bits must reach the native engine without truncation.

## Concurrency, Ordering, and Consistency (if relevant)

Native Aer owns measurement collapse, condition evaluation and per-shot initialization. No Python loop executes branch decisions. Shots and jobs share no quantum or classical state. Existing dispatch concurrency and capacity queuing remain unchanged. Native threads must respect each allocation; do not add a global job cap.

## Failure Modes & Recovery (timeouts, retries, circuit breakers, degraded modes)

Unsupported engine/operation combinations reject at submission. Circuit/importer errors discovered in the child become safe terminal failures using existing categories. Never retry through Cirq, qsim or Clifft as a fallback. Preserve cancellation and existing callback/idempotency protections. No active-job deadline is introduced.

If import safety, output mapping or isolation cannot be demonstrated, keep Aer admission closed and return the finding to design review. Do not weaken the condition semantics to make a test pass.

## Security Model (authn/z, permissions, secrets handling, injection defenses)

Retain the credential-free child and existing source, memory and output bounds. QASM2 calls `qasm2.loads(..., include_path=())`; admit only the packaged `qelib1.inc`. QASM3 admits only `stdgates.inc` and passes no annotation handlers. Prove that include rejection cannot read filesystem or network content.

Before QASM2 import, use maintained lexer tokens to bound literal register declarations and reject custom gates/unsupported constructs. This is structural admission checking only. Before QASM3 conversion, validate the maintained OpenQASM AST, allowing literal declaration sizes and the explicit subset. Do not write a parser or rewrite circuit source. Reject oversized numeric tokens before expensive integer/expression construction. The current preflight rejects every IF token and cannot simply be reused unchanged.

Retain 26 declared qubits, 65,536 source characters, at most 65,536 expanded primitive operations, nesting bound 32, and the 1,000,000 sampling-cell budget. Count both conditional branches conservatively and include all possible measurement events in the budget, even if a branch is skipped. Check the native/transpiled circuit again before execution. Keep limits enforceable before importer-driven allocation; hostile input tests are a release gate.

Double-precision statevectors and Aer imports may consume more memory than current qsim. The existing 3 GiB child address-space limit must pass actual image tests. The 26-qubit ceiling is a maximum, not evidence all such jobs fit. If a lower Aer-specific admission bound is necessary, measure and document it for review; do not silently increase worker memory or impose runtime limits.

## Observability (signals, dashboards, alerts; what you will measure)

Record engine/importer versions and separate import/transpile/execution durations where supported. Keep existing job/status/resource views. Do not log submitted source, native tracebacks or private configuration. Measure worker startup and peak memory in image tests before selecting operational resources.

## Rollout Plan (staged rollout, feature flags, backout)

The System2 design review gate passed on 2026-10-14. The owner also approved [the implementation task plan](tasks.md) on 2026-10-14. Implementation is in progress; verification and owner-merged PRs remain required before a separately authorized deployment.

1. After design approval, amend the task plan with bounded leases: importer/safety probes, adapter/results, API/docs, and integration/image verification. Do not treat the previous completed TASK-006 as reopened implementation authority.
2. Implementation PRs preserve existing tests and add Aer checks. Standpunkt remains the preferred test runner with hosted fallback; the packaged-image job remains separately hosted and required. Automatic review only, at most two rounds before escalation. Owner merges.
3. Publish reviewed worker support and verify image compatibility before admitting Aer requests. A deployment and live smoke check need separate authorization. Reuse the release/CD flow once approved; this amendment does not decide its implementation.
4. Backout closes Aer admission and drains accepted Aer jobs on capable workers. Preserve stored results. No engine substitution or forced active-job termination.

## Alternatives Considered (at least 2, with pros/cons)

| Option | Benefit | Limitation / decision |
| --- | --- | --- |
| Explicit Qiskit/Aer engine | Maintained QASM import and native classical memory/control; 12 passing local cases | Adds one dependency family and a third engine. Recommended for approval |
| Explicit Cirq simulator | Already installed; scalar controls execute | Existing importer drops broadcast operations and native controls fail on unwritten keys. Needs importer repairs and mapping evidence; not selected |
| Upgrade qsim or extend its native core | Retains existing engine identity | Pinned qsim rejects controls; inspected upstream main translation does not establish an available fix. A native-engine contribution is larger than a service integration |
| Clifft upgrade / companion conversion | Retains near-Clifford engine | Clifft 0.12 QASM2 source still rejects classical statements. Its Stim feedback support is not a maintained general QASM conditional adapter |
| Do nothing / smaller change | No added engine/dependency | Keep rejecting conditions; valid fallback if dependency cost is not approved. A single measured-bit-only Cirq subset would still leave important QASM memory semantics unresolved |

## Open Design Questions

- **Resolved owner decision (2026-10-14):** explicit Aer and the bounded first subset are approved. The remaining discovery items below gate implementation milestones.
- **Resolved by TASK-011:** the combined Python 3.12 worker environment preserves existing engine pins, passes dependency/capability checks, and has a recorded transitive license inventory and measured foundation image footprint. See [implementation-decisions.md](implementation-decisions.md). **Remaining TASK-014 gate:** actual Aer execution and boundary workloads must pass the 3 GiB child isolation envelope; image disk size does not establish runtime memory use.
- **Discovery Needed, controller/worker task:** identify how allocated CPU and compatible worker-image identity reach the child. Reuse current fields where sufficient; any new configuration contract requires a documented task-plan amendment.
- **Discovery Needed, validation task:** prove source-level preallocation guards, native instruction allowlisting and classical bit mapping for both formats. A failure blocks admission, not a reason to substitute another backend.

## Simplicity Budget (maximum new modules, maximum new public interfaces, dependency addition policy, and a required "do nothing / smaller change" alternative that was evaluated)

At most one additional runtime module (`qsim/aer_adapter.py`), no HTTP routes, tables or result schemas, and one new public engine value. One justified dependency family: Qiskit/Aer plus its maintained QASM3 importer and transitives. Keep existing worker dispatch explicit. No generic plugin framework. The smaller-change alternative is continued explicit rejection.

## Rejected Abstractions (abstractions considered and explicitly rejected with rationale)

No handwritten classical interpreter, branch enumeration, postselection/deferred-measurement rewrite, arbitrary provider routing or silent backend fallback. Do not label Cirq or Aer execution as Google qsim. No IBM hardware credentials or hardware jobs are needed.

## Verification Strategy (mapping to requirements and test strategy)

| Requirement | Gate |
| --- | --- |
| REQ-005/006, V04 | Both branches; unwritten and partially written registers; little-endian integer tests; repeated writes; conditional reset; skipped measurement; teleportation in X/Y/Z readout bases; fresh shot state |
| REQ-003/004, V03 | Multiple/scalar registers, declaration order, native memory mapping, summary truncation and complete NPZ |
| REQ-002/012–014/017 | Existing hashes/defaults unchanged; Aer engine identity; owner/key conflicts; retry, callback and artifact authorization |
| REQ-010/011/016/022 | Exact capability matrix; invalid combinations; safe errors; unsupported nesting/loops/bindings/custom declarations; no fallback |
| REQ-018–021 | Include confinement, oversized input before allocation, branch/measurement budgets, isolated child, allocated native threads, unchanged concurrency and active lifetime |
| REQ-023/024 | Three-engine CI, actual packaged child execution, documentation examples, worker compatibility and separately authorized live acceptance |

Local probes use 4,096 shots, seed 42, deterministic exact expected outputs and a predeclared absolute frequency tolerance 0.05. They validate upstream feasibility only. Extend to independent analytic/reference checks for teleportation in several bases, all seed boundary cases and negative inputs before admission. Preserve the parent's legacy timing acceptance threshold. No service CI, production acceptance or independent persona review is claimed by this amendment.
