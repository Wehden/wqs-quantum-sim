# Physics simulation engines

Status: reconciled on 2026-11-02 against main `8d8b2d0486e0feba6b0cc7514836f67256d83cb5`, including merged PRs #60 through #64. Requirements/design and the original task plan are approved; G1 was accepted on 2026-10-26. G2 implementation is in progress. The owner confirmed that the JoSIM TASK-035/036 plan is already approved for execution. Physics worker/API integration and production acceptance remain gated.

The FluxxedUp proposal describes three device-level backends, rather than an executable simulator: QuTiP/Lindblad, scqubits and JoSIM. These complement the existing gate-level engines. The owner confirmed on 2026-10-25 that all three backends are in scope. Implementation may proceed in dependency order, but completion of this flow requires all three. GPU suitability is also in scope following the owner's explicit request; placement remains under WQS/SkyPilot control.

| Gate | Artifact | Status |
| --- | --- | --- |
| Context and requirements | [requirements.md](requirements.md) | Approved on 2026-10-25 |
| Design | [design.md](design.md), [JoSIM bounded-history amendment](discovery/josim-workspace.md) | Base approved on 2026-10-25; G1 amendment approved on 2026-10-26; JoSIM bounded-history direction approved |
| Capability discovery | [baseline](discovery/baseline.md), [Lindblad](discovery/lindblad.md), [scqubits](discovery/scqubits.md), [JoSIM](discovery/josim.md), [GPU](discovery/gpu.md) | Current compatibility and all-three constrained CPU references pass; declared-case GPU discovery is complete through SkyPilot. G1 decisions approved on 2026-10-26 |
| G1 review packet | [decisions](decisions.md), [module exports](interfaces.json), [import boundaries](module-boundaries.json) | Approved on 2026-10-26; TASK-007 onward authorized |
| Tasks | [tasks.md](tasks.md) | Original plan approved on 2026-10-25; owner confirmed existing TASK-035/036 execution approval on 2026-10-30 |
| Implementation | [Reconciled task ledger](tasks.md#execution-checklist-reconciled-2026-11-02) | Native Lindblad operators/states/drive grids, certified scqubits spectra/matrix elements, and the bounded JoSIM history patch/proof are merged. Solvers and final native admission certificates remain open |
| G2 CPU acceptance | TASK-011 through TASK-026 and approved TASK-035/036 | Open: solvers, numeric export, final native admission certification, worker/API integration and final combined-image proof remain |
| G3 GPU acceptance | TASK-027 through TASK-029 | Discovery decisions accepted; runtime/profile acceptance remains open. GPU work uses SkyPilot |
| G4 release acceptance | TASK-030 through TASK-034 and existing guarded CD | Open: physics CI/docs, combined audit and exact-release live acceptance follow runtime integration |

TASK-012 (bounded Lindblad drive grids) and TASK-036 (bounded native JoSIM history)
are complete and merged. The next parallel lanes are TASK-013 deterministic
Lindblad evolution and the remaining TASK-020 JoSIM admission certificate, under
their existing disjoint write leases. Scqubits export, TASK-019, waits for the TASK-016 Lindblad static
solver. No new approval gate is required for these already-approved tasks;
material scope or numerical-contract changes still return for review.

The existing circuit release at `8d8b2d0` passed all eight live cases, native
SkyPilot checks and cleanup after recorded capacity recovery. Its CD receipt is
recorded in the [latest reconciliation](tasks.md#merged-stage-and-operational-receipt-2026-11-02).
Physics runtime and release acceptance remain separate gates.

Specifications stay local for review until implementation, following the established WQS spec workflow. Implementation uses PRs, automatic review, at most two review rounds unless the owner grants an exception, and owner merges. CI defaults to Standpunkt with hosted fallback. Existing backup/bootstrap gates and credentials remain in place.

Primary proposal: `jheckey/fluxxedup`, commit `ae0cea45d244f9a6ee3204516fe6aef44340a34f`, `docs/cluster/job-design.md` and `agent-instructions.md`. WQS owns the integration architecture. The adaptation decisions in the design take precedence over unimplemented suggestions in that proposal once approved.
