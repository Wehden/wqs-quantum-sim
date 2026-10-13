# Requirements: broader circuit support

Status: draft for owner review. These requirements describe the intended outcome; they do not announce current support or fix an implementation architecture.

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| R01 | The service shall reuse maintained standard parsers and circuit tooling where they support the required semantics. Any custom adapter shall identify its specific upstream gap. | Versioned capability matrix and integration decision, with representative executable fixtures. |
| R02 | Existing Stim sampling and QASM2 probability requests shall retain their validation, idempotency and result contracts. | Existing API/numerical tests plus compatibility fixtures pass unchanged. |
| R03 | When QASM sampling is requested on a supported engine, the service shall accept standard classical-register declarations and terminal measurements, applying explicit shot and seed values. | Bell sampling, multiple registers, reordered destinations and unmeasured-bit fixtures. |
| R04 | Results shall preserve named register/bit mapping and distinguish final classical memory from measurement-event history where both are exposed. | Repeated writes, repeated measurements, register ordering and JSON/NPZ reconstruction tests. |
| R05 | Where upstream execution supports it, mid-circuit measurement and reset shall preserve collapse, entanglement and fresh per-shot initialization semantics. | Measure-reset-measure and entangled-reset fixtures compared with an independent local reference. |
| R06 | Where upstream execution supports it, classical conditions shall depend on the correct preceding measurement/classical value. | Both condition branches, conditional correction and teleportation-style fixtures; unsupported controls rejected. |
| R07 | Standard custom gate declarations shall be parsed and decomposed through reusable tooling with bounded expansion. | Parameterized/nested valid gates, malformed definitions and recursion/expansion rejection fixtures. |
| R08 | Standard parameter binding shall reject missing, invalid and non-finite values. Binding values shall participate in idempotency identity. The service shall not invent an OpenQASM 2 template dialect. | Library-bound reference circuits, changed-binding conflicts and validation tests. Binding syntax selected during design. |
| R09 | OpenQASM 3 support shall expose a tested, engine-specific subset through an existing importer. | Equivalent supported QASM2/QASM3 cases; unsupported timing, pulse or other constructs fail explicitly. |
| R10 | Unsupported engine/format/operation combinations shall produce actionable errors without silently removing instructions or selecting another simulator. Non-unitary circuits shall not be treated as unitary probability queries. | Capability/error contract tests and malformed/unsupported input fixtures. |
| R11 | New operations shall use existing durable jobs, owner isolation, idempotent retry and JSON/NPZ delivery. No cross-request quantum state shall be retained. | Ownership, duplicate submission, recovery, result and independent-job tests. |
| R12 | Existing status/final-result streaming shall remain resumable. Incremental sample streaming shall be advertised only with demonstrated backend/chunking semantics for ordering, seeds and retries. | Event replay and completed-result reconstruction; unsupported incremental mode identified in capabilities. |
| R13 | Inputs shall remain data-only. Includes shall be limited to approved packaged libraries; arbitrary local/network reads and client callables shall not be accepted. | Include traversal/network and executable-payload rejection tests. |
| R14 | Existing memory/output/input safeguards, explicit cancellation and idle cleanup shall remain. No active-job wall/CPU-time deadline or fixed concurrency cap shall be added. | Lifecycle regression tests and bounded-output/expansion validation. |
| R15 | Each delivered feature shall update capabilities, developer/agent examples and exception text for the tested engine/version combinations. | Working examples, documented subset, CI for both integrations and approved live smoke evidence. |

## Numerical and sampling acceptance

Use analytic results and established reference simulators. Define tolerances appropriate to each engine's precision. Compare stochastic distributions with a stated statistical criterion; identical seeds do not imply identical samples across different engines. Preserve current bit-order behavior rather than silently changing it to match a new library.

## Delivery constraints

Split implementation into reviewable PRs after the design and tasks stages. Prefer the smallest useful release supported by existing adapters. Dependencies between features follow tested importer/backend requirements, not an assumed need to implement every language construct first.

Run the existing repository verification command, `bash tools/ci/check.sh`, and retain the established Standpunkt-first/GitHub-fallback policy. Deployment changes belong in the approved private baseline, not in this public specification. Owner review and merge remain required.
