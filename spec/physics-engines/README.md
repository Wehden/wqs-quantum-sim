# Physics simulation engines

Status: reconciled on 2026-10-30 against main `943bb0ab773630259a5dcfbb371d984d218f4a77`, including merged PRs #49, #50, #51 and #52. Requirements/design and the original task plan are approved; G1 was accepted on 2026-10-26. G2 implementation is in progress. The owner confirmed that the JoSIM TASK-035/036 plan is already approved for execution. Physics worker/API integration and production acceptance remain gated.

The FluxxedUp proposal describes three device-level backends, rather than an executable simulator: QuTiP/Lindblad, scqubits and JoSIM. These complement the existing gate-level engines. The owner confirmed on 2026-10-25 that all three backends are in scope. Implementation may proceed in dependency order, but completion of this flow requires all three. GPU suitability is also in scope following the owner's explicit request; placement remains under WQS/SkyPilot control.

| Gate | Artifact | Status |
| --- | --- | --- |
| Context and requirements | [requirements.md](requirements.md) | Approved on 2026-10-25 |
| Design | [design.md](design.md), [JoSIM bounded-history amendment](discovery/josim-workspace.md) | Base approved on 2026-10-25; G1 amendment approved on 2026-10-26; JoSIM bounded-history direction approved |
| Capability discovery | [baseline](discovery/baseline.md), [Lindblad](discovery/lindblad.md), [scqubits](discovery/scqubits.md), [JoSIM](discovery/josim.md), [GPU](discovery/gpu.md) | Current compatibility and all-three constrained CPU references pass; declared-case GPU discovery is complete through SkyPilot. G1 decisions approved on 2026-10-26 |
| G1 review packet | [decisions](decisions.md), [module exports](interfaces.json), [import boundaries](module-boundaries.json) | Approved on 2026-10-26; TASK-007 onward authorized |
| Tasks | [tasks.md](tasks.md) | Original plan approved on 2026-10-25; owner confirmed existing TASK-035/036 execution approval on 2026-10-30 |
| Implementation | [Reconciled task ledger](tasks.md#execution-checklist-reconciled-2026-10-30) | Native Lindblad operators and certified scqubits spectra/matrix elements are merged. JoSIM validation/native staging is merged; its workspace certificate remains open |
| G2 CPU acceptance | TASK-011 through TASK-026 and approved TASK-035/036 | Open: states/pulses/solvers, numeric export, bounded JoSIM history, worker/API integration and final combined-image proof remain |
| G3 GPU acceptance | TASK-027 through TASK-029 | Discovery decisions accepted; runtime/profile acceptance remains open. GPU work uses SkyPilot |
| G4 release acceptance | TASK-030 through TASK-034 and existing guarded CD | Open: physics CI/docs, combined audit and exact-release live acceptance follow runtime integration |

Next, TASK-011 (Lindblad states and collapse operators) and TASK-035 (JoSIM history
audit) can run in parallel under disjoint write leases. TASK-036 follows a
successful history audit. Scqubits export, TASK-019, waits for the TASK-016
Lindblad static solver. No new approval gate is required for these already-approved
tasks; a material scope or numerical-contract change still returns for review.

Specifications stay local for review until implementation, following the established WQS spec workflow. Implementation uses PRs, automatic review, at most two review rounds unless the owner grants an exception, and owner merges. CI defaults to Standpunkt with hosted fallback. Existing backup/bootstrap gates and credentials remain in place.

Primary proposal: `jheckey/fluxxedup`, commit `ae0cea45d244f9a6ee3204516fe6aef44340a34f`, `docs/cluster/job-design.md` and `agent-instructions.md`. WQS owns the integration architecture. The adaptation decisions in the design take precedence over unimplemented suggestions in that proposal once approved.
