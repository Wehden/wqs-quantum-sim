# GPU discovery: initialization boundary under investigation

Status: TASK-005 is **incomplete**, checked on 2026-10-26 after the Lindblad and scqubits CPU references passed. A real allocated GPU failed CUDA initialization under the current 3 GiB address-space limit; the SkyPilot initialization sweep is complete, while the proposed larger numerical discovery profile awaits owner approval. Solver suitability and speedup remain unknown. No GPU capability, production worker image or resource-policy change is enabled by this report.

## Candidate paths

- scqubits 4.3.1 has native `esys_cupy_dense` and `esys_cupy_sparse` methods. Dense diagonalization constructs the Hamiltonian on the host, transfers it to CuPy, computes the full eigensystem and copies selected results back. The [upstream diagonalization guide](https://scqubits.readthedocs.io/en/latest/guide/settings/ipynb/custom_diagonalization.html) describes these methods. The CPU fixtures and native-source hash are in [scqubits.md](scqubits.md). A device-free import is insufficient to establish benefit or correctness.
- QuTiP's CuPy plugin was inspected at upstream commit `bd471ca0581fcd9a84e613775092bca418d0b66a`, version `0.1.0.dev0`, BSD-3-Clause. Its [installation instructions and CPU-conversion warning](https://github.com/qutip/qutip-cupy/blob/bd471ca0581fcd9a84e613775092bca418d0b66a/README.md) state that it is unreleased and that missing specializations can fall back to CPU. No eigensolver or matrix-exponential specialization is registered in the inspected `__init__.py`. GPU matrix storage therefore does not establish native GPU spectrum, propagator or ODE execution. Solver coverage needs operation-specific execution and transfer/fallback evidence.
- CuPy CUDA 12.x package candidate metadata reports `cupy-cuda12x==14.2.0`; it is now installed in the tested disposable fixture, not a combined-worker pin. Compatibility with NumPy 2.5.4, QuTiP 5.3.1, the plugin revision and the allocated device's driver must be verified together. Pinned candidate runtime/plugin versions appear below; successful GPU numerical execution is not established.
- JoSIM has no established native GPU path in the source examined by its discovery lane. It remains CPU for this design.

## Placement attempt and observations

Commands used the existing remote SkyPilot 0.13.0 API server, with a private disposable client configuration selecting the existing Kubernetes context and job namespace. No new API server or scheduler was created. The endpoint and context names belong in private operational configuration and are omitted here.

1. The SkyPilot Kubernetes credential check passed. Its GPU inventory nevertheless reported only one labeled NVIDIA model, with `0 of 0` GPU resources. Kubernetes separately advertised ten NVIDIA device resources across five nodes. This is a placement discrepancy, not proof that those devices are free or ready for this workload.
2. A real SkyPilot launch was attempted for an owned discovery cluster, requesting one RTX5080, 2 CPUs and 4 GB memory. Its sole diagnostic command was `nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv`. Ten-minute idle autodown and down-after-job were requested. This was an allocation diagnostic, not a solver benchmark.
3. Launch failed before provisioning with `ResourcesUnavailableError`: no Kubernetes instance satisfied the requested resources. A subsequent SkyPilot status lookup confirmed that the owned discovery cluster did not exist. There was no benchmark allocation to terminate, and no existing workload was stopped.

A portable form of the diagnostic is below. Set the private variables to the approved existing configuration and context, and use a unique owned cluster name:

```bash
SKYPILOT_CONFIG="$PRIVATE_SKY_CONFIG" sky check kubernetes
SKYPILOT_CONFIG="$PRIVATE_SKY_CONFIG" sky gpus list --infra k8s --all
SKYPILOT_CONFIG="$PRIVATE_SKY_CONFIG" sky launch \
  --infra "k8s/$PRIVATE_CONTEXT" --gpus RTX5080:1 --cpus 2 --memory 4 \
  --cluster "$OWNED_DISCOVERY_CLUSTER" --idle-minutes-to-autostop 10 --down -y \
  'nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv'
SKYPILOT_CONFIG="$PRIVATE_SKY_CONFIG" sky status "$OWNED_DISCOVERY_CLUSTER"
```

The locally installed SkyPilot implementation was examined at its reported source revision `b1431e52d97c22e9bb8fa8b67f162543754ddaf5`. In `sky/provision/kubernetes/utils.py`, `_gpu_resource_key_helper` scans supported resource keys in AMD-then-NVIDIA order and returns the first present key; `CUSTOM_GPU_RESOURCE_KEY` can override the choice in the executing process. The observed mixed-vendor cluster and inventory output are consistent with that mechanism. The initial WQS check did not inspect the remote environment. Laminar subsequently confirmed that the existing API server had no NVIDIA override; its managed correction is [Laminar PR #170](https://github.com/Wehden/laminar/pull/170), commit `720fb5f0122649964627ac6d7cda6998dce61030`. It was merged on 2026-10-26 and Laminar applied the managed NVIDIA override and controlled API-server restart. Existing local cards still have consumers; no local test allocation was taken from them. Several NVIDIA nodes also lack the SkyPilot-recognized accelerator label. Neither fact is resolved by changing a simulation CPU or memory request.

On 2026-10-26 the owner authorized Laminar to fix placement while preserving existing GPU workloads and credentials. Laminar prepared the managed fix and a non-preempting client profile. Physical occupancy checks found existing consumers on every inspected NVIDIA card; a free logical reservation or free VRAM is not an exclusive spare allocation. The labeled RTX5080 is occupied, and WQS must not preempt its consumer. Placement configuration and physical capacity are therefore separate gates. No node labels, server overrides, running consumers, production configuration or existing credentials were changed by WQS discovery. Private logs remain outside this repository; they must be sanitized before sharing.

## Benchmark protocol once placement works

Use the actual CPU cases in [lindblad.md](lindblad.md) and [scqubits.md](scqubits.md), preserving physical models, precision, output semantics and numerical tolerances. Predeclare small and larger cases before measuring: Transmon raw dimensions 61 and 511 for scqubits, and the fixed two-level Rabi plus 256-level dense spectrum for QuTiP. Additional operation cells need separate evidence. Do not expand accepted dimensions because a GPU is present.

For each candidate cell, run at least three fresh-process cold trials and three warm repeats on the same allocated device. Measure scheduling/provisioning separately from imports, context creation, host construction, transfer, native solution, synchronization and CPU result copy. Compare float64/complex128 results against the CPU references; compare degenerate subspaces through projectors/residuals, not arbitrary eigenvector phases. Track host RSS, device/driver/runtime identity, VRAM peak and complete output bytes. Record unsupported specialization or CPU conversion explicitly. A faster matrix multiply is not a successful GPU ODE benchmark.

Test initialization and execution under the current sanitized-child environment, 3 GiB address-space and 12 MiB file limits first. Device visibility must follow the scheduler's allocation. A CUDA virtual-address failure needs a measured, bounded replacement policy and the required design review; do not remove the limit to make a benchmark pass. GPU equivalence, seed behavior, useful speedup, native operation coverage and limit compatibility are all **unmeasured** here.

The disposable fixture and CPU references below are now tested. A later owner-authorized serverless allocation reached CUDA initialization and failed as recorded below. Device-free checks and completed transport requests are not GPU acceptance. TASK-005 still needs initialization-boundary evidence and successful numerical execution. TASK-006 cannot mark the GPU discovery gate complete from this report, nor conclude that GPUs are unhelpful.


## Disposable candidate fixture, 2026-10-26

`tests/fixtures/physics/gpu/Dockerfile` builds the fixed `probe_gpu.py` without modifying the production dependency lock or worker. Supply `DISCOVERY_BASE` as an immutable operator-approved base; the tested base image ID was `sha256:17fec9e20e50023882509aae2c52b536fead249c1e5bab4948daca712d678562`. Its registry address is private configuration. The resulting local image ID is `sha256:5d65e7b780162d05e7500f6b06f3305c7d93b08e88d2b602d39a3e663c4fb3fd` (7,796,223,789 bytes including its existing base). This is a discovery artifact, not a production image or published GPU capability.

The image pins Python 3.12.3, NumPy 2.5.4, SciPy 1.18.1, QuTiP 5.3.1, scqubits 4.3.1, CuPy CUDA12x 14.2.0, CUDA toolkit wheels 12.9.2.0, and the inspected QuTiP-CuPy commit. Direct CUDA component versions are explicit in the Dockerfile. The complete resolved manifest is `/opt/discovery-packages.txt` in the image. `uv pip check` passed. Driver/runtime compatibility and native operation support remain hardware checks. The installed CUDA wheels do not prove GPU execution.

```bash
docker build --build-arg DISCOVERY_BASE="$CI_IMAGE" \
  -f tests/fixtures/physics/gpu/Dockerfile \
  -t wqs-physics-gpu-discovery:local .
docker run --rm --cpus 2 --memory 4g --memory-swap 4g \
  wqs-physics-gpu-discovery:local --backend cpu --suite
```

The suite starts three fresh sanitized children per predeclared cell. Each performs its first operation plus three warm repeats, giving 12 fresh children and 48 numerical checks. Every child sets the existing 3 GiB address-space and 12 MiB file limits before native imports. CPU references use the same physical inputs, float64/complex128 and output representation as the candidate GPU cells. CPU spectrum references are independent SciPy eigensystems. Transmon comparisons check energies, residuals and eigenspace projectors; Rabi uses the analytic population. Numerical cases have no option to disable or raise these limits; the separate initialization-only profiles below cannot execute a solver.

All **12 CPU children and 48 checks passed** with two CPUs, 4 GiB memory and no additional swap. No existing tests or assertions changed. These measurements are on the local discovery host; CPU/GPU speedups must use measurements on the same eventual allocated worker.

| Fixed cell | Median first operation seconds | Median warm operation seconds | Maximum numerical error | Peak RSS KiB | Numeric NPZ bytes |
|---|---:|---:|---:|---:|---:|
| Transmon D61, 6 eigenpairs | 0.000324 | 0.000189 | 9.51e-14 GHz | 257072 | 3490 |
| Transmon D511, 6 eigenpairs | 0.006649 | 0.005874 | 4.41e-12 GHz | 280748 | 25090 |
| Two-level Rabi, 401 samples | 0.002702 | 0.002311 | 7.49e-9 population | 255408 | 3476 |
| QuTiP dense spectrum D256 | 0.003865 | 0.003633 | 2.85e-14 GHz | 261344 | 2316 |

Operation times exclude imports and the independently computed reference. Fresh-process wall time was 2.24–2.61 seconds. JSON receipts also separate host construction and reference time. Output is numeric NPZ for these benchmark arrays; these sizes are not final API/base64 output budgets.

A device-free negative test reached `cuda_initialization` under the unchanged limits and returned `cudaErrorInsufficientDriver`. This is the expected absence of an allocated driver/device in that local container. It proves bounded failure reporting only; it does not diagnose the cluster driver or establish the address-space limit's compatibility with a real GPU. No GPU computation, resource sweep or existing workload alteration occurred in this test.

The GPU probe selects scqubits' actual `esys_cupy_dense`, counts calls receiving CuPy device arrays, synchronizes its native eigensolver and records host-to-device timing separately. The QuTiP spectrum cell counts host conversions because the candidate plugin lacks an eigensolver specialization. The Rabi cell counts device-backed matrix operations and host conversions; a mixed execution path must remain labeled as such. Receipts record actual device/runtime/driver, CuPy allocator usage and device free memory. These fields are populated only after real initialization. Allocator-resident bytes are not a complete VRAM peak: driver/context, solver workspace and device-wide consumers require separate allocation-scoped observation, without inspecting or perturbing unrelated workloads.

## Historical direct transport, withdrawn

An earlier direct Runpod Serverless test bypassed the required SkyPilot scheduler. That transport is withdrawn from the plan, and its handler/SDK Docker target were removed from the proposed source. The retained private receipts show 12 CPU children / 48 checks passed, while all 12 GPU children failed at CUDA initialization under 3 GiB AS. Those receipts do not establish accepted scheduling or production GPU capability. The existing idle endpoint is retained as requested and receives no further direct submissions. No direct provider operation is an approved fallback.

## SkyPilot Runpod discovery, 2026-10-26

All subsequent GPU probes and lifecycle operations use the existing SkyPilot API server. Its installed 0.13.0 Runpod provider provisions short-lived Pods. The repeatable fixture is `tests/fixtures/physics/gpu/skypilot.yaml`; invoke it from the repository root with private existing API configuration and a unique test-owned cluster name. It requests one RTX4090, at least four CPUs and 16 GB host memory, and a 40 GB disposable disk. No new public application ingress or credentials are created. Use `--down` and explicitly run `sky down` on the owned cluster if provisioning/setup fails.

The first RTX3090 attempt exhausted the provider's catalog regions without capacity. SkyPilot subsequently allocated an RTX4090 in EU-RO-1. The catalog price was $0.89/hour; this is a catalog estimate, not an account billing receipt. The owner-approved total test budget remains $25. No local GPU consumer was displaced. The observed device reports NVIDIA GeForce RTX 4090, driver 595.91.07 and 24,564 MiB VRAM.

SkyPilot's default `runpod/base:1.0.2-ubuntu2204` image was used for this trial, with numerical dependencies installed into a separate virtual environment. The observed Python installation was 3.12.15. The checked-in fixture pins 3.12.13 for repeatability; it has not yet been rerun at that patch version. NumPy 2.5.4, SciPy 1.18.1, QuTiP 5.3.1 and scqubits 4.3.1 are checked at runtime. CuPy is 14.2.0, CUDA wheels are 12.9.2 and the QuTiP-CuPy source is pinned to `bd471ca0581fcd9a84e613775092bca418d0b66a`. This mutable-base setup is a discovery environment, not an immutable production worker receipt.

The exact executed probe SHA256 is `20826a6b28152e6d29eb04beddba9d77cf03f563d65882b79499d4887e6d018e`. The first scheduled request failed on a copied file's permissions before native imports. After a fixed chmod, SkyPilot scheduled a second request on the same owned allocation. Each numerical child runs as a dedicated nonroot user with sanitized environment and unchanged 3 GiB AS / 12 MiB file limits. The containing Runpod allocation is not a verified 2-CPU/4-GiB CPU profile: observed affinity includes 48 logical CPUs and the cgroup-v2 diagnostic files were unavailable. Native-library thread counts are fixed to one. Do not use these results as a CPU-profile acceptance claim.

### Initialization-only diagnostic

The fixed `cuda_init` case runs fresh children with AS limits 3, 8, 16 and 64 GiB. Larger profiles apply only to initialization, never a numerical solver. File limits and child sanitization remain active. There is no unlimited profile or arbitrary caller-selected limit.

| AS bound GiB | Native result | Process virtual size after init / at failure | Resident memory |
| --- | --- | --- | --- |
| 3 | `cudaErrorMemoryAllocation` during initialization | 2,678,412 KiB at failure | 433,184 KiB at failure; peak 502,640 KiB |
| 8 | Initialized | 4,584,112 KiB | 506,768 KiB |
| 16 | Initialized | 6,682,288 KiB | 507,020 KiB |
| 64 | Initialized | 6,683,320 KiB | 506,896 KiB |

The assigned device had only 1 MiB used before initialization. Fresh controlled children differ by their fixed AS profile: initialization succeeds at 8 GiB and fails at 3 GiB on this device/runtime. The observations support an address-space constraint, not physical GPU exhaustion. Successful initialization does not establish numerical correctness, runtime coverage, solver workspace or acceleration benefit. No numerical child has been granted a larger AS limit.

### Numerical and lifecycle receipt

The second SkyPilot job finished with native failures correctly propagated to overall `FAILED` status. All 12 CPU children / 48 numerical checks passed on the same assigned worker. All 12 GPU children failed at `cuda_initialization` under the unchanged 3 GiB AS bound, before any simulator operation. The initialization sweep itself returned failure because its 3 GiB case failed; its three larger initialization-only cases passed. No solver used a larger limit and no speedup is claimed.

SkyPilot teardown completed immediately after recording the complete run. A subsequent SkyPilot status readback confirms no owned discovery cluster remains. A separate fixed 8 GiB numerical discovery profile is proposed in [decisions.md](../decisions.md); it requires owner approval before use. CPU and production policies remain unchanged. TASK-005 and G1 remain open until actual numerical GPU coverage and isolation decisions are reviewed.

| CPU cell on assigned worker | Median first operation s | Median warm operation s | Maximum numerical error | Peak RSS KiB |
| --- | ---: | ---: | ---: | ---: |
| transmon61 | 0.000679 | 0.000455 | 9.5e-14 | 253680 |
| transmon511 | 0.023590 | 0.018329 | 4.4e-12 | 278772 |
| rabi2 | 0.007546 | 0.006747 | 7.49e-09 | 254332 |
| spectrum256 | 0.009142 | 0.008429 | 2.84e-14 | 259516 |
