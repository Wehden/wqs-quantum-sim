# GPU discovery results

**Status:** A100 screening and targeted confirmation recorded on 2026-11-22, with internal RTX 3060 confirmation on 2026-11-25 and a four-cell follow-up on 2026-11-26. The A100 comparison used source baseline `cb3e557459421e6b4f33e5b412b50719609ac55b`.

## Placement and provenance

SkyPilot reported four allocatable V100 slots. Host telemetry showed one actual 16 GiB V100 with an existing process using about 8.1 GiB, and the idle RTX 3060 had 12 GiB. Neither met the reviewed internal allocation requirements. All GPU work used the existing SkyPilot authority. The owned Runpod attempts used one quoted Secure A100 at $1.79/hour. A custom-image attempt stalled at provisioning and its registry returned HTTP 401 to an anonymous pull. That attempt was canceled and removed. A public standard-image attempt found no US capacity. A subsequent SkyPilot request acquired one Secure A100 in Canada.

The successful allocation reported an NVIDIA A100 80 GB PCIe with 81,920 MiB VRAM and NVIDIA driver 595.112.07. The public Runpod base was pinned to `sha256:ffe1c3b1ec997f7eaaef8561c2a701792c79ece19754d528222a14ee25d24cb0`. SkyPilot transferred a hashed bundle containing synthetic fixtures, a discovery copy of the QASM adapter with only the width guard lifted to 30, and a wheel built from qsim `v0.22.1` (`8d7150a7f796e8f7ebbf7587e67e80ed2674a5d0`). The qsim wheel hash was `66407dddcd86812e5c1c03ee2825db773b8d3216bb6133e34794fb79b5ecd822`. Exact artifact, allocation and request details are retained privately. The run evaluated scqubits 4.3.1, CuPy 14.2.0 and qsimcirq 0.22.1 at the approved large discovery tier.

The discovery task used one unprivileged child at a time and capped its address space at 48 GiB, output at 12 MiB and each trial at 120 seconds. It checked host and device resource bounds before running a cell. The trial RSS and sampled device-memory peaks below describe this A100 worker. The internal V100 remains untested.

## CPU preparation

The immutable dependency base from [TASK-005](../discovery/gpu.md) supplies scqubits and the Python runtime. The qsim build recipe pins CUDA 12.8.1 and compiles the CPU, native CUDA and cuStateVec modules from the same source. The worker used the resulting wheel and the frozen fixture bundle. The local CPU preparation passed 13 scqubits arms and 10 qsim arms. qsim CPU preparation included both OpenQASM dialects, asymmetric bit order, exact deterministic samples and a 24-qubit random circuit. Direct CUDA and cuStateVec mode requests on a CPU-only system raised a CUDA initialization error. The repository CI script also passed locally in a Bookworm container with 2,198 Python tests and three console tests. The workstation run hit a glibc static thread-local storage error loading the packaged OpenMP library.

| Fixture | Driver SHA-256 | Manifest SHA-256 |
| --- | --- | --- |
| scqubits | `13f22a4383a33bb77409d6a3f76ed0cf4a60d780be70e63deecae12a5d228268` | `4e8374390da87d76811ed4dc8a06304c03cc668d19e505f519e88e7dc1e011d8` |
| qsim | `fc629d404757729a6d03bad0c9543e497dccac698283b46f251395547307864e` | `f92ff96b8670b2cd69c3d9c1bde72e2d262200861974caa53771a0095e4dfcc2` |

## scqubits outcome

Dense CuPy executed native device operations and met the selected numerical checks on the completed cells. The GPU arm used one CPU thread. The comparison selects the fastest correct CPU arm from one-thread and eight-thread SciPy methods, including structure-aware methods where supported. Fresh-child time includes imports, model construction, solve and encoding. Provisioning and API latency are separate.

| Fixture | Completed pairs | Fastest CPU median | Dense GPU median | Result |
| --- | ---: | ---: | ---: | --- |
| Transmon D61, 8 eigenpairs | 5 | 4.90 s | 5.74 s | GPU slower |
| Transmon D511, 8 eigenpairs | 5 | 4.92 s | 5.76 s | GPU slower |
| Fluxonium D512, 32 levels and operator | 5 | 5.01 s | 6.17 s | GPU slower |
| Fluxonium D1024, 8 eigenvalues | 5 | 5.51 s | 8.31 s | GPU slower |
| Fluxonium D2048, 32 levels and operator | 5 | 8.91 s | 27.93 s | GPU slower |
| FluxQubit D441, 32 levels and operator | 5 | 4.93 s | 5.76 s | GPU slower |
| FluxQubit D1089, 8 eigenvalues | 5 | 5.04 s | 5.87 s | GPU slower |
| FluxQubit D2025, 32 levels and operator | 5 | 5.51 s | 6.23 s | GPU slower |
| High-ratio Fluxonium D1024, 32 levels and operator | 5 | 5.57 s | 8.65 s | GPU slower |

Peak sampled device memory across the scqubits lane was 849 MiB. The largest observed host RSS among the completed GPU cells was 1,186 MiB. Fluxonium D2048 showed a GPU solve median of 0.258 s versus 0.293 s in the fastest CPU arm. Construction and fresh process startup account for most of the latency. The sparse GPU solver failed the D511 Transmon energy tolerance on all five trials, missing a selected eigenvalue by about 0.00028 GHz. FluxQubit sparse trials failed with `CUSPARSE_STATUS_NOT_SUPPORTED`. These cells do not qualify.

The independent CPU cutoff check found a 649 Hz maximum shift among the first 32 FluxQubit transition energies from D441 to D1089. The shift from D1089 to D2025 was below a millihertz for that fixture. For the pinned high-ratio Fluxonium fixture, the CPU energies at D1024 and D2048 agreed within a millihertz. The separate cutoff check below adds operator convergence for these fixtures.

The run reached the 26-minute scqubits phase deadline. The high-ratio D2048 and 4K cells did not complete five pairs. The conditional 8K tier was not started. Keep CPU for the tested cells. Any further Fluxonium trial should include a GPU arm with threaded host Hamiltonian construction. The recorded GPU arm used one host thread and does not establish the speed of that alternate profile.

## qsim outcome

The timings compare five fresh children per arm after worker provisioning. The GPU arm used one host thread and the fastest passing CPU arm used two or eight threads. Both arms include imports, QASM preparation, native execution and output encoding. They use identical fusion and inputs. API latency and SkyPilot dispatch are excluded.

The eight-qubit controls verified output ordering and deterministic terminal measurement. Both GPU backends met the simultaneous 99% confidence bound on the 4,096-shot GHZ control. The 256-shot deterministic control matched its analytic output exactly. Its generic pooled confidence interval was too wide to establish the stochastic equivalence bound.

The 24-qubit probability case took about 6.9 s across all three backends. Native CUDA lowered the solve median from 0.621 s to 0.040 s, but process startup dominated. The 26-qubit, depth-60 probability case cleared the five-pair latency threshold in both GPU modes. Both matched the CPU amplitudes and probabilities. In CUDA, the native stage took 0.340 s versus 7.424 s on the eight-thread CPU arm. CUDA's full median was 7.401 s versus 13.930 s and the sample p90 was 7.571 s versus 14.229 s. The CUDA arm reached 939 MiB sampled VRAM and 578 MiB host RSS.

The qsim probes recorded the selected native module and its SHA-256, synchronized the device after native execution and sampled device activity in the single assigned-GPU child interval. This established backend attribution for the screen. Separate kernel traces are required for full qualification.

At 28 qubits and depth 60, the two-thread CPU reference timed out in all five trials. The eight-thread CPU and GPU children returned outputs, but the benchmark could not verify them against the frozen reference. This leaves the case unqualified and distinguishes a missing comparator from a numerical discrepancy.

| Case and operation | CPU median | CUDA median | cuStateVec median | Verification |
| --- | ---: | ---: | ---: | --- |
| QASM2 8q control, probabilities | 6.33 s | 6.82 s | 6.88 s | Selected amplitudes and probabilities agreed |
| QASM3 8q control, probabilities | 6.18 s | 6.76 s | 6.75 s | Selected amplitudes and probabilities agreed |
| QASM2 8q control, 256 samples | 6.27 s | 6.84 s | 6.82 s | Exact deterministic support passed |
| QASM3 8q GHZ, 4,096 samples | 6.28 s | 6.83 s | 6.86 s | Distribution bound passed |
| QASM2 24q random, probabilities, depth 20 | 6.93 s | 6.97 s | 6.93 s | Selected amplitudes and probabilities agreed |
| QASM3 26q random, probabilities, depth 60 | 13.93 s | 7.40 s | 7.44 s | Selected amplitudes and probabilities agreed |
| QASM2 28q random, probabilities, depth 60 | n/a | n/a | n/a | Incomplete paired verification |
| QASM2 24q random, 256 samples, depth 20 | 7.14 s | 9.22 s | 7.06 s | Distribution bound remains open |
| QASM3 26q random, 4,096 samples, depth 20 | n/a | n/a | n/a | Incomplete paired verification |
| QASM3 28q GHZ, 4,096 samples | 9.07 s | 43.57 s | 6.89 s | Distribution bound passed |

The qsim phase reached its 43-minute deadline after one verified 26-qubit random sampling trial per arm. It did not reach the 30-qubit selected probabilities or the 28-qubit random 4,096-shot samples. The 28-qubit GHZ check verified terminal sampling on both GPU backends. CUDA was slower than CPU and cuStateVec's 1.32× improvement did not reach the latency threshold. The 24-qubit random 256-shot case passed output-shape checks but had insufficient sample count for the distribution bound. Neither sampling backend qualified for promotion.

The combined job ended with both phase deadlines reached and SkyPilot status `FAILED`. Completed-cell evidence was collected before teardown. Neither phase achieved full matrix coverage.

## qsim confirmation

The best passing screening candidate is native CUDA on the frozen QASM3 26-qubit, depth-60 probability case. Confirmation repeats that cell and the verified QASM2 24-qubit, depth-20 neighbor on the same A100 profile. The frozen comparator is the eight-thread CPU arm for both cases, selected from the screening runs. The two cases change depth as well as width.

The 26-qubit case passed 20 of 20 paired comparisons. The median fresh-child time was 13.996 s on CPU and 7.389 s on CUDA. The corresponding p90s were 14.205 s and 7.550 s. The 10,000-resample paired bootstrap gave a 95% interval of 1.87× to 1.92× for the median ratio and 6.51 s to 6.77 s for the time saved. The CUDA arm peaked at 939 MiB sampled VRAM and 579 MiB host RSS.

The 24-qubit neighbor also passed 20 of 20 comparisons. CPU took 7.044 s and CUDA took 7.067 s. Their p90s were 7.224 s and 7.153 s. The median speed ratio was 0.997× with a 95% paired interval of 0.982× to 1.015×. The CUDA arm peaked at 555 MiB sampled VRAM and 578 MiB host RSS. The confirmation job succeeded and its allocated device and library identities matched the screening profile.

## Allocation and cost

SkyPilot reported all four trial allocations terminated, and refreshed status returned an empty result for those names. The elapsed durations include setup and teardown where represented by SkyPilot. Compute estimates use the highest compatible quote, $1.79/hr, across the allowed Runpod regions.

| Attempt | SkyPilot elapsed | Quote-based compute |
| --- | ---: | ---: |
| Private-image provisioning stall | 323 s | $0.16 |
| Standard-image US capacity miss | 2 s | Less than $0.01 |
| Combined screen | 4,286 s | $2.13 |
| CUDA confirmation | 1,790 s | $0.89 |
| **Total** | **6,401 s** | **$3.18** |

SkyPilot returned zero computed cost for both regional A100 sessions. The quote-based total accounts for their full reported durations. Adding the run sheet's $3 ancillary allowance gives a $6.18 planning estimate against the $25 cap. Provider invoice charges were not available through SkyPilot. Request IDs, actual assignments, setup logs, termination receipts and the execution bundle are retained in private operator storage.

## Disposition

| Engine and operation | Decision |
| --- | --- |
| scqubits dense or sparse eigensystems | Keep CPU at tested sizes. Dense CuPy passed the completed numerical cases without a fresh-child latency win. Sparse failures and the larger basis tiers remain open. |
| qsim, selected probabilities | Retain the 26-qubit, depth-60 CUDA result as a candidate for a separate production design. The 24-qubit neighbor showed parity, and the 28-qubit case lacks a verified reference. The two fixtures cannot establish a width cutoff because depth also changed. |
| qsim, terminal measurement sampling | Keep CPU. The completed cases show no qualifying win, and the wider random circuits lack complete evidence. |

The time results apply to fresh local children on the benchmark worker. They exclude API queueing and provisioning. V100, larger basis tiers and qsim beyond the production width limit lack qualification. A production GPU profile still needs matched-depth characterization, separate kernel traces and exact-image acceptance through SkyPilot.

## Internal RTX 3060 confirmation

The owner approved this 12 GiB discovery profile on 2026-11-23 for the same 26-qubit, depth-60 probability case and its 24-qubit, depth-20 control. The physical RTX 3060 reported 12,288 MiB VRAM, zero use and no compute consumers before placement. SkyPilot placed one job on Kubernetes with one RTX 3060 and eight vCPUs. The pod rendered the requested host memory and ephemeral storage as 16 GB and 30 GB. The assigned worker used an AMD Ryzen 5 5500 and NVIDIA driver 580.178.04.

The disposable worker reused the pinned qsim wheel, inputs, seed, fusion setting and numerical checks from the A100 run. CUDA reported 11,910 MiB usable, about 97% of the physical VRAM. The 12 GiB probe now checks the physically verified model separately from the CUDA capacity and budgets from usable memory. The two- and eight-thread CPU arms and one-thread native CUDA arm ran in fresh children on the same worker. The initial 24-qubit control passed across all three arms and is excluded from timed trials.

Two base-image attempts stopped during SkyPilot bootstrap. The remote runtime selected SkyPilot 0.14.3 while the server provisioner used Ray 2.9.3. Restricting package resolution in the disposable worker before bootstrap selected the server’s SkyPilot 0.13.0 and resolved the mismatch. A subsequent attempt stopped on an OpenMP-sensitive CPU availability check. The run now checks the effective cgroup limit and the process affinity independently. The next attempt stopped at the CUDA usable-memory guard and the smoke validator’s interpreter lookup. The worker now uses the pinned interpreter for the smoke validator. All attempts used SkyPilot, and each stopped allocation was reconciled before the retry.

### First attempt and retained results

| Frozen fixture | Captured CPU/CUDA pairs | Fastest CPU median | RTX 3060 median | CPU / CUDA |
| --- | ---: | ---: | ---: | ---: |
| 26-qubit, depth-60 selected probabilities | 4 | 34.66 s | 6.35 s | 5.46× |
| 24-qubit, depth-20 startup control | 1 | 6.97 s | 5.42 s | 1.29× |

Every captured trial passed the selected-amplitude and probability checks. The 26-qubit CUDA arm used the native qsim module and peaked at 627 MiB sampled VRAM. Its native solve median was 0.91 s, compared with 29.65 s on the fastest CPU arm. The table reports fresh-child time and excludes placement, queueing and API overhead. CPU and CUDA ran on the same worker. The A100 run used a different host, so the ratios across the two runs do not rank the devices.

The local observer stopped during the interrupted turn. SkyPilot subsequently removed the worker. A refreshed lookup confirmed all five owned allocations were absent, and SkyPilot recorded zero cloud compute charges. The API retained the launch result and the last streamed partial benchmark snapshot. The final native job status and the remaining trial receipts were unavailable after teardown. The result remains partial and the 20-pair qualification gate is open. The repeat uses a service that streams receipts to private operator storage and attempts a final export before retiring the worker.

### Repeat with independent receipt capture

The 2026-11-25 repeat used the same bundle and exact local profile. A service
on the operator machine retained the live native job stream and collected the
final receipt. It ran independently of the interactive turn. SkyPilot used
the same Kubernetes scheduler and pinned runtime compatibility settings.

The 26-qubit case passed all 20 CPU/CUDA pairs. Fresh-child CPU and CUDA
medians were 34.60 s and 6.27 s. The two-thread CPU arm was the faster matched
comparator. The eight-thread median was 35.09 s. CPU and CUDA p90s were
35.13 s and 6.45 s. The paired 95% interval for speedup was 5.47× to 5.55×,
with 28.17 s to 28.46 s saved. CUDA solver time was 0.91 s. The worker used
the same native CUDA module as the A100 run and peaked at 627 MiB sampled VRAM
and 494 MiB host RSS.

The 24-qubit control also passed all 20 pairs. Fresh-child CPU and CUDA
medians were 7.09 s and 5.25 s, with p90s of 7.50 s and 5.37 s. The eight-thread
CPU arm was the faster comparator. The paired 95% speedup interval was
1.33× to 1.38×, below the 1.5× criterion. CUDA peaked at 243 MiB sampled VRAM.

Both fixtures passed all selected amplitude and probability comparisons. The
same-worker result confirms a speed benefit for the 26-qubit case. The A100
completed its native solve faster on a different host, with a 0.26 s median.
The different host CPU and import times matter for end-to-end latency.

The native job succeeded. Receipt capture completed before SkyPilot teardown,
and a refreshed lookup verified that the owned worker was gone. The
[deployment plan](deployment-plan.md) defines packaging and exact-image
acceptance for the bounded GPU canary.

### Native kernel evidence

A separate SkyPilot worker with the same RTX 3060 profile traced the fixed
trials without contributing any timing samples to the paired comparison.
CUPTI Python 12.8.0, CUDA bindings 12.8.0 and CUPTI 12.9.79 recorded qsim
gate kernels for both cases. The 26-qubit fixture produced 271 CUDA kernel
records with 0.874 s aggregate kernel duration, and the 24-qubit control
produced 85 with 0.069 s. The worker used the same native CUDA module and
exited successfully. The [CUPTI Activity API](https://docs.nvidia.com/cupti-python/tutorials/tutorials.html)
documents the trace method.

The confirmation and trace allocations ran for 2,124 s and 104 s
respectively in SkyPilot’s accounting. Both were terminated and returned no
cloud compute charges. Native traces and full raw result receipts remain in
private operator storage.

### Matched-depth screen

On 2026-11-26, SkyPilot placed one internal 12 GiB RTX 3060 with eight vCPUs
and 16 GB of host RAM. The device reported 12,288 MiB of physical memory,
zero use at startup and NVIDIA driver 580.178.04. The AMD Ryzen 5 5500
host ran both CPU arms. The job used the pinned qsim 0.22.1 wheel above
and discovery manifest
`fe92d68065081dd34e628539502a384e32099f2a90cf494121811032c2a553f0`.
It excluded the startup smoke from all timings.

Each fixture ran five fresh-child trials on each of the two- and eight-thread
CPU arms and the native CUDA arm. Every trial passed the selected-amplitude
and probability checks. The two-thread CPU arm had the faster median in each
cell. The ratios compare median timings on the same worker. The intervals
come from 10,000 paired bootstrap resamples within each cell.

| OpenQASM | Qubits | Depth | Verified pairs | CPU median / p90 | CUDA median / p90 | Speedup (95% interval) | Peak sampled VRAM |
| --- | ---: | ---: | ---: | --- | --- | --- | ---: |
| 2 | 24 | 20 | 5 | 7.01 / 7.21 s | 5.30 / 5.47 s | 1.32× (1.26–1.41×) | 243 MiB |
| 2 | 24 | 60 | 5 | 11.70 / 11.87 s | 5.66 / 5.92 s | 2.07× (1.96–2.14×) | 243 MiB |
| 3 | 26 | 20 | 5 | 14.89 / 14.99 s | 5.60 / 5.63 s | 2.66× (2.59–2.73×) | 627 MiB |
| 3 | 26 | 60 | 5 | 34.68 / 34.89 s | 6.33 / 6.54 s | 5.48× (5.27–5.56×) | 627 MiB |

CUDA peaked at 503 MiB host RSS. Three of the four cells cleared the 1.5×
screening threshold. Both 24-qubit circuits use OpenQASM 2 and both 26-qubit
circuits use OpenQASM 3. Each circuit seed is fixed at `481516 + width + depth`.
At both widths, the depth-60 fixture showed a greater speedup than the depth-20
fixture. A routing rule still needs replicated seeds, a common circuit dialect,
and cold and ready-worker API timings. The earlier 20-pair confirmation
remains the basis for the fixed canary.

The native job succeeded. SkyPilot terminated the owned allocation, and a
refreshed status check returned no matching worker. The full receipt was
captured before teardown. The internal session lasted 973 seconds and used no
paid cloud capacity.

## scqubits basis convergence with operator output

A separate CPU check computed the lowest 33 eigenpairs of the frozen models
with SciPy's EVR driver, then compared the lowest 32 after aligning phases in
their shared basis. The extra eigenpair checked the final gap. Fluxonium used
the common oscillator basis and FluxQubit used the common centered charge
grid. The comparison used scqubits 4.3.1 and SciPy 1.18.1.

| Model and dimensions | Maximum transition shift | Relative operator infinity-norm error |
| --- | ---: | ---: |
| High-ratio Fluxonium D1024 → D2048 | 0.00049 Hz | (5.65 × 10^{-13}) |
| High-ratio Fluxonium D2048 → D4096 | 0.00077 Hz | (8.77 × 10^{-13}) |
| FluxQubit D441 → D1089 | 649 Hz | (7.46 × 10^{-6}) |
| FluxQubit D1089 → D2025 | 0.000020 Hz | (5.11 × 10^{-12}) |
| FluxQubit D2025 → D4225 | 0.000028 Hz | (7.96 × 10^{-12}) |

The tested 32-level operator met the (10^{-8}) tolerance from D1024 on
high-ratio Fluxonium and from D1089 on FluxQubit. D441 missed that tolerance.
This closes operator convergence for these fixed models and operators. Larger
bases remain useful for measuring solver scaling.

## Remaining GPU measurements

The frozen 28-qubit, depth-60 qsim circuit completed one local CPU reference
with eight threads in 26.78 seconds. This gives the next same-worker trial a
comparator within the 120-second bound. The large scqubits CPU preparation
also passed on the D2048 high-ratio Fluxonium, D4096 Fluxonium and D4225
FluxQubit fixtures. Neither check establishes GPU performance.

An earlier attempt at the matched-depth comparison found free RTX 3060 slots
without the required host CPU and RAM. The V100
passed SkyPilot's resource dry run, then Kubernetes reported insufficient
CPU for the real request. The quoted A100 PCIe and SXM requests returned no
capacity. SkyPilot also tried the eligible L40 offering across all 14 quoted
regions without finding capacity. SkyPilot reported all five requests
terminated, with no benchmark allocation remaining, and $0.013 in estimated
Runpod charges. Provider invoice charges remain unverified.

Keep the 26-qubit, depth-60 RTX 3060 result as a candidate for the fixed
release canary. A second circuit seed, additional sampling, larger-basis
GPU timings and the exact packaged-image canary still need execution. [Deployment plan](deployment-plan.md)
