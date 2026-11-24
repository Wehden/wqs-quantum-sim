# Costed SkyPilot run sheet

**Status:** Owner approved the discovery run sheet on 2026-11-22. Prices below are planning rates checked on 2026-11-22. Rates and reservations will be refreshed before requesting placement.

## Internal placement

At 08:17 PST on 2026-11-22, the live Laminar Grafana catalog (dashboard v18, panel 12) returned the SkyPilot placement policy. Its linked policy page returned HTTP 404. The installed policy and dashboard instructions govern placement. SkyPilot 0.13.0 against the existing Laminar authority returned Kubernetes inventory. It reported **four V100 slots free**, with 13 vCPU and about 23 GB RAM free on their host, and **two RTX 3060 slots free**, with about 20 GB RAM free. RTX 5060 Ti slots were booked and the dedicated RTX 5080 was unavailable to this workload. The inventory establishes scheduler-visible capacity at query time. Check for other reservations or consumers before launch. The allocated worker must confirm V100 VRAM and driver version before starting a benchmark.

| Profile | Resource request | Work to run |
| --- | --- | --- |
| Internal first | Kubernetes via Laminar SkyPilot, one V100, 8 vCPU, 16 GiB RAM, at least 16 GiB verified VRAM, 30 GiB bounded ephemeral scratch, one worker and one child at a time | scqubits controls and 1K/2K candidates, with 4K subject to accounting. qsim native CUDA controls and 24/26/28-qubit cells as memory permits. Pick a CUDA 12.x build with verified Volta support. |
| Large or newer device, internal preferred | 8 vCPU, 64 GiB RAM, one compatible NVIDIA GPU with at least 24 GiB verified VRAM, 50 GB ephemeral disk, one worker and one child at a time | 30-qubit qsim, cuStateVec and deferred scqubits size tiers. Use a compatible internal offering first. Use a supported compute-capability and driver combination for the pinned libraries. |
| Runpod fallback through SkyPilot | `A100-80GB:1` for FP64-sensitive scqubits or the combined coverage, or `L40S:1` for qsim alone if offered and verified. Same large-tier minimums. Use one non-spot allocation at a time. | Only the cells that could not be covered internally or need a qualified follow-up. Prefer one shared A100 session if both lanes need fallback and the artifact is ready. |

Both profiles use Linux x86_64, pinned images and synthetic simulation fixtures. There is no inference model or context. Internal storage means a verified bounded scratch mount or ephemeral budget available through SkyPilot. No persistent volume or preemption is requested. Ask for resources and let SkyPilot place the workload. The V100 path does not require the current cuStateVec releases. [NVIDIA cuStateVec support](https://docs.nvidia.com/cuda/cuquantum/latest/custatevec/index.html)

## Run order and time budget

| Window | Limit | Actions and output |
| --- | ---: | --- |
| Preparation | 0 billed GPU hours | Build and pin the images, run CPU checks, freeze fixture digests, and validate limits and the early-stop path. Refresh dashboard, scheduler reservations, device compatibility and Runpod costs. Record exact specifications and an owner-reviewed task before submission. |
| Internal screen | Up to 2 hours per lane, sequential | Reserve up to 20 minutes for setup, 70 for screening, 15 for confirmation, and 15 for collection and shutdown. Run CPU/GPU probes at the initial tier and report any coverage gaps. |
| Runpod initial windows | Up to 2 billed hours per lane, at most 4 combined | Use the same time split. Screen missing cells in ascending memory order. Prefer 28-qubit cuStateVec and the next larger scqubits tier before the 30-qubit and largest-basis cases. Stop admitting trials at the collection boundary. |
| Runpod contingency | Up to 2 additional billed hours total within the cap | Use only for a failed setup reconciled to a specific cause, or to confirm a potential pass. Confirm no prior owned allocation remains before starting it. |

The time windows are upper bounds, not completion guarantees. Keep unfinished cells explicit and prioritize controls, coverage of both engines, then confirmation of the best candidate per operation. Stop a slow screen case at the trial bound. If its paired reference cannot be measured, record the limit and leave the crossover unqualified. At the end of any window, copy receipts before teardown and verify the allocation is gone. No pool stays warm between sessions.

## Runpod budget

The shared SkyPilot catalog returned a **$1.79/hr** one-GPU Secure A100 offer (8 vCPUs, 117 GB advertised host RAM). Its device-memory column was zero, so verify the actual VRAM instead of using that field as evidence. The conservative public planning rate is **$2.09/hr** for the 80 GB PCIe A100 or **$2.19/hr** for SXM. The public L40S reference is **$1.09/hr** (48 GB VRAM, 94 GB host RAM, 16 vCPUs). These are catalog prices, with availability and final rates to be verified through SkyPilot. [Runpod pricing](https://www.runpod.io/pricing)

| Cost item | Conservative estimate |
| --- | ---: |
| Internal-only execution | $0 incremental Runpod charges |
| Two 2-hour A100 PCIe windows | $8.36 compute |
| Optional 2-hour A100 PCIe contingency | $4.18 compute |
| Two 2-hour windows, A100 PCIe for scqubits and L40S for qsim | $6.36 compute |
| 50 GB container disk for six hours at $0.10/GB-month (730 hours) | About $0.05 before rounding or provider-specific fees |
| Maximum plan at $2.50/hr × 6 billed hours, plus $3 allowance for disk, transfer and other charges | **$18.00** |
| Headroom under the owner's hard cap | **$7.00** |

The $25 is total Runpod spend for this round, including failed attempts, provisioning, image pulls, disk/transfer, idle time and teardown. The proposed retry envelope uses a $2.50/hr rate ceiling, a 6-hour cumulative billed-time ceiling and an $18 operating budget. Before every attempt, reserve its full cost including teardown, reconcile actual and pending charges, and decline any offering or attempt that would exceed these bounds. Validate the maximum selectable rate for the eligible SkyPilot offers, including any automatic region fallback. The lowest catalog quote alone is insufficient. If the rate or bounded cleanup cannot be established, record the cell as waiting for internal capacity. This budget relies on the verified deadline and teardown path. Provider-side enforcement has not been established.

## Operator checkpoints

1. Pin the existing SkyPilot endpoint in private operator configuration. Refresh the live catalog and `sky gpus list --infra k8s --all`. Cross-check current reservations, consumers, resource and device fit. Prefer the approved internal tier. Validate the digest-pinned task using the installed client's dry-run option before submitting any allocation request. Bring material deviations from the reviewed scope back to the owner.
2. Allocate an owned cluster name and record quote, start/stop budget, image digest, task inputs and request identity. After approval, use SkyPilot to provision, submit and observe. Use a short idle autodown where supported and a separate deadline watcher outside the allocation. The watcher stops submission and initiates owned SkyPilot cancellation and teardown before the billable deadline. Prove its failure-path behavior before a paid launch. Idle autodown does not cover a stuck setup or running job. [SkyPilot CLI reference](https://docs.skypilot.co/en/latest/reference/cli.html)
3. Verify GPU model, VRAM, driver and native libraries inside the scheduler-assigned worker. Record the real start time and initial RSS, VRAM and virtual address usage. Stop or reduce the scope if the profile does not fit. Never redirect an allocated task to another GPU by hostname, pod or UUID.
4. Export job and timing receipts through SkyPilot and request teardown of the exact owned cluster. Refresh its SkyPilot status and reconcile observed spend. For an uncertain or stalled launch, stop retries, reconcile the request ID, cancel it if active, and tear down only its allocation. A failed teardown freezes new paid spend and is escalated with the outstanding IDs.
