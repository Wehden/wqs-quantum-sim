# JoSIM workspace evidence and approved bounded-history design

Status: source audit and bounded native measurements complete on 2026-10-28. The workspace certificate remains unavailable. The owner approved the bounded-history design direction on 2026-10-28 and requested implementation planning. TASK-035/036 in [the task plan](../tasks.md#task-035-prove-the-native-josim-history-dependencies) await implementation approval; no native patch is implemented. Existing JoSIM admission remains closed.

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

Implementation sequencing is specified by TASK-035/036, pending task-plan approval:

1. Add a focused native-history task before completing TASK-020 certification and before TASK-022 integration. Lease the reviewed patch and a synthetic native equivalence/bounded-memory probe. Record immutable upstream, patch and resulting binary hashes.
2. Compare unmodified and patched native output for the existing SFQ fixture, the DC reproducers, each admitted trace/device family, timestep retries and failure cases. Require exact deterministic CSV/array equality under the same toolchain and preserved native output times; the 2% SFQ physical-reference check is separate. Any numerical difference requires an explicit per-trace tolerance with source-backed justification and owner acceptance before patch implementation; preserve deterministic behavior where the unmodified solver is deterministic.
3. Demonstrate bounded retained memory as internal step count grows while requested output count stays fixed. Prove the allocation line items from source, then test largest admitted shapes in the combined constrained environment. A successful small run alone does not close the certificate.
4. Complete JoSIM model admission only after that proof. TASK-023 must package the reviewed source patch and all required notices/relink materials and establish the production binary identity. Worker/API activation still waits for G2 and existing release/backup gates.

Alternative: define an explicitly narrower, mathematically certifiable input family. That would change the approved public capability scope and requires a separate owner decision. A generic workspace multiplier, hidden timestep/iteration cap, larger memory limit or silent fallback does not resolve this proof gap and is not proposed.

Native admission remains closed until the approved design is implemented and its evidence passes. Design approval does not close the workspace certificate, G2 or release acceptance gates.
