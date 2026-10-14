# Proposed requirements clarification: classical conditions

Status: approved by the owner with [the design amendment](design.md) on 2026-10-14. This is an additive clarification of the current spec. Existing requirement IDs remain authoritative. No implementation tasks are approved here.

| Parent requirement | Proposed clarification | Design impact | Validation |
| --- | --- | --- | --- |
| REQ-006 | Where conditional sampling is advertised, the system shall evaluate each condition using the classical memory at that point in that shot, including zero-initialized unwritten bits, repeated writes and language-defined integer significance. A skipped measurement shall preserve its destination's previous value. | Amendment, D3/D4 | Deterministic true/false, partial/unwritten registers, repeated writes and skipped-measurement fixtures |
| REQ-001/010/016 | When an engine is explicitly selected, the system shall execute that engine or reject the unsupported request. Any additional engine shall be separately named and shall advertise its exact supported subset. | Amendment, D1/D2/D6; invalidates the two-engine simplicity assumption only if approved | Capability/dispatch tests; negative no-fallback tests; provenance |
| REQ-002/012–014 | When adding an engine, the system shall preserve existing defaults, hashes, result contracts and owner-scoped retries, and shall include the new engine choice in new request identity. | Amendment, D2/D7 | Historical fixtures, cross-engine idempotency conflicts, full result retrieval |
| REQ-018–021 | Where conditional execution is enabled, the system shall bound parser allocation and all possible branch work before execution, retain existing isolation, and avoid adding active-job deadlines or fixed dispatch concurrency caps. | Amendment, D9 | Hostile source tests, both-branch accounting, real isolated worker checks |

No requirement promises every condition syntax on every engine. The proposed Aer subset and its explicit exclusions are in the design. Supporting new Aer custom gates, parameter bindings or nested control flow requires further evidence and a scope amendment.

## Gate sequence and traceability

1. Owner reviews this clarification and design amendment together.
2. If approved, update the parent decisions and task plan with new identifiers following TASK-010; map each task to the parent requirements above. Allocate write leases only then.
3. Implement and verify through PRs under the existing CI/review policy. Source acceptance and deployed acceptance remain separate gates.

The original TASK-001/TASK-006 evidence remains valid for the earlier subset. Their completion does not claim that classical conditions are implemented. The original rollout handoff remains independent work.
