# GPU discovery design amendment

**Status:** Approved for discovery on 2026-11-22. Scope is isolated measurement of scqubits and Google qsim on a single NVIDIA GPU.

## Overview

Start with an internal V100 if it meets the execution envelope. Use a larger internal allocation when available. Runpod via SkyPilot can fill individual coverage gaps, particularly cuStateVec on a supported device or the 30-qubit memory tier. Report the performance crossover and its practical cost for each operation.

## Architecture (components, responsibilities, and boundaries)

An operator-owned SkyPilot task runs a digest-pinned discovery image with the relevant native libraries. The task driver starts a fresh unprivileged numerical child for each timed request. The child imports only its selected engine, reads a frozen fixture, and writes a bounded result. The driver verifies results outside the timed window and exports a small structured receipt. Existing WQS adapters and fixtures supply reference behavior. Discovery-sized inputs run through the disposable harness with explicit bounds, using production QASM translation and result semantics where practical. A checked-in test copy may parameterize only the discovery size guard where production rejects the larger cases. Separate core-only experiments carry a separate label and cannot certify the WQS path.

## Data Flow (step-by-step; include a Mermaid sequence diagram when useful)

1. Freeze inputs, seeds, reference policy, build digests and thresholds. Record the scheduler's catalog and reservations, then select a resource tier using the [run sheet](run-sheet.md).
2. Check device compatibility, host and device limits, and numerical smoke cases on the scheduler-assigned GPU. Record exact identifiers and observed limits before scaling.
3. Run matched CPU and GPU arms on the allocated host. Preserve correctness results for controls, screening cases and confirmation trials. Export a receipt and reconcile teardown through SkyPilot.
4. Review the evidence and recommend a bounded operation/device matrix, or retain CPU for cells that miss the criteria.

## Public Interfaces (APIs, CLIs, schemas, config)

Discovery accepts only reviewed fixtures through a private task. No public API, schema, profile flag or exported production symbol is required. The [interface manifest](interfaces.json) and [module boundaries](module-boundaries.json) describe this discovery increment. The [existing contracts](../interfaces.json) continue to govern WQS.

## Data Model & Storage (including migrations and idempotency)

Each receipt records `run_id`, `case_id`, `trial`, `arm`, `source_sha`, `fixture_sha256`, `image_digest`, library and device identity, placement/request IDs, timestamps, outcome, phase timings, host RSS and GPU memory peaks, output bytes and correctness checks. Keep operator identity, actual cluster assignment, usage charges and raw logs in private artifacts. Store sanitized aggregate evidence in the spec after discovery. No database migration or change to WQS job idempotency is required.

## Concurrency, Ordering, and Consistency (if relevant)

Execute one simulation at a time and interleave CPU/GPU order using a fixed seed. Use one allocation at a time. Compare the current production CPU thread count and an optimized CPU comparator within eight allocated vCPUs, selecting the fastest correct comparator per cell on screening data and freezing it before confirmation. Capture machine load; reject interfered trial sets and report the exclusions. A warm worker still forks a fresh numerical child for each request.

## Failure Modes & Recovery (timeouts, retries, circuit breakers, degraded modes)

Treat unavailable capacity, unsupported driver/library, CPU fallback, nonconvergence, memory guard refusal, timeout and incorrect output as distinct outcomes. Scale one tier at a time. Use a 120-second per-trial screening timeout and an independently enforced session deadline for these fixtures. After a lost response, reconcile the exact request, job and owned allocation before retrying. Collect partial receipts where feasible and verify retirement via SkyPilot. No automatic CPU substitution for a GPU arm.

## Security Model (authn/z, permissions, secrets handling, injection defenses)

The operator owns the task and its cleanup. Use pinned images and fixed numeric models and circuits, isolated scratch, sanitized child environments and bounded output. Keep production credential mounts, registry secrets, callback tokens and external scheduler controls outside numerical children. Any image pull uses existing approved infrastructure. Local taints, reservations and existing workloads remain protected.

## Observability (signals, dashboards, alerts; what you will measure)

Record scheduling and image setup wall time separately from fresh-child startup, preparation, transfers, synchronized native execution and serialization. Include child wall time, host peak RSS, peak VRAM, output bytes, backend proof and cost per successful request including failed attempts. Timestamp every phase. For GPU timing, synchronize before stopping the timer. Capture kernel evidence for each backend and workload tier using a separate trace trial so profiling overhead does not bias timing. A GPU array or idle CUDA context alone cannot establish GPU computation.

## Rollout Plan (staged rollout, feature flags, backout)

Owner review precedes launch. Screen the smallest useful workload, grow each lane only after its measured and estimated memory checks pass, then confirm promising cells. Publish the results and request review of any production proposal. A later deployment would reuse the existing SkyPilot dispatcher and pool with explicit certified resource and device profiles, and would require exact-image GPU acceptance alongside existing CPU coverage and backup gates. Discovery backout terminates only its owned allocation and retains its evidence.

## Alternatives Considered (at least 2, with pros/cons)

| Alternative | Tradeoff |
| --- | --- |
| Keep CPU only | Zero implementation cost. Leaves the larger-size crossover and capacity limit unmeasured. |
| Run only on a cloud A100 | Simplifies driver and VRAM coverage. Spends money before testing internal capacity and leaves internal compatibility unknown. |
| Add a general GPU selector to WQS now | Makes experimentation convenient. Expands admission and fallback behavior before compatibility, memory bounds or benefit are proven. |

## Open Design Questions

- Confirm the V100 memory size, driver and compatible CUDA/CuPy pins before the first internal test. The scheduler snapshot omits those fields. Current cuStateVec lists Turing and newer GPUs. The V100 lane targets native CUDA, and cuStateVec screening requires a compatible newer allocation. [NVIDIA support matrix](https://docs.nvidia.com/cuda/cuquantum/latest/custatevec/index.html)
- Confirm which larger scqubits fixtures need a larger basis for the requested levels or operators to converge. A stress case can measure scaling, but does not justify a public limit by itself.
- Confirm stage-specific host and device memory estimates and the GPU child's address-space limit from initialization evidence. The previous 8 GiB exception was for smaller discovery fixtures. Treat 12 GiB and 48 GiB as proposed upper bounds for the internal and large discovery child respectively, subject to the dry memory review and a successful initialization proof. An incompatible runtime stops that cell.

## Simplicity Budget (maximum new modules, maximum new public interfaces, dependency addition policy, and a required "do nothing / smaller change" alternative that was evaluated)

At most two probe modules and two isolated image recipes across the two lanes. No new public interface or production dependency. Pin any added diagnostic dependency in its recipe. CPU-only is the smaller alternative and remains the recommendation for cells that fail to qualify.

## Rejected Abstractions (abstractions considered and explicitly rejected with rationale)

A general plugin framework, multi-GPU scheduler and persistent numerical child are out of scope. Fixed fixtures and one-device execution are sufficient to test the current WQS execution model and isolate each backend's costs.

## Verification Strategy (mapping to requirements and test strategy)

| Lane | Frozen screening matrix | CPU reference and correctness |
| --- | --- | --- |
| scqubits 4.3.1 | Repeat Transmon D61 and D511 as controls. Use the existing Fluxonium and FluxQubit parameters as anchors, plus one predeclared regime selected for basis convergence. Fluxonium D512, 1024, 2048, 4096, and conditionally 8192. FluxQubit `ncut` 10, 16, 22, 32 and conditionally 45 gives D441, 1089, 2025, 4225 and 8281. Request 8 or 32 levels. Screen spectrum with and without vectors, then a representative documented operator on each model. | Compare CuPy dense full solve/slice and sparse lowest eigenpairs where the method supports the actual matrix. Use WQS SciPy subset, plus qualified structure-aware CPU solvers where available. Include Hamiltonian creation and format conversion on both arms. Require energy agreement within `1e-9 GHz + 1e-10 × abs(reference)` and scaled residual and orthogonality infinity norms at most `1e-9`. Compare degenerate eigenspaces via principal angles or projector differences at `1e-7`, with no truncation through a degenerate block. Compare aligned operator matrices within `1e-8` of their reference scale. Check physical convergence at successive cutoffs separately from solver agreement. |
| qsimcirq 0.22.1 | Use 24, 26 and 28 qubits first, then 30 on the large tier. Generate nearest-neighbor entangling circuits at depths 20 and 60 from fixed seeds. Include an asymmetric analytic control for bit order, deterministic sampling and a GHZ sampling control. Exercise QASM2 and QASM3, selected-basis probabilities for 64 predeclared bitstrings, and terminal measurement sampling at 256 and 4096 shots. Start with native CUDA for probabilities and compare with cuStateVec where the pinned version supports the device. Test sampling backends separately. | Use matching qsim CPU precision and check smaller cases against an independent double-precision or analytic reference. Require amplitude and probability errors within `2e-6 + 2e-4 × abs(reference)` with additional nonzero amplitude/probability controls to catch underflow and wrong qubit ordering. Validate shot count, shape, support and bit order exactly. For stochastic cases, compare predeclared marginals and correlations with exact or matched CPU references using 99% simultaneous confidence intervals with Bonferroni adjustment within a cell. Require the intervals for differences to fit within ±0.05 at the higher shot count and size the pooled trial count before execution. Report repeatability on each backend separately. |

Freeze representative physical inputs, the independent reference availability and criteria before GPU runs. The scqubits comparison uses the same sorted eigenpair and operator contract. Each qsim input is translated once per request with the same seed, bitstrings and qubit order across arms. Keep parameter binding and public result fields covered by the compatibility controls. Google documents different GPU advantages for selected amplitudes and sampling. [scqubits eigensolver guide](https://scqubits.readthedocs.io/en/v4.3/guide/settings/ipynb/custom_diagonalization.html) · [Google qsim hardware guide](https://quantumai.google/qsim/choose_hw)

Use five fresh-child pairs per screened cell and 20 for a promising cell and its neighboring size. Review median, p90 and failures. A proposed speed cell passes if all correctness/resource checks pass, median fresh-child latency improves by at least **1.5× and 0.5 seconds**, and p90 is no worse than CPU. Use a paired bootstrap interval to flag uncertain wins and keep them unqualified. A size-extension cell can qualify separately when it fits the measured GPU envelope but exceeds a documented CPU service capacity bound. Such a claim requires independent correctness and an explicit latency/cost target in the results review. Show provisioning and queue delay beside the fresh-child comparisons. A GPU win after kernel warmup alone is insufficient.

For GPU-005, budget construction intermediates, native solver workspace, allocator pools, transfer copies and result encoding. One scqubits D8192 matrix requires 512 MiB in float64 or 1 GiB in complex128. A single qsim state vector requires 128 MiB, 512 MiB, 2 GiB and 8 GiB at 24, 26, 28 and 30 qubits respectively. These are lower bounds. Require estimates and measured peaks within 75% of the requested host RAM and verified GPU VRAM before advancing a tier. Include runtime initialization and guard the file bound independently. Keep a refusal result when a tier exceeds its envelope. GPU-001, GPU-002 and GPU-007 close with [run-sheet](run-sheet.md) and allocation ledger evidence. GPU-003 through GPU-006 close with versioned benchmark receipts and the [decision task](tasks.md#tasks-the-full-list).
