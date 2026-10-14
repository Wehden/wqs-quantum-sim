# QASM support specification

Status: requirements and design approved by the owner on 2026-10-13. The task plan was also approved on 2026-10-13. Implementation and local verification are complete; PR review/CI and owner merges remain.

This flow develops the feature targets in [roadmap issue #2](https://github.com/Wehden/wqs-quantum-sim/issues/2). The issue is a tracker; the repository contains the specification and decisions.

| Stage | Artifact | Exit condition | Status |
| --- | --- | --- | --- |
| Context | [context.md](context.md) | Owner accepts scope, baseline and reuse constraints | Accepted as supporting scope on 2026-10-13 |
| Requirements | [requirements.md](requirements.md) | Owner accepts observable behavior and exclusions | Approved on 2026-10-13 |
| Design | [design.md](design.md) | Reusable integration selected with version-specific evidence; API compatibility and result semantics reviewed | Approved on 2026-10-13; capability evidence still required |
| Tasks | [tasks.md](tasks.md) | PR-sized implementation and validation steps trace to requirements | Approved; TASK-001 through TASK-009 locally verified |
| Implementation | Separate feature PRs | Tests and review satisfy the approved tasks | Implemented; PR gates pending |
| Release | Validation evidence and deployment record | Owner merges; approved deployment and smoke checks recorded | Not started |

Next action: complete PR review/CI and owner merges before release handoff. Requirements/design approval accepts the proposed direction and contracts, while retaining the recorded discovery prerequisites. Tasks begin with executable capability evidence and resolution of affected open decisions.

Specification revisions stay local for owner review until implementation. Implementation and configuration changes use PRs. Existing automatic review triggers apply without manual duplicates; allow at most two review rounds and escalate remaining findings. Notify the owner if CI fails; missing CI is not passing. The owner merges. Follow the existing local-first CI and private deployment ownership policies.

Review record: [review.md](review.md). Design boundaries: [interfaces.json](interfaces.json) and [module-boundaries.json](module-boundaries.json).

Execution evidence: [validation.md](validation.md).
