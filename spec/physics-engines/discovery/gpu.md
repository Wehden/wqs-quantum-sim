# GPU discovery: allocation blocked

Status: TASK-005 is **incomplete**, checked on 2026-10-25 after the Lindblad and scqubits CPU references passed. GPU suitability remains unknown. No GPU capability, worker image or resource-policy change is enabled by this report.

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

The locally installed SkyPilot implementation was examined at its reported source revision `b1431e52d97c22e9bb8fa8b67f162543754ddaf5`. In `sky/provision/kubernetes/utils.py`, `_gpu_resource_key_helper` scans supported resource keys in AMD-then-NVIDIA order and returns the first present key; `CUSTOM_GPU_RESOURCE_KEY` can override the choice in the executing process. The observed mixed-vendor cluster and inventory output are consistent with that mechanism. The remote server's environment was not inspected, so this is a source-supported explanation to verify, not a confirmed diagnosis of its effective configuration. Several NVIDIA nodes also lack the SkyPilot-recognized accelerator label. Neither fact is resolved by changing a simulation CPU or memory request.

The Laminar cluster chat was asked to inspect the supported NVIDIA placement path read-only under the owner's standing placement-coordination request. No node labels, server overrides, running consumers, production configuration or existing credentials were changed by WQS discovery. Private logs remain outside this repository; they must be sanitized before sharing.

## Benchmark protocol once placement works

Use the actual CPU cases in [lindblad.md](lindblad.md) and [scqubits.md](scqubits.md), preserving physical models, precision, output semantics and numerical tolerances. Predeclare small and larger cases before measuring: Transmon raw dimensions 61 and 511 for scqubits, and the fixed two-level Rabi plus 256-level dense spectrum for QuTiP. Additional operation cells need separate evidence. Do not expand accepted dimensions because a GPU is present.

For each candidate cell, run at least three fresh-process cold trials and three warm repeats on the same allocated device. Measure scheduling/provisioning separately from imports, context creation, host construction, transfer, native solution, synchronization and CPU result copy. Compare float64/complex128 results against the CPU references; compare degenerate subspaces through projectors/residuals, not arbitrary eigenvector phases. Track host RSS, device/driver/runtime identity, VRAM peak and complete output bytes. Record unsupported specialization or CPU conversion explicitly. A faster matrix multiply is not a successful GPU ODE benchmark.

Test initialization and execution under the current sanitized-child environment, 3 GiB address-space and 12 MiB file limits first. Device visibility must follow the scheduler's allocation. A CUDA virtual-address failure needs a measured, bounded replacement policy and the required design review; do not remove the limit to make a benchmark pass. GPU equivalence, seed behavior, useful speedup, native operation coverage and limit compatibility are all **unmeasured** here.

No benchmark Dockerfile or GPU probe is presented as tested while this allocation blocker remains. TASK-005 must resume with an actual allocation and reproducible pinned fixture. TASK-006 cannot mark the GPU discovery gate complete from this report, nor conclude that GPUs are unhelpful.
