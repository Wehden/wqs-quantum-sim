# Discovery task plan

**Status:** Owner approved the amendment and run sheet on 2026-11-22. Existing TASK-005 and first-release TASK-027/028/029 keep their historical scope and evidence.

## Task Graph Overview (short)

TASK-037 and TASK-038 can prepare independently in their leases. Each lane must pass CPU preparation before its SkyPilot session. TASK-039 runs eligible lanes sequentially, then TASK-040 reports the disposition.

## Tasks (the full list)

### TASK-037 Prepare scqubits fixtures and CPU reference

- **Goal and steps.** Freeze the scqubits models, requested levels and convergence cases. Build the digest-pinned image, implement dense/sparse comparisons and verify the reference and memory bounds on CPU.
- **Dependencies and mode.** Owner review of the design before execution. Test engineer. Covers GPU-003 and GPU-005.
- **Risk.** High. Large Hamiltonian intermediates and wrong low-energy eigenpair selection can distort results.
- **Verification.** Prove the D61 and D511 controls and fault checks for wrong eigenpairs, oversized inputs, missing GPU provenance and excessive outputs. Freeze the CPU comparator selection rule and run the CPU-only path in the pinned image. Record the exact commands and digests.
- **Backout.** Retain failed evidence and skip this lane until its CPU reference passes.
- **Change budget.** 4 files, 12 new test-only symbols, interface policy `none`.

```text
^tests/fixtures/physics/gpu-scqubits/(Dockerfile|probe\.py|cases\.json|test_probe\.py)$
```

### TASK-038 Prepare qsim fixtures and CPU reference

- **Goal and steps.** Freeze QASM2/QASM3 circuit, bitstring and sampling controls. Build and pin CPU and GPU qsim targets from the same source. Record native build flags and dependencies. Exercise the existing parsing and result-shape semantics through CPU fixtures.
- **Dependencies and mode.** Owner review of the design before execution. Test engineer. Covers GPU-004 and GPU-005.
- **Risk.** High. Device fallback, host state-vector copies and measurement-order differences can misstate coverage.
- **Verification.** Check bit-order and probability controls against independent references, verify sampling output and storage bounds, and demonstrate failure for unsupported GPU mode. Record exact CPU commands, source and artifact digests.
- **Backout.** Retain failing cases and hold this lane for a corrected build.
- **Change budget.** 4 files, 12 new test-only symbols, interface policy `none`.

```text
^tests/fixtures/physics/gpu-qsim/(Dockerfile|probe\.py|cases\.json|test_probe\.py)$
```

### TASK-039 Execute the reviewed SkyPilot run sheet

- **Goal and steps.** Refresh the live inventory and quotes, validate task definitions, freeze code and fixture IDs, then schedule the prepared lanes. Collect cold and warm measurements and reconcile owned allocations and charges. Use the internal tier first and the costed fallback for eligible gaps.
- **Dependencies and mode.** Approved launch scope and each lane's completed CPU preparation. Executor. Covers GPU-001 through GPU-007.
- **Risk.** High. Capacity can change and failed provisioning can leave billable resources.
- **Verification.** Use the [run-sheet checkpoints](run-sheet.md#operator-checkpoints). Prove scheduler and numerical identities, resource bounds, paid-stop behavior and teardown. Report timed-out, failed and uncovered cells.
- **Backout.** Cancel or tear down owned work through SkyPilot. Preserve receipts and stop paid submissions if reconciliation fails.
- **Change budget.** 0 production or tracked files, 0 symbols, interface policy `none`. Keep task definitions, provider configurations and receipts in private operator storage. No tracked write lease.

### TASK-040 Review crossover and promotion scope

- **Goal and steps.** Report each measured cell with correctness, latency, host/device memory, full cost and any uncovered stages. Recommend an operation-specific CPU/GPU disposition and draft a separate implementation proposal only for cells that qualify.
- **Dependencies and mode.** TASK-039 evidence or a documented blocker. Test engineer. Covers GPU-003 through GPU-007.
- **Risk.** Medium. A kernel-only win, unrepresentative basis or uncertain sample can lead to a poor production profile.
- **Verification.** Apply the frozen criteria to paired runs and audit receipts against native device IDs, scope, budget and cleanup. Connect each recommended cell to its supporting evidence.
- **Backout.** Retain the CPU disposition for unqualified cells.
- **Change budget.** 1 spec file, 0 symbols, interface policy `none`.

```text
^spec/physics-engines/gpu-discovery/results\.md$
```

## Definition of Done Checklist

- [x] Owner reviews the requirements, design and launch scope.
- [x] Fixture, reference and image identities are pinned and CPU preparation passes.
- [x] Live placement, compatibility and any Runpod quote are checked before submission.
- [x] Independent CPU cutoff comparisons cover the tested scqubits operators. The eight-thread CPU reference for the 28-qubit qsim fixture fits the trial deadline.
- [ ] Full matrix evidence remains open for larger scqubits GPU tiers, incomplete qsim cells and additional traces. The latest SkyPilot attempts found insufficient host CPU or unavailable Runpod capacity.
- [x] Every owned allocation is reconciled. The quote-based estimate is below the cap, including the ancillary allowance. The provider invoice was unavailable through SkyPilot.
- [x] Results and remaining qualification work are recorded for owner review.

## Execution Notes (tooling, environment, checkpoints)

The [results](results.md) record the completed screening cells and gaps. The internal RTX 3060 confirmed the frozen 26-qubit case with 20 pairs, and the 24-qubit control missed the speed criterion. [Deployment plan](deployment-plan.md) covers subsequent exact-image acceptance. TASK-037 and TASK-038 produced frozen recipes, CPU controls and the executed synthetic fixtures. TASK-039 reached both combined-job phase deadlines, retained the evidence and tore down its allocation. It completed the bounded qsim confirmation for the screened CUDA case and its neighbor. TASK-040 records the resulting CPU/GPU disposition for owner review.

Use current main and standalone fixtures under the task leases. Keep shared reporting with the coordinator. Existing production suites remain independent. Use the existing SkyPilot authority for all discovery GPU work and retain private run records without committing credentials, provider identifiers or hostnames. Report launch and teardown ID mismatches before further requests.

## Traceability (REQ IDs -> TASK IDs)

| Requirements | Tasks |
| --- | --- |
| GPU-001, GPU-002 | TASK-039 |
| GPU-003 | TASK-037, TASK-039, TASK-040 |
| GPU-004 | TASK-038, TASK-039, TASK-040 |
| GPU-005 | TASK-037, TASK-038, TASK-039, TASK-040 |
| GPU-006, GPU-007 | TASK-039, TASK-040 |
