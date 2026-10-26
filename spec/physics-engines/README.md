# Physics simulation engines

Status: requirements and design approved by the owner on 2026-10-25. The implementation task plan and parallel execution were approved on 2026-10-25. Baseline and discovery execution has started; deployment remains gated. Prepared on 2026-10-25 against WQS main `d941d3f5c3fc3360b92477ff4df681d83cc869f7`.

The FluxxedUp proposal describes three device-level backends, rather than an executable simulator: QuTiP/Lindblad, scqubits and JoSIM. These complement the existing gate-level engines. The owner confirmed on 2026-10-25 that all three backends are in scope. Implementation may proceed in dependency order, but completion of this flow requires all three. GPU suitability is also in scope following the owner's explicit request; placement remains under WQS/SkyPilot control.

| Gate | Artifact | Status |
| --- | --- | --- |
| Context and requirements | [requirements.md](requirements.md) | Approved on 2026-10-25 |
| Design | [design.md](design.md) | Approved on 2026-10-25 |
| Capability discovery | [baseline](discovery/baseline.md), [Lindblad](discovery/lindblad.md), [scqubits](discovery/scqubits.md), [JoSIM](discovery/josim.md), [GPU](discovery/gpu.md) | Current compatibility and all-three constrained CPU references pass; GPU allocation and final contract decisions remain open |
| Tasks | [tasks.md](tasks.md) | Approved on 2026-10-25 |
| Implementation | Feature PRs | Baseline and discovery probes implemented; runtime integration waits for G1 |
| Release | Existing guarded CD and live acceptance | Not started |

Specifications stay local for review until implementation, following the established WQS spec workflow. Implementation uses PRs, automatic review, at most two review rounds unless the owner grants an exception, and owner merges. CI defaults to Standpunkt with hosted fallback. Existing backup/bootstrap gates and credentials remain in place.

Primary proposal: `jheckey/fluxxedup`, commit `ae0cea45d244f9a6ee3204516fe6aef44340a34f`, `docs/cluster/job-design.md` and `agent-instructions.md`. WQS owns the integration architecture. The adaptation decisions in the design take precedence over unimplemented suggestions in that proposal once approved.
