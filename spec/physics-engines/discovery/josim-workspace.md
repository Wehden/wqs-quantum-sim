# JoSIM workspace evidence and approved bounded-history design

Status: source audit and bounded native measurements complete on 2026-10-28. The workspace certificate remains unavailable. The owner approved the bounded-history design direction on 2026-10-28 and requested implementation planning. The owner confirmed on 2026-10-30 that TASK-035/036 in [the task plan](../tasks.md#task-035-prove-the-native-josim-history-dependencies) are already approved for execution. TASK-035 now records the full retained-history audit and frozen original-binary comparison harness below. TASK-036 has not started. Existing JoSIM admission remains closed.

## Why the output bound is insufficient

The accepted positive netlist grammar bounds requested output rows to 4,096. Pinned JoSIM retains selected trace history and time at every internal timestep, and downsamples only when writing output. Its retry loop can repeatedly halve the integration timestep without changing the print interval. A small CSV therefore does not establish a native memory bound.

All source links below refer to upstream commit `02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224`:

- [Simulation.cpp, lines 19-50](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L19-L50): transient retry loop.
- [Lines 325-328](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L325-L328): phase increment above `0.4*pi` requests a retry.
- [Lines 140-156](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L140-L156): timestep halving and history reset.
- [Lines 127-132](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L127-L132): selected variables and time are retained at each internal step.
- [Transient.hpp, line 35](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/include/JoSIM/Transient.hpp#L35): internal row count derives from `tstop/tstep`.
- [Output.cpp, lines 74-92](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Output.cpp#L74-L92): output downsampling follows retention.

The pinned CLI has no supported bounded-retention option. `-m` suppresses reporting; `.PRINT` selects variables and does not change history cadence. Fixed circuit dimensions and KLU workspace bounds cannot bound the separate retained-history vectors.

## Reproducer and actual measurements

The synthetic fixture is valid under the current schema and positive validator:

```text
B01 1 0 MOD AREA=1
V01 1 0 DC 0.1
.MODEL MOD JJ(RTYPE=1 VG=0.0028 CAP=7e-14 R0=160 RN=16 ICRIT=0.0001)
.TRAN 1e-12 1e-9
.PRINT PHASE B01
.END
```

The second run changes `.TRAN` to `1e-11 1e-8`. Both runs use the existing fixed native argv and produce the same number of output rows.

| Stop / requested step | Actual CSV rows | CSV bytes | Peak child RSS | Native elapsed |
| --- | ---: | ---: | ---: | ---: |
| 1 ns / 1 ps | 1,000 | 26,014 | 11,244 KiB | 0.0258 s |
| 10 ns / 10 ps | 1,000 | 26,014 | 100,908 KiB | 0.3978 s |

An independent coordinator repeat measured 11,116 KiB / 0.027621 s and 100,408 KiB / 0.389952 s respectively, again with 1,000 rows and 26,014 bytes. Both native exits were zero and stderr was empty. Execution used nonroot UID 1001, two CPUs, a 4-GiB cgroup, a 3-GiB address-space limit and a 12-MiB file limit, without network/capabilities and with a read-only image plus private scratch. A discovery-harness guard was not reached and introduces no product timeout.

For the likewise valid `.TRAN 1e-9 1e-6`, the stable DC relation implies a phase increment of approximately `h*0.1/SIGMA`, where `SIGMA=3.291059757e-16`. Satisfying `0.4*pi` requires `h <= 4.1357e-15 s`. Eighteen halvings give approximately `h=3.8147e-15 s` and 262,144,000 internal steps. This is a derivation assuming the stable DC regime, not a full-size execution measurement. Time and one phase history alone then occupy 3.90625 GiB of logical double storage. The measured GCC 13 vector-growth policy would allocate capacity `2^28` for each vector, or 4 GiB before other state and reallocation overlap. The approved total charge is at most 2 GiB. No giant exhaustion run was needed or performed to claim a measured limit.

A regression covers the 1-ns, 10-ns and 1-us forms: they remain structurally valid, have small predicted output counts, and fail admission because a native workspace certificate is absent. The focused validation/security suite passes 141 tests.

## Frozen provenance

| Artifact | SHA-256 / identity |
| --- | --- |
| Native discovery image | `sha256:30465f32041d36dc6f3d0ac1e20fe5b856f72a25943f74829683cfc7fea61b79` |
| Native executable | `b75dae95de6c4601efef4aac377104ba6b12a94c7d36f6b9b0ecde8cf9372450` |
| Simulation.cpp | `429fe2f8ab3cb8d61251846c2cfae05f83cba6b031359b7dae63f98341097130` |
| Output.cpp | `21c61a22b8a312e7d02bd87bc51fe77caaf1c4a46c54dbaffd3b9040f3e42bac` |
| Transient.hpp | `8ab6da4f543c3137681f20bbb8c6ceb1a83d09d4ee9dda177876021aac9b7543` |
| Fixed discovery probe | `3bae80e16eeeb66837d1b50c5128c99878f7fa3d8abf48e7f7f565cd3bb1fa5a` |

The discovery binary has the distribution obligations already recorded in [josim.md](josim.md). These measurements do not authorize redistribution or substitute for final worker-image acceptance.

## Owner-approved design direction

Preferred approach: retain the existing API, positive grammar, output semantics, SkyPilot launcher and CPU limits, and permit a narrowly reviewed patch to the pinned native JoSIM source that bounds retained history independently of internal timestep count.

The native patch must preserve integration equations, adaptive-step decisions, convergence behavior, trace units/order and the native output-selection/interpolation rules. Retain only the rolling history proven necessary for the admitted device equations and output conversion, plus bounded requested output samples. Before discarding any history, audit every read of that history for the allowed R/L/junction/DC/PWL family. If that audit cannot prove equivalence, leave the family gated and return the design question; do not replace native numerical rules with an approximation.

The patch adds no iteration, wall-time or job-wide work cutoff. Existing explicit memory/result limits still apply, with checks before allocation and sanitized resource failure rather than partial success. Requested output is neither padded nor truncated. Long-running work remains allowed within its memory profile.

Implementation sequencing follows approved TASK-035/036:

1. Add a focused native-history task before completing TASK-020 certification and before TASK-022 integration. Lease the reviewed patch and a synthetic native equivalence/bounded-memory probe. Record immutable upstream, patch and resulting binary hashes.
2. Compare unmodified and patched native output for the existing SFQ fixture, the DC reproducers, each admitted trace/device family, timestep retries and failure cases. Require exact deterministic CSV/array equality under the same toolchain and preserved native output times; the 2% SFQ physical-reference check is separate. Any numerical difference requires an explicit per-trace tolerance with source-backed justification and owner acceptance before patch implementation; preserve deterministic behavior where the unmodified solver is deterministic.
3. Demonstrate bounded retained memory as internal step count grows while requested output count stays fixed. Prove the allocation line items from source, then test largest admitted shapes in the combined constrained environment. A successful small run alone does not close the certificate.
4. Complete JoSIM model admission only after that proof. TASK-023 must package the reviewed source patch and all required notices/relink materials and establish the production binary identity. Worker/API activation still waits for G2 and existing release/backup gates.

Alternative: define an explicitly narrower, mathematically certifiable input family. That would change the approved public capability scope and requires a separate owner decision. A generic workspace multiplier, hidden timestep/iteration cap, larger memory limit or silent fallback does not resolve this proof gap and is not proposed.

Native admission remains closed until the approved design is implemented and its evidence passes. Design approval does not close the workspace certificate, G2 or release acceptance gates.


## TASK-035 retained-history audit, 2026-10-30

The audit covers the pinned source, current positive grammar and fixed phase-analysis CLI, not every JoSIM feature. The history dependency proof passes for that execution path: numerical integration has fixed-size state; output conversion requires the current and two preceding **internal** rows. No necessary unbounded history consumer was found in the admitted path. This permits planning the bounded-history patch. It does not certify native memory, prove a patch equivalent, enable JoSIM or close G2. TASK-036 has not started.

The boundary is material: explicit selected traces, R/L/B devices, independent I/V sources with DC/PWL, two-argument `.TRAN`, phase analysis, default startup, print start zero, FIR disabled, CSV output. Existing syntax admits at most 32 devices, 16 node names including any ground, 64 pairs per PWL, eight traces and 4,096 predicted rows. Noise, transmission lines, mutual inductance, capacitor/controlled/phase sources, IV analysis, alternate output formats, caller files and voltage analysis remain outside that grammar and fixed invocation.

### Reader-to-lifetime ledger

Every link below fixes the full upstream commit. A repository-wide search of `xVector`, `timeAxis` and `results` found Results accesses only in Simulation, Output and the excluded IV consumer; Errors contains names in messages. Device histories were separately searched in accepted component headers/constructors and every Simulation handler. The audited source files were byte-compared against the corresponding pinned Git blobs; all matched. A pre-existing local build-dependency CMake adjustment was not part of this numerical-source audit and was not changed.

| Producer / allocation | Reader and exact dependency | Minimum lifetime / patch obligation |
| --- | --- | --- |
| [Results declarations, Simulation.hpp 22-26](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/include/JoSIM/Simulation.hpp#L22-L26); [Simulation setup 53-71](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L53-L71) allocates optional vectors only for selected node/current/JJ variables. | [RelevantTrace 129-145](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/RelevantTrace.cpp#L129-L145) contributes at most three indices per trace and uniquifies. | At most 24 selected indices before deduplication. Optional slots are bounded by circuit dimension; unrelated variables need no trace storage. |
| [Simulation 110-132](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L110-L132) appends each selected solution value and `i*h` after every successful solve. | [Output 74-92](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Output.cpp#L74-L92) selects the first native time at or after each successively added print threshold. | Keep previous two internal samples plus current, and only selected output records. Preserve multiplication `i*h`, accumulated floating threshold additions and actual selected native time. Do not synthesize requested times, round indices, interpolate or select every kth step. |
| [Simulation 31-32 / 65-66 / 85-86 / 159-164](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L159-L164) maintains solution and right-hand-side vectors. | Every accepted handler reads the previous solved `x_`; KLU overwrites the next solution. | Circuit-sized state survives the next solve. Neither vector reads Results. Preserve solver ordering and floating equations. |
| [Resistor state, Resistor.hpp 42-58](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/include/JoSIM/Resistor.hpp#L42-L58). | [Simulation 210-237](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L210-L237): node difference and fixed `pn1..pn4`; phase BDF uses `pn1,pn2`. | Keep all existing scalar fields unchanged; no time-growing device history. Ground-positive, ground-negative and two-node signs must remain identical. |
| [Inductor state, Inductor.hpp 39-64](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/include/JoSIM/Inductor.hpp#L39-L64). | [Simulation 239-260](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L239-L260): phase-analysis inductance is in the fixed matrix; the voltage branch uses fixed `In2..In4` and previous solution. | No retained Results dependency. Mutual-inductance vector is empty in the admitted grammar. Do not alter either branch's device scalars. |
| [JJ.hpp 53-93](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/include/JoSIM/JJ.hpp#L53-L93) holds fixed phase/voltage and junction transition state. | [Simulation 291-395](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L291-L395): `pn1..pn4`, `vn1..vn6`, phase prediction, RTYPE=1 transition and LU refactor decision. | Keep all existing scalar history, including older rollback fields. No full Results read. The phase increment test and its `i/simSize > .01` condition must remain exact. Header `step_back` methods have no call sites in this pinned solver. |
| [VoltageSource.hpp 24-38](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/include/JoSIM/VoltageSource.hpp#L24-L38). | [Simulation 397-428](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L397-L428): phase BDF uses previous node phase, `pn2` and source value at `i*h`. | Preserve fixed `pn1..pn4`, source evaluation and update order. No output-history read. |
| [Function PWL parse 73-132 and DC parse 287-292](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Function.cpp#L73-L132) allocate input arrays. | [PWL evaluation 313-332](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Function.cpp#L313-L332), [DC 471](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Function.cpp#L471), [current sources 197-208](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L197-L208). | Source input arrays live for a matrix attempt, at most 64 time/value pairs each; DC needs one amplitude. Evaluators are stateless: negative time before PWL zero returns zero, intervals use `x>=left && x<right`, the last endpoint holds the last amplitude. Preserve interpolation arithmetic exactly. No per-step append for these source types. |
| [Startup 74-107](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L74-L107). | Negative-time solves initialize device and solution state, but do not append Results. | Startup state carries into time zero; startup samples must never enter the output-history ring. Output indices zero and one have their own clipping rules. |
| [Retry 19-50 and 140-156](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L140-L156). | Timestep halving, RNG rewind, fresh Matrix, selected-index rediscovery and cleared Results precede a new attempt. KLU symbolic/numeric state is freed per attempt. | Reset output cursor, first-sample state, ring and selected records on every retry. Release abandoned attempt storage before a replacement can add to peak charge. `clear()` retains vector capacity; account for that explicitly. No retry counter or wall/work cutoff is proposed. |
| [Output FIR 58-72](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Output.cpp#L58-L72), [Transient defaults/parse](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Transient.cpp#L27-L61). | Two-argument `.TRAN` leaves `prfirwindow=0`, `prstart=0`; FIR has one normalized tap of 1.0. | No future-looking FIR samples are required. Preserve the existing zero accumulator, multiply by 1.0 and addition order, including signed-zero behavior. An alternate FIR mode cannot use this certificate. |
| [Voltage conversion 122-175](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Output.cpp#L122-L175). | JJ DEVV reads the JJ voltage variable directly. Other DEVV differentiates node phase using current, previous and second-previous internal rows. At index one, repeat index zero twice; at index zero, repeat the current value. | Depth two before the current sample is sufficient. These are internal neighbors, not previous printed rows. Retain exact final timestep and parenthesized BDF expression. Both node inputs and absent-ground zeros must be preserved. |
| [Phase conversion 176-203](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Output.cpp#L176-L203). | In admitted phase analysis, selected PHASE B is the current node phase difference. | Current raw selected row only. The alternate voltage-analysis phase integration below this range is excluded. |
| [Current conversion 261-289](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Output.cpp#L261-L289). | R/L/B/V current reads its current unknown. Independent I-source DEVI re-evaluates DC/PWL at the selected native time. | Current raw value and selected time only. Preserve source interpolation and sign, plus one-tap accumulation. |
| [Output trace buffers / CSV 343-377](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Output.cpp#L343-L377). | Selected time and per-trace vectors are formatted in existing order, scientific notation with precision six. | At most requested bounded output rows and eight traces. No padding, truncation or reordered columns. Keep formatter behavior and exact bytes. |
| [Transmission-line consumer 497-675](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Simulation.cpp#L497-L675); [IV consumer 90-109](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/IV.cpp#L90-L109); [voltage-analysis phase output 205-259](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Output.cpp#L205-L259). | Delayed transmission lines read `i-k`, `i-k-1`, `i-k-2`; IV scans retained data; alternate phase conversion builds a full integration vector. | All are unreachable through the current accepted input/fixed argv. Guard the bounded path against these modes; leave legacy behavior outside the certified path. Their history must not be silently discarded under a broader claim. |

One existing native/schema discrepancy is now explicit. `.PRINT DEVV I01` passes the current positive validator for an independent current source, but [RelevantTrace 185-274](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/RelevantTrace.cpp#L185-L274) does not map that device to node indices: independent I sources are stored separately. Native emits an UNKNOWN_DEVICE warning, drops that column and exits zero. The production adapter's exact header check rejects the incomplete result. `source_traces` freezes this warning and actual header. This audit does not change that existing behavior or narrow the grammar; resolving the capability mismatch is a separate corrective contract obligation under TASK-020/021/G2. It must not be silently fixed or narrowed by TASK-035/036. This case is expected WQS-boundary failure, not successful API support or a convergence failure. Its actual native header is `time, V(V01), I(V01), I(I01), V(R02)`; the requested `V(I01)` is absent.

### Checked arithmetic and allocation obligations

The native history fix needs explicit numeric/index checks alongside bounded storage:

- `Transient::simsize()` casts `tstop/h` to signed int64 before checking finite range. Validate positive finite `h` and stop, finite quotient, and representability **before** the cast, preserving truncation for representable cases. A double rounded to `2^63` must be refused even though it compares equal to a rounded `INT64_MAX` double.
- Startup currently casts `2*pow(10,(abs(log10(h))-12)*2+1)` to int64 before clamping to 1,000. Saturate before the cast when the positive expression exceeds the already-existing startup clamp; preserve original truncation below it. The positive grammar permits very small times, so this cannot be assumed safe from input row bounds.
- On a retry, check the halved timestep remains positive and representable before using it to rebuild matrix coefficients; check required quotients/products and finite output conversion coefficients. Fail closed on unrepresentable numerical/index arithmetic with no partial result. These are machine-representation checks, not a new simulation work cutoff. Existing integration/retry equations and accepted numeric syntax remain unchanged.
- Native `i` remains int64; computed actual sample time is still `i*h`. Selected output indices, stored rows, flat offsets and all `count*sizeof(T)` charges require checked arithmetic before allocation. Guard `j-1`/`j-2` with the native zero/one clipping cases. Existing Output's initial `auto i=0` scan stops immediately at native time zero in the admitted `prstart=0` path; it is not an admitted long scan. The full internal axis still cannot be indexed with a newly introduced 32-bit counter.
- With at most 16 node names, use conservative `D <= 16 + 32 + 32 = 80` for solution unknowns: one per nonground node, at most one branch current and one JJ variable per device. This is deliberately conservative even when ground consumes a node slot. `K <= 3*8 = 24` selected raw indices. A sufficient, source-derived history representation is a three-row raw ring plus current/previous/second-previous selected snapshots for each printed row. Its double payload is at most `8*(3*80 + 3*24*4096 + 4096) = 2,393,984` bytes, independent of internal step count. This is a sufficient bound, not a claim that every retained scalar is necessary.
- Reserve fixed capacities from checked dimensions and account for optional/vector/container overhead, output indices, CSV formatting buffers and any simultaneous old/new allocation. Reusing capacity after retry is acceptable only if the peak charge proves it remains bounded. Output trace vectors add at most `8*9*4096 = 294,912` logical bytes. Neither number includes KLU, matrix/setup allocations, library buffers or allocator overhead: the full at-most-2-GiB planned charge and 3-GiB address-space certificate remain TASK-020/036 work. Observed RSS cannot replace that certificate.

### Exact upstream source-path lease for TASK-036

The proposed repository artifact remains `vendor/josim/bounded-history.patch`, under TASK-036's existing three-file lease. Its native hunks may touch exactly:

```text
include/JoSIM/Simulation.hpp
src/Simulation.cpp
src/Output.cpp
include/JoSIM/Transient.hpp
```

`Simulation.hpp` owns Results bounded capture metadata and storage. `Simulation.cpp` owns selection cadence, capture/reset, startup and retry checks. `Output.cpp` owns bounded raw-row access and exact existing conversion/formatting behavior. `Transient.hpp` owns checked timestep/index representation. R/L/JJ/source equations, source interpolation, RelevantTrace mapping and matrix factorization are read-only dependencies. `Matrix::reduce_step` is transmission-line setup logic and unreachable in this admitted family. A patch requiring another source path, a numerical tolerance, broader mode coverage or different product limits returns to the coordinator before writing it.

Keep unsupported legacy modes on their original full-history path; select the bounded path only when its audited conditions hold. A bounded-path eligibility guard must never silently make an unsupported circuit eligible for WQS. The chosen storage layout and its capacities still need TASK-036 source review and native instrumentation. No native source file was changed during TASK-035.

### Frozen comparison harness and execution evidence

The new probe is [probe_josim_history.py](../probe_josim_history.py). It accepts only explicit trusted absolute local binary paths, pins the original executable digest, and contains literal frozen fixture/CSV/parsed-array/diagnostic digests and headers. It has no provisioning, download, network, GPU or production entry point. Child environment is rebuilt with synthetic private HOME, locale and one-thread settings; no ambient tokens or service settings are passed through. Native stderr uses an FSIZE-limited scratch file, not an unbounded capture pipe. The 30-second default experiment guard is a discovery-harness guard only and was never reached; it is not a product timeout or work policy.

Comparison requires equal exit status, exact CSV bytes, exact parsed headers/numeric arrays and SHA-256 of big-endian IEEE-754 double arrays in row-major order. Diagnostic digests are also equal. No waveform tolerance is accepted. Separate SFQ integrals remain within 2% of one flux quantum over 50-200 ps. The probe cannot rewrite its frozen references. Parser negative checks reject nonfinite, backwards-time and ragged output; a deliberate array-digest mutation is rejected.

The following fixed fixtures ran twice per self-comparison under the unchanged CPU profile. Fixture, CSV, array and diagnostic hashes are frozen in the probe's `FROZEN` manifest; modifying any original reference fails the command.

| Fixture | Coverage | Exit | Rows | CSV bytes |
| --- | --- | ---: | ---: | ---: |
| `sfq` | Existing two-junction SFQ, R/L/B, DC-equivalent held PWL and pulsed I/V; voltage/phase/current physical reference | 0 | 999 | 78,282 |
| `dc_1ns` | Exact 1-ns small-output DC reproducer; phase history and adaptive retry | 0 | 1,000 | 26,014 |
| `dc_10ns` | Exact 10-ns reproducer with unchanged requested rows and greater internal work | 0 | 1,000 | 26,014 |
| `retry_derivative` | DC-driven retry plus V/B DEVV, V/B DEVI, B PHASE and I PWL current at selected native times; tests internal neighbors across downsampling gaps | 0 | 1,000 | 92,063 |
| `families` | R/L/B/I/V; R/L/B DEVV and DEVI, B PHASE, I DEVI; signed waveforms, node orientations, zero/one output indices and exact PWL endpoint/interior/held-tail evaluation | 0 | 79 | 9,635 |
| `source_traces` | V DEVV/DEVI, I DEVI, native rejected I DEVV warning; half-step PWL knots and fractional final output interval | 0 | 8 | 574 |
| `singular_failure` | Contradictory ideal voltage constraints; real native singular-matrix failure, no partial CSV | 255 | 0 | 0 |

The DC fixtures exercise the solver's native retry condition: the requested steps imply steady DC phase increments far above `0.4*pi` at 0.1 V; the accepted run must halve its step. The probe makes no claim to measured retry counts. JoSIM's admitted solver has no separate finite nonlinear iteration convergence status: the failure fixture covers its actual KLU singular-solve error. Neither an invented retry cap nor a time-limited convergence failure was substituted.

Self-comparison completed with seven fixtures, three malformed-CSV checks and one comparator-tamper check. Both binaries were the same pinned executable, so this **does not prove native patch equivalence**. The independent existing SFQ probe also passed: 999 rows, 78,282 bytes, 23 adversarial variants rejected, SFQ flux ratios `0.9999982732007825` and `0.9999995315993714`. Its existing excluded-noise discovery checks reproduced seed 42 and changed with seed 43. Native elapsed for the final self-comparison was about 0.0036 s for SFQ, 0.0317 s for DC 1 ns and 0.4143 s for DC 10 ns. Timings are observations, not acceptance thresholds.

Exact isolated execution setup:

```sh
docker run -d --name wqs-josim-history-035 --user 1001:1001 \
  --cpus 2 --memory 4g --memory-swap 4g --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 \
  --mount type=bind,src="$PWD/spec/physics-engines/probe_josim_history.py",dst=/workspace/spec/physics-engines/probe_josim_history.py,readonly \
  --mount type=bind,src="$PWD/tests/fixtures/physics/josim/sfq.cir",dst=/workspace/tests/fixtures/physics/josim/sfq.cir,readonly \
  --entrypoint /bin/sh \
  sha256:30465f32041d36dc6f3d0ac1e20fe5b856f72a25943f74829683cfc7fea61b79 \
  -c 'sleep 7200'
docker exec wqs-josim-history-035 /usr/bin/python3.12 \
  /workspace/spec/physics-engines/probe_josim_history.py \
  --original /build/josim-cli --candidate /build/josim-cli
docker exec -i wqs-josim-history-035 /usr/bin/python3.12 - \
  --binary /build/josim-cli \
  --fixture /workspace/tests/fixtures/physics/josim/sfq.cir \
  < spec/physics-engines/probe_josim.py
docker rm -f wqs-josim-history-035
```

Each numerical child enforces AS=3,221,225,472 bytes, FSIZE=12,582,912 bytes and disabled core files. Only the two public synthetic probe/fixture paths are mounted; repository credentials and host secrets are absent. No GPU or external service was used. The large 1-us exhaustion case was not run. Container removal applies only to this task's named container.

### Additional immutable audited-source identities

The source commit and three principal hashes appear in Frozen provenance above. These additional hashes bind the reader/lifetime ledger:

| File | SHA-256 |
| --- | --- |
| `include/JoSIM/Simulation.hpp` | `56cc0af7501501bcdc0283783cce27e38a8c22ccdbfdf12e18907c761f895e5b` |
| `src/Function.cpp` | `6f3571ca7e6c7912ff027efd767b847cb6cb9dc24b36fb1cc1e3439f5a08ca8b` |
| `src/RelevantTrace.cpp` | `83e60b48da28ea710d875243a6b678768062ae1467824ba950175a5511ba94cb` |
| `src/Transient.cpp` | `bdbbaccc2ed853af440ac69a8d5567c432c9ffd6f8e5484e9f39eb9548a65de8` |
| `src/Matrix.cpp` | `a4206a94da98187f5c639db6c239b04d8da56f1c52e92b2ac33f9465c3c05ba1` |
| `src/IV.cpp` | `b9e9c33d1ab0d8336f0e6a24b485625e911c1748b4585e2aa92830fad04c092a` |
| `include/JoSIM/Resistor.hpp` | `f1019aaaccd1cb0e628f6a31922da91c43724162d31929d6ec7b603e77814e1b` |
| `include/JoSIM/Inductor.hpp` | `e5ebc1fca76738d0c05ec537ebab292fee4f9b06fe007d9230daac2846c909c8` |
| `include/JoSIM/JJ.hpp` | `5804bfd6cae62a1e23c415121db4f329d83c9de069071d20ff143b1387042d15` |
| `include/JoSIM/VoltageSource.hpp` | `5fbb5bf1f4c0e94271eeb0d384e7f38872168c089ea2776700f40ce3482b0180` |
| `include/JoSIM/CurrentSource.hpp` | `b4cc80c748e28810151a634ce90029b1bdf699025d5b899505041987aaa3ae44` |
| `include/JoSIM/Function.hpp` | `3ca2eaf093a26874142da4b87d655d3334cd2aa2cffa8a993ba4d2c072d412ce` |

TASK-036 must build a distinct candidate from the same pinned source/toolchain and compare it against these frozen references, then prove capacities and the full workspace charge. Until then, the certificate verdict remains **unavailable**, admission remains closed, and existing release/backup gates and credentials remain unchanged.
