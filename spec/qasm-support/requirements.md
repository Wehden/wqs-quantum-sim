# Requirements: broader circuit support

Status: approved by the owner on 2026-10-13 following System2 requirements-engineer and design-architect review. Open evidence and contract decisions below remain prerequisites for affected implementation. Task plan approved on 2026-10-13; execution starts with capability evidence.

Source: [context.md](context.md), roadmap issue #2 and the original R01–R15 draft. Existing source contracts were inspected; new importer or engine capabilities have not been established by executable evidence in this requirements review. Requirements state intended behavior, not currently available features.

## Functional Requirements (EARS, numbered with IDs)

- **REQ-001 — Reuse.** The system shall use maintained standard-language parsers and circuit tooling where they preserve the required behavior; where an integration gap remains, its design shall identify the gap and justify any adapter. The service shall not introduce a new circuit language, general compiler IR or handwritten classical execution engine.
- **REQ-002 — Compatibility.** When an existing valid Stim sampling or QASM2 basis-probability request is submitted, the system shall preserve its endpoint, default engine, bit ordering, validation of existing fields, idempotency behavior and result contract. Additive supported inputs shall not invalidate previously accepted requests.
- **REQ-003 — QASM sampling.** Where an engine/format combination is advertised as supporting QASM sampling, the system shall accept supported classical-register declarations and terminal measurements and execute the validated shot count and seed. Classical bits shall begin at the language-defined initial value (zero for supported bit/register declarations), including bits never written by a measurement. The supported subset shall be explicit.
- **REQ-004 — Classical results.** When returning QASM sample results, the system shall identify classical registers, widths, bit indices and output ordering so a client can reconstruct each shot's final classical memory without access to internal circuit objects. If measurement-event history is exposed, the system shall distinguish it from final memory, including repeated writes to one bit.
- **REQ-005 — Measurement and reset.** Where advertised as supported, the system shall preserve mid-circuit measurement collapse and reset behavior, including entangled inputs, and shall initialize each shot independently.
- **REQ-006 — Classical conditions.** Where advertised as supported, the system shall evaluate each supported conditional operation against the classical value at that point in execution, preserving the language-defined bit significance when a register is interpreted as an integer. If a condition or dependency cannot be preserved by the selected integration, the system shall reject it rather than approximate it.
- **REQ-007 — Gate declarations.** Where advertised as supported, the system shall accept standard custom gate declarations using reusable parsing/decomposition facilities, preserve parameter and operand binding, and reject malformed, recursive or over-budget expansion before executing the expanded circuit.
- **REQ-008 — Parameter binding.** Where parameter binding is exposed, the system shall accept only documented standard/library-native parameter forms and finite validated values, reject missing or invalid bindings, and include bindings in idempotency identity. The service shall not introduce an OpenQASM 2 placeholder/template dialect; extra-binding policy remains OR-002.
- **REQ-009 — OpenQASM 3.** Where OpenQASM 3 is advertised, the system shall expose a versioned, engine-specific tested subset through maintained importing facilities and reject constructs outside that subset. This requirement does not imply support for every OpenQASM 3 feature or for every engine. Import acceptance alone shall not establish execution support; blocked or unsupported engine/feature cells are valid outcomes of the capability investigation.
- **REQ-010 — Honest capabilities.** The system shall publish tested support by engine/version, input format and operation, including dynamic-circuit features, parameter forms and streaming mode. If the requested combination is unsupported, the system shall reject it without silently dropping instructions, substituting another simulator, or executing against a hardware provider.
- **REQ-011 — Probability semantics.** If a basis-probability request contains measurement, reset or other operations requiring non-unitary execution, the system shall reject the request rather than remove those instructions or present sample frequencies as exact probabilities. An unused classical declaration is not itself a non-unitary operation; it may be rejected as unsupported syntax by an integration but shall not be described as measurement or non-unitary execution. A different future probability operation requires a separately reviewed contract.

## Data & Interface Contracts (schemas, APIs, persistence, idempotency)

- **REQ-012 — Durable submission.** When a new operation is accepted, the system shall persist it through the existing authenticated durable job lifecycle and owner-scoped idempotency contract. A retry of the same validated request and key shall identify the same job; a changed request under that key shall conflict. Engines, operations and bindings shall be part of request identity. Legacy request normalization and stored hashes shall remain compatible, including omitted default-engine handling; adding model defaults shall not cause a replay of an unchanged legacy request to conflict. This does not promise exactly-once backend execution.
- **REQ-013 — Result delivery.** When a job succeeds, the system shall expose an owner-authorized JSON summary and NPZ artifact sufficient to reconstruct the documented result, including result schema version and engine provenance for new result types. It shall preserve existing artifacts and schemas for legacy operations and shall not mark incomplete samples as a complete successful result.
- **REQ-014 — State boundaries.** The system shall keep circuit state and per-shot classical state isolated between requests. Clients shall continue to own batching, call sequences, optimization and state between jobs; this feature shall not add a server-managed optimizer, workflow or persistent quantum-state session.
- **REQ-015 — Streaming.** The system shall preserve resumable status and final-result events. Where incremental samples are advertised, the system shall define ordering, sample identity, replay/retry behavior and seed behavior sufficient to reconstruct the completed artifact without missing or double-counting samples. If that contract cannot be demonstrated, the system shall report incremental sampling as unsupported; a completed sample array shall not be described as incremental streaming.

Compatibility anchors: existing `/api/jobs/stim` and `/api/jobs/qasm2` require an idempotency key; QASM2 currently means basis probabilities with required `bitstrings` and optional `bit_order`. The generic submission route has its existing optional-key behavior. These contracts remain intact. New route names, schemas, binding representation and register array layout are design decisions, not implied changes to those routes.

## Error Handling & Recovery (including retries, timeouts, fallbacks)

- **REQ-016 — Actionable failures.** If syntax, binding, capabilities or resource validation fails, the system shall identify the failure category and an actionable supported alternative where known, without exposing secrets or internal infrastructure. Schema and capability-selection failures found before acceptance shall reject submission; circuit parse/semantic failures found in the isolated worker shall end the accepted durable job with a safe reason. The design shall document these phases without moving untrusted native parsing into the API merely to make every error synchronous. Unsupported operations shall not trigger silent simulator fallback.
- **REQ-017 — Recovery isolation.** When a client disconnects, reconnects or retries a submission, the system shall preserve the simulation lifecycle independently of that connection. Recovery and callback retries shall preserve existing terminal-state and ownership protections; incomplete or conflicting output shall not be promoted to success.

Transport, database and callback-delivery timeouts are distinct from simulation deadlines. Their existing behavior may remain; no transport timeout shall be interpreted as permission to terminate active simulation work. Retry counts and callback-delivery recovery remain the existing lifecycle contract unless an implementation PR explicitly identifies and approves a change.

## Performance & Scalability (explicit budgets/thresholds where possible)

- **REQ-018 — Lifecycle and capacity.** The system shall retain SkyPilot-managed placement, queue when capacity cannot place work, and introduce no fixed active-simulation concurrency cap or active worker/simulation wall-time or CPU-time deadline. Explicit user cancellation and cleanup after completion shall remain; idle cleanup shall not terminate active work.
- **REQ-019 — Bounded resources.** The system shall retain existing input, memory and output safeguards and enforce documented bounds for new expansion, classical memory and measurement-history output. If a new operation exceeds its bounds, the system shall report a resource-limit failure without silently truncating results.

Current anchors, not expansion commitments: 65,536 characters per circuit; sampling 1–10,000 shots with seed 0 through 2^63−1; 1–256 requested basis bitstrings; Clifft 22 active-width/4,096 declared-qubit limits; qsim 26-qubit limit; worker address-space limit 3 GiB; file output limit 12 MiB; existing Clifft sampling output limit 1,000,000 cells. Preserve their existing scope. New QASM classical/history cells and decomposed gates need explicit accounting and thresholds before implementation (OR-004). Existing event-stream connection caps and reconnect timeout are not simulation concurrency or lifetime limits.

No new latency/throughput SLO is asserted without measurement. Integration validation shall record representative validation/dispatch/execution timings and compare the unchanged unitary path against its baseline; release regression thresholds remain OR-007.

## Security & Privacy (authn/z, least privilege, input sanitization, logging hygiene)

- **REQ-020 — Data-only circuits.** The system shall treat submitted circuits and bindings as data and accept includes only from approved packaged libraries. It shall reject arbitrary client callables, executable Python, local-file traversal and network include resolution. Reusable tooling shall not widen the submitter's file/network access.
- **REQ-021 — Isolation and hygiene.** The system shall apply existing owner authorization to submissions, status, streams and artifacts, preserve credential-free, resource-limited processing of untrusted circuits, and omit credentials, raw circuit bodies, binding values and private infrastructure identifiers from newly introduced public errors and diagnostic logs.

## Observability (logs/metrics/traces; SLIs/SLOs if relevant)

- **REQ-022 — Provenance and diagnosis.** For new operations, the system shall make the selected engine/version, importer/adapter version, input format, operation, job identifier, lifecycle outcome and safe validation/failure category available to authorized diagnosis. Capability output shall distinguish verified support from planned or unverified features. Observability shall not require logging submitted circuit or binding contents.

## Backward Compatibility & Migration

- **REQ-023 — Evidence and documentation.** Before advertising a feature, the project shall provide executable correctness and contract fixtures for its supported engine combinations, update developer and agent examples, capability output and exception text, and retain regression coverage for both existing engines. Any dependency upgrade shall include version-specific compatibility evidence. Stored legacy jobs and result artifacts shall remain readable.

No new backend becomes the default by this feature. The existing CPU execution model remains. Any new result metadata is additive or confined to a versioned new-operation schema; migrations must not require rewriting prior job artifacts. Unknown-field rejection remains for fields not deliberately added to a reviewed schema.

## Compliance / Policy Constraints (if relevant)

- **REQ-024 — Review and release policy.** The project shall keep specification revisions local for owner review until implementation, use PRs for implementation, retain Standpunkt-first verification with GitHub-hosted fallback, rely on automatic review without duplicate manual triggers, and limit substantive review remediation to two rounds before escalation. CI failure shall be reported to the owner; missing CI shall not be represented as passing. The owner shall merge implementation PRs; deployment shall follow the approved private deployment baseline and its action-specific authorization.

Integration selection must verify upstream licenses at selected versions. Hardware-provider credentials, paid hardware runs, production job replay and public disclosure of private deployment configuration are not required or authorized by this specification review.

## Open Requirements

Open requirements block acceptance of the affected feature, not independent delivery of features with resolved contracts. None may be silently treated as approved.

| ID | Decision needed before affected implementation | Related requirements |
| --- | --- | --- |
| OR-001 | Which exact pinned importer/adapter/engine combinations preserve each requested operation? Record executable positive and negative evidence and clearly deferred combinations. | REQ-001, 003, 005–010, 023 |
| OR-002 | Which standard parameter forms will be exposed, how are bindings represented, and are unused bindings rejected? OpenQASM 2 declarations are not general unresolved global inputs. | REQ-008, 012 |
| OR-003 | What are the new sampling schema, register flattening/bit order, and optional measurement-history representation? Do not change legacy ordering. | REQ-003, 004, 013 |
| OR-004 | What measured expansion/history/classical-state budgets apply, and how are loops or unsupported dynamic sizes rejected without adding an active-job deadline? | REQ-007, 009, 019 |
| OR-005 | Can native or chunked incremental sampling preserve results, replay and seed semantics across retries? If not, retain status/final streaming only. | REQ-015, 017 |
| OR-006 | What engine-specific numerical tolerances and predeclared sampling acceptance criteria apply to each fixture? Establish before judging numerical acceptance. | REQ-023 |
| OR-007 | Which benchmark inputs and measurable regression thresholds justify release without imposing an unsupported latency SLO? | REQ-019, 023 |

## Validation Plan (how each requirement will be tested/validated)

Validation evidence shall identify dependency versions, fixture inputs, selected engine and expected behavior. A provider's documentation or a successful reference-simulator run alone shall not prove execution support in a wqsim engine. Use analytic fixtures and established local reference simulators; no paid hardware is required. Statistical checks shall use a predeclared criterion and reproducible seeds per engine, without requiring identical samples across different engines.

| Validation ID | Requirements | Evidence required |
| --- | --- | --- |
| V01 | REQ-001, 009, 010, 022 | Versioned capability matrix; minimal positive/negative circuit probes through actual candidate adapters and engines; provenance/capability contract checks. |
| V02 | REQ-002, 013, 023 | Existing API/numerical suites unchanged; prior-job/result fixtures remain readable; legacy bit ordering/defaults and additive schema checks. |
| V03 | REQ-003, 004 | Bell sampling plus asymmetric deterministic basis states (to detect bit reversal); multiple registers; reordered destinations; zero-initialized unused bits; repeated writes/measurements; reconstruct final memory from JSON/NPZ metadata. |
| V04 | REQ-005, 006 | Measure-reset-measure; entangled reset; both condition branches; conditional correction and teleportation-style circuits; independent-shot tests and unsupported dependency rejection. |
| V05 | REQ-007, 008 | Parameterized/nested custom gates; malformed/recursive/over-budget definitions; finite/missing/non-finite binding cases; standard-bound reference equivalents and changed-binding conflicts. |
| V06 | REQ-011, 016 | Non-unitary probability rejection; unsupported format/engine/control constructs; pre-acceptance and worker-side failure states; safe actionable errors without instruction removal. |
| V07 | REQ-012, 014, 017 | Owner-scoped duplicate/conflict tests; restart/retry recovery under existing guarantees; callback conflict/incomplete-result tests; independent jobs and disconnects do not alter work. |
| V08 | REQ-015 | Event replay from Last-Event-ID; reconnect; if incremental enabled, chunk-boundary replay and retries reconstruct exactly the completed artifact and satisfy documented seed contract. |
| V09 | REQ-018, 019 | Focused long-running lifecycle regression beyond former deadlines; active-vs-idle cleanup and cancellation tests; capacity-queue behavior; input/memory/output/expansion boundary checks. |
| V10 | REQ-020, 021 | Reject include traversal/network access and callable/executable input; cross-owner status/stream/artifact access denied; assert no newly logged secrets/circuit/binding payloads. |
| V11 | REQ-023, 024 | Run `bash tools/ci/check.sh` at implementation; execute documented examples and both engine regressions; record chosen CI path, review rounds, owner merge and authorized release smoke evidence. |

No feature probes, runtime tests, CI or live deployment validation are claimed by this document-only review. Design experimentation and implementation validation must record their own results.

## Traceability Matrix (Requirement -> Design Section -> Task IDs)

Design section labels refer to the approved design. Task IDs below refer to the draft task plan; none is approved for execution or completed.

| Requirement | Original draft | Design section (proposed) | Validation | Task IDs |
| --- | --- | --- | --- | --- |
| REQ-001 | R01 | D1: Integration selection | V01 | TASK-001, TASK-002, TASK-004 |
| REQ-002 | R02 | D2: API compatibility | V02 | TASK-003, TASK-005, TASK-009 |
| REQ-003 | R03 | D3: Sampling and classical results | V03 | TASK-005 |
| REQ-004 | R04 | D3: Sampling and classical results | V03 | TASK-002, TASK-005, TASK-006 |
| REQ-005 | R05 | D4: Dynamic-circuit semantics | V04 | TASK-001, TASK-006 |
| REQ-006 | R06 | D4: Dynamic-circuit semantics | V04 | TASK-001, TASK-006 |
| REQ-007 | R07 | D5: Import, gates and binding | V05 | TASK-001, TASK-002, TASK-007 |
| REQ-008 | R08 | D5: Import, gates and binding | V05 | TASK-002, TASK-007 |
| REQ-009 | R09 | D1: Integration selection | V01 | TASK-001, TASK-002, TASK-007 |
| REQ-010 | R10 | D6: Capabilities and errors | V01 | TASK-001, TASK-004, TASK-005, TASK-009 |
| REQ-011 | R10 | D6: Capabilities and errors | V06 | TASK-005, TASK-008 |
| REQ-012 | R11 | D7: Durable jobs and results | V07 | TASK-003, TASK-007, TASK-008 |
| REQ-013 | R11 | D7: Durable jobs and results | V02 | TASK-005, TASK-008, TASK-009 |
| REQ-014 | R11 | D7: Durable jobs and results | V07 | TASK-005, TASK-006, TASK-008 |
| REQ-015 | R12 | D8: Streaming | V08 | TASK-008, TASK-009 |
| REQ-016 | R10 | D6: Capabilities and errors | V06 | TASK-004, TASK-005, TASK-008 |
| REQ-017 | R11, R12 | D7: Durable jobs and results | V07 | TASK-008 |
| REQ-018 | R14 | D9: Lifecycle, limits and security | V09 | TASK-008 |
| REQ-019 | R07, R14 | D9: Lifecycle, limits and security | V09 | TASK-002, TASK-004, TASK-005, TASK-008 |
| REQ-020 | R13 | D9: Lifecycle, limits and security | V10 | TASK-004, TASK-007, TASK-008 |
| REQ-021 | R11, R13 | D9: Lifecycle, limits and security | V10 | TASK-004, TASK-008 |
| REQ-022 | R01, R15 | D6: Capabilities and errors | V01 | TASK-004, TASK-005, TASK-009 |
| REQ-023 | R15 | D10: Verification and release | V02, V11 | TASK-001, TASK-009, TASK-010 |
| REQ-024 | Delivery constraints; owner direction | D10: Verification and release | V11 | TASK-009, TASK-010 |

All original R01–R15 targets are retained through this mapping; they are superseded as identifiers, not marked delivered. Requirements and design are approved; task plan approved on 2026-10-13.
