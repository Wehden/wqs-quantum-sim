# First-release device matrix

The owner-approved first production physics matrix is **CPU-only**. The enabled
GPU matrix is empty. TASK-027/028 have no artifact or runtime branch to implement
for this matrix; TASK-029 records this disposition, not a passing hardware test.

| Engine | Enabled device | Discovery basis |
| --- | --- | --- |
| QuTiP/Lindblad | CPU | Tested CuPy-backed Rabi and spectrum paths converted to the host; storage on a GPU did not prove native solver execution. |
| scqubits | CPU | Native GPU eigensolver execution was demonstrated through SkyPilot, but the corrected D61/D511 trials showed no useful benefit at those measured sizes. |
| JoSIM | CPU | No established native GPU implementation was found in the pinned source. |

The [original discovery](gpu.md#candidate-paths) retains source, driver,
precision, allocation and correctness details. Its corrected operation-only
measurements, rather than the withdrawn timing ratios, govern this decision.
The SkyPilot RTX4090 run passed 24 fresh children and 96 comparisons. The GPU
8-GiB address-space exception was discovery-only. It does not change the
production 2-CPU/4-GiB, 3-GiB child-address-space profile.

This is not a conclusion that larger problems or other maintained native
backends cannot benefit from GPUs. A later enabled GPU cell requires measured
correctness/benefit, bounded host/device memory, a pinned deployable artifact,
and actual SkyPilot/device evidence. Missing or skipped GPU execution is not a
successful GPU test. There is no silent CPU/GPU or precision substitution.
No GPU allocation, spending, provider call or serving-workload change is needed
for this release disposition.
