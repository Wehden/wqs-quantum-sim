# GPU discovery: candidate fixture and pending allocation

Status: TASK-005 is **incomplete**, checked on 2026-10-26 after the Lindblad and scqubits CPU references passed. GPU suitability remains unknown. No GPU capability, worker image or resource-policy change is enabled by this report.

## Candidate paths

- scqubits 4.3.1 has native `esys_cupy_dense` and `esys_cupy_sparse` methods. Dense diagonalization constructs the Hamiltonian on the host, transfers it to CuPy, computes the full eigensystem and copies selected results back. The [upstream diagonalization guide](https://scqubits.readthedocs.io/en/latest/guide/settings/ipynb/custom_diagonalization.html) describes these methods. The CPU fixtures and native-source hash are in [scqubits.md](scqubits.md). A device-free import is insufficient to establish benefit or correctness.
- QuTiP's CuPy plugin was inspected at upstream commit `bd471ca0581fcd9a84e613775092bca418d0b66a`, version `0.1.0.dev0`, BSD-3-Clause. Its [installation instructions and CPU-conversion warning](https://github.com/qutip/qutip-cupy/blob/bd471ca0581fcd9a84e613775092bca418d0b66a/README.md) state that it is unreleased and that missing specializations can fall back to CPU. No eigensolver or matrix-exponential specialization is registered in the inspected `__init__.py`. GPU matrix storage therefore does not establish native GPU spectrum, propagator or ODE execution. Solver coverage needs operation-specific execution and transfer/fallback evidence.
- CuPy CUDA 12.x package candidate metadata reports `cupy-cuda12x==14.2.0`; it is an uninstalled discovery candidate, not a combined-worker pin. Compatibility with NumPy 2.5.4, QuTiP 5.3.1, the plugin revision and the allocated device's driver must be verified together. No CUDA runtime, plugin or driver execution version is claimed here.
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

The locally installed SkyPilot implementation was examined at its reported source revision `b1431e52d97c22e9bb8fa8b67f162543754ddaf5`. In `sky/provision/kubernetes/utils.py`, `_gpu_resource_key_helper` scans supported resource keys in AMD-then-NVIDIA order and returns the first present key; `CUSTOM_GPU_RESOURCE_KEY` can override the choice in the executing process. The observed mixed-vendor cluster and inventory output are consistent with that mechanism. The initial WQS check did not inspect the remote environment. Laminar subsequently confirmed that the existing API server had no NVIDIA override; its managed correction is [Laminar PR #170](https://github.com/Wehden/laminar/pull/170), commit `720fb5f0122649964627ac6d7cda6998dce61030`. It awaits the owner's manual merge before installation and controlled restart. Several NVIDIA nodes also lack the SkyPilot-recognized accelerator label. Neither fact is resolved by changing a simulation CPU or memory request.

On 2026-10-26 the owner authorized Laminar to fix placement while preserving existing GPU workloads and credentials. Laminar prepared the managed fix and a non-preempting client profile. Physical occupancy checks found existing consumers on every inspected NVIDIA card; a free logical reservation or free VRAM is not an exclusive spare allocation. The labeled RTX5080 is occupied, and WQS must not preempt its consumer. Placement configuration and physical capacity are therefore separate gates. No node labels, server overrides, running consumers, production configuration or existing credentials were changed by WQS discovery. Private logs remain outside this repository; they must be sanitized before sharing.

## Benchmark protocol once placement works

Use the actual CPU cases in [lindblad.md](lindblad.md) and [scqubits.md](scqubits.md), preserving physical models, precision, output semantics and numerical tolerances. Predeclare small and larger cases before measuring: Transmon raw dimensions 61 and 511 for scqubits, and the fixed two-level Rabi plus 256-level dense spectrum for QuTiP. Additional operation cells need separate evidence. Do not expand accepted dimensions because a GPU is present.

For each candidate cell, run at least three fresh-process cold trials and three warm repeats on the same allocated device. Measure scheduling/provisioning separately from imports, context creation, host construction, transfer, native solution, synchronization and CPU result copy. Compare float64/complex128 results against the CPU references; compare degenerate subspaces through projectors/residuals, not arbitrary eigenvector phases. Track host RSS, device/driver/runtime identity, VRAM peak and complete output bytes. Record unsupported specialization or CPU conversion explicitly. A faster matrix multiply is not a successful GPU ODE benchmark.

Test initialization and execution under the current sanitized-child environment, 3 GiB address-space and 12 MiB file limits first. Device visibility must follow the scheduler's allocation. A CUDA virtual-address failure needs a measured, bounded replacement policy and the required design review; do not remove the limit to make a benchmark pass. GPU equivalence, seed behavior, useful speedup, native operation coverage and limit compatibility are all **unmeasured** here.

The disposable fixture and CPU references below are now tested. Actual GPU execution still needs an allocation; device-free checks are not GPU acceptance. TASK-005 must resume on the corrected SkyPilot placement path. TASK-006 cannot mark the GPU discovery gate complete from this report, nor conclude that GPUs are unhelpful.


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

The suite starts three fresh sanitized children per predeclared cell. Each performs its first operation plus three warm repeats, giving 12 fresh children and 48 numerical checks. Every child sets the existing 3 GiB address-space and 12 MiB file limits before native imports. CPU references use the same physical inputs, float64/complex128 and output representation as the candidate GPU cells. CPU spectrum references are independent SciPy eigensystems. Transmon comparisons check energies, residuals and eigenspace projectors; Rabi uses the analytic population. The benchmark source has no option to disable or raise these limits.

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

## Owner-authorized serverless test transport

On 2026-10-26 the owner selected a temporary Runpod serverless GPU for discovery while the local NVIDIA placement correction and device availability remain separate infrastructure work. This is a test-only execution transport. Production WQS placement remains the approved SkyPilot architecture. No existing GPU consumers, deployment credentials or recovery keys are changed.

The Dockerfile's `serverless` target derives from public `python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2`, without the private CI runner base. It pins uv 0.11.6 and Runpod SDK 1.12.0, whose current release metadata was checked against PyPI. Numerical/CUDA/plugin pins are shared with the ordinary `probe` target. The SDK imports only in the transport process, outside the numerical child's unchanged limits.

`serverless_handler.py` accepts only `backend: cpu|gpu` plus one of the four fixed cases, or `suite: true`. It rejects unknown fields and combinations before starting a child. The executable and script paths are fixed. Its child environment contains only the runtime path, scheduler-provided CUDA/NVIDIA visibility and fixed thread/cache settings; Runpod tokens and other credential environment variables are excluded. The handler returns child status and bounded diagnostic receipts, so operators must inspect the numerical return code rather than treating a completed transport request as successful physics.

```bash
docker build --platform linux/amd64 --target serverless \
  -f tests/fixtures/physics/gpu/Dockerfile \
  -t wqs-physics-gpu-serverless:local .
docker run --rm --cpus 2 --memory 4g --memory-swap 4g \
  wqs-physics-gpu-serverless:local \
  --test_input '{"input":{"backend":"cpu","case":"transmon61"}}'
```

Transport harness checks rejected nine invalid requests before subprocess execution, verified the fixed executable/argument vector, and confirmed that synthetic Runpod/AWS credentials were absent from the child's environment. Registry publication, endpoint creation, GPU allocation and cleanup belong to the authorized coordinator; source preparation does not establish that any such action or hardware test has succeeded.


The public-base serverless image built and passed dependency checks as local image ID `sha256:e5c6a501b954288c888ac5dcf4806fd0dbaeb42ddf492e620a567b8619173966` (6,030,087,030 bytes). An actual SDK 1.12.0 `--test_input` CPU request returned numerical child code 0 and four passing Transmon D61 checks under the unchanged child limits, in a two-CPU/4 GiB container. SDK tests for an arbitrary case/path and an extra `code` field were rejected with exit 1. The public image creates a dedicated nonroot UID 1001 with its own home when the base does not already supply that UID; this fixes QuTiP's home-directory lookup without forwarding the operator's home or credentials.

Probe source SHA256 is `32a4e27768b014b478e14f3d60e927cda4ce1ca5e5735fdf780ea475d19ab90e`; handler SHA256 is `0dfddacbcc1911ddd1c605a38a1f10bc1c77f14952fbe1f791cc5c1be9a9f149`. Algorithm timing now explicitly excludes independently computed CPU-reference time; the receipt retains reference-inclusive trial time under a separate name. The earlier CI-base image and timing table document that earlier measured artifact, while this public image is the transport-ready candidate. Neither image has established GPU initialization or performance.

The public-image command-line suite additionally passed all 12 fresh children / 48 numerical checks with the unchanged child limits. That packaging regression run did not impose the outer two-CPU/4 GiB container quota and is not used for profile performance claims; the SDK single-case check above and earlier constrained CPU measurements retain their stated outer limits.
