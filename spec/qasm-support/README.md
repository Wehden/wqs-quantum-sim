# QASM support specification

Status: context and requirements drafted for owner review. No implementation is authorized by this spec PR.

This flow develops the feature targets in [roadmap issue #2](https://github.com/Wehden/wqs-quantum-sim/issues/2). The issue is a tracker; the repository contains the specification and decisions.

| Stage | Artifact | Exit condition | Status |
| --- | --- | --- | --- |
| Context | [context.md](context.md) | Owner accepts scope, baseline and reuse constraints | Draft |
| Requirements | [requirements.md](requirements.md) | Owner accepts observable behavior and exclusions | Draft |
| Design | design.md, created after context/requirements review | Reusable integration selected with version-specific evidence; API compatibility and result semantics reviewed | Not started |
| Tasks | tasks.md, created after design review | PR-sized implementation and validation steps trace to requirements | Not started |
| Implementation | Separate feature PRs | Tests and review satisfy the approved tasks | Not started |
| Release | Validation evidence and deployment record | Owner merges; approved deployment and smoke checks recorded | Not started |

Next action: review context and requirements. The subsequent design investigation should run representative circuits against existing parsers/adapters and record actual gaps. It must not start by designing a custom compiler or choosing a new backend without evidence.

All changes use PRs. Existing automatic review triggers apply without manual duplicates; allow at most two review rounds and escalate remaining findings. Notify the owner if CI fails; missing CI is not passing. The owner merges. Follow the existing local-first CI and private deployment ownership policies.
