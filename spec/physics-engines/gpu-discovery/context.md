# Larger GPU workloads

**Status:** Owner approved the discovery scope and launch on 2026-11-22.

## Problem Statement

The previous scqubits benchmark demonstrated native GPU diagonalization. Warm GPU operations were about 5.02× and 1.28× slower than CPU at Transmon dimensions 61 and 511 on an RTX 4090. Larger-workload crossover remains unmeasured. WQS at baseline `cb3e557459421e6b4f33e5b412b50719609ac55b` supports scqubits raw bases through 512 and qsim circuits through 26 qubits. Its qualified production GPU matrix is empty. [Previous GPU measurements](../discovery/gpu.md#corrected-timing-receipt-2026-10-26)

## Goals (bullet list, measurable when possible)

- Measure larger scqubits eigensystems and Google qsim state-vector workloads.
- Identify operation, size and hardware ranges that meet correctness, latency and resource requirements.
- Test first on eligible internal GPUs, with up to $25 in aggregate Runpod spend through SkyPilot for gaps in coverage.

## Non-Goals / Out of Scope

Other engines, multiple GPUs per job, persistent simulator state, production GPU activation and public API or input-limit changes.

## Users & Use-Cases

WQS operators assess devices and profiles. WQS users with larger circuit or superconducting-device problems benefit from subsequent qualification of workloads that are faster or fit within a supported GPU profile.

## Constraints & Invariants (include constitution items and platform constraints)

The owner reviews this packet before any GPU launch. The existing Laminar SkyPilot authority schedules all GPU work using resource requests and current placement policy. Discovery uses one owned, non-preempting allocation at a time. Synthetic fixtures and the current engine output contracts govern the benchmarks. Existing production credentials, workloads and backup/release gates remain in force.

## Success Metrics & Acceptance Criteria

Document a pass, fail or unmeasured result per operation and size range. Performance claims require paired fresh-child measurements, correct results and device execution proof. Size-extension claims require measured resource bounds and correctness evidence. The [design](design.md#verification-strategy-mapping-to-requirements-and-test-strategy) defines thresholds.

## Risks & Edge Cases

Free scheduler slots can disappear before placement. V100 memory, CUDA compatibility and native kernel support affect which cells fit. scqubits host construction and qsim state-vector copies can dominate execution. Runpod billing can continue through a stalled or disconnected launch.

## Observability / Telemetry expectations

Retain image and fixture digests, request and allocation identity, native backend evidence, phase timing, peak memory, case outcome, cost and verified teardown.

## Rollout & Backward Compatibility (if applicable)

Run isolated discovery first. Review results before proposing any production GPU profile. Existing CPU clients use the current request and result contracts throughout.

## Open Questions (with owner and how to resolve)

- The discovery operator verifies the internal GPU and driver fit from live scheduler and allocated-device receipts.
- The benchmark author confirms the memory envelope and freezes fixture inputs during CPU preparation.
- The owner reviews the proposed acceptance thresholds and budget before execution and reviews promotion separately.

## Minimal Change Intent (existing modules expected to absorb the change, abstractions explicitly out of scope unless later approved, API surface that must remain unchanged unless explicitly required)

Use disposable harnesses and the existing references. Exclude a second scheduler, API, native execution service or GPU pool. Use the current WQS dispatcher and result contracts as the baseline for any later design.

## Glossary (define overloaded terms)

**Cell** is an engine, operation, backend, device and input tier combination. **Fresh child** is a new isolated numerical process on an already allocated worker. **Crossover** is the smallest tested range at which a GPU cell meets the proposed latency or size-extension criterion.

Read [requirements](requirements.md), [design](design.md), [tasks](tasks.md) and the [costed run sheet](run-sheet.md) together. [Production device matrix](../discovery/gpu-acceptance.md)
