# JoSIM discovery

TASK-004, measured 2026-10-25 Pacific / 2026-10-26 UTC. Native feasibility and a narrow positive subset are established. This discovery does not enable a service endpoint or claim production parser/packaging acceptance.

## Candidate and build provenance

Select **JoSIM v2.7** for implementation discovery rather than the proposal's unspecified 2.6.x. Its source includes a deterministic seed option, and the fixed SFQ test below passes. Pin the commit, not the moving release label:

| Item | Exact identity |
| --- | --- |
| Annotated v2.7 tag object | `b8ea91de54f500e181fb4be134e520d6145df19f` |
| Peeled JoSIM source commit | `02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224` |
| `git archive HEAD` SHA-256 | `101e4a0e60e5bc897649bb13285d29efbd57c9515124d8b012d9ed386c1dfb2c` |
| CMakeSuiteSparse source | `f4bc35614c99dd1853a807108d18bb56ea6d9348` |
| CMakeSuperLU source | `30b8ef4db1e8d8729f4a1cd53029da69653f7bd6` |
| CMakeCBLAS source | `8ee64086a8ab012595744c4a97a49cdfc73b81ab` |
| CI base image local content ID | `sha256:17fec9e20e50023882509aae2c52b536fead249c1e5bab4948daca712d678562` |
| Discovery image local content ID | `sha256:30465f32041d36dc6f3d0ac1e20fe5b856f72a25943f74829683cfc7fea61b79` |
| Native executable SHA-256 in discovery image | `b75dae95de6c4601efef4aac377104ba6b12a94c7d36f6b9b0ecde8cf9372450` |
| Final fixture SHA-256 | `0b8066e6d3074e4fcf7bde182d03e9e4aa134346739640315f1f2f6d025146be` |

The native version reports `v2.7.02a34ee`. Container compiler: GCC/G++ 13.3.0, Ubuntu package `13.3.0-6ubuntu2~24.04.1`; CMake 3.28.3; C++17, release build, default KLU solver, OpenMP disabled, C99 for C dependencies. Runtime shared dependencies are glibc, libm, libstdc++, libgcc and the ELF loader. Sparse solver objects are linked into the executable; `nm` confirms `klu_l_*` definitions.

Upstream's `cmake/dependencies.cmake` fetches CMakeSuiteSparse from **master.zip**, even though its CPM version says 0.1.0. The discovery scratch checkout replaces that URL with the exact commit above. No JoSIM numerical source was patched. Production must retain the immutable dependency pin. A first host build with GCC 15.2's default C standard failed in old SuperLU declarations (`cprint_soln`); explicitly selecting C99 built successfully. The constrained acceptance run used the Ubuntu compiler image, not that host executable.

The build embeds compile date/time, and the scratch image installed current Ubuntu build packages. These hashes identify the measured artifacts; they are not a claim of a byte-reproducible production build or published registry artifact. TASK-007/023 must lock the production toolchain/package sources and produce its own immutable image receipt.

Sources: [release](https://github.com/JoeyDelp/JoSIM/releases/tag/v2.7), [pinned source](https://github.com/JoeyDelp/JoSIM/tree/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224), [dependency declaration](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/cmake/dependencies.cmake).

## License and packaging gate

JoSIM and its upstream JTL fixture are MIT, copyright Johannes Delport. The adapted fixture retains the complete MIT notice. JoSIM LICENSE SHA-256 is `b1c5db5d5a373e8519fd35a4cc3f15da84fdc60977e14e2780893a398f93c38e` (original CRLF bytes).

The executable must **not** be described as having only MIT dependencies:

- Pinned KLU and BTF sources carry `SPDX-License-Identifier: LGPL-2.1+`, University of Florida copyright. Static KLU linkage is present in the measured binary.
- AMD, COLAMD and SuiteSparse_config sources carry BSD-3-clause identifiers and their original author notices.
- The declared SuperLU dependency identifies its source as BSD and references `License.txt`; the wrapper checkout does not provide that referenced file. Default KLU mode still builds the SuperLU target, although this probe does not establish that unused SuperLU objects enter the final executable.
- The CBLAS wrapper contains f2c-translated sources without a complete top-level license/notice file. Provenance and redistribution notices need completing before distributing the worker image.

TASK-007/023 must collect the complete upstream notices/source correspondence and choose an LGPL-compliant distribution/linking arrangement, including any required replacement/relink materials. This discovery does not waive that packaging gate or infer that JoSIM's MIT grant covers third-party code. No binary or native library is committed here.

## Positive subset prototype

`probe_josim.py` is an executable discovery boundary, not the eventual production validator. It normalizes case, strips only whole-line `*`/`#` comments, joins leading `+` continuation lines, then **full-matches every logical statement and every numeric token**. It returns this normalized text for the native run; it never validates one representation and launches the original. Numeric values are finite decimal/scientific literals with explicit SPICE engineering suffixes. No expression evaluation is added.

Allowed initially:

- R and L elements, positive numeric values, numeric node IDs; B junctions with explicit model and positive numeric AREA.
- I/V sources with numeric DC or explicit PWL time/value pairs only. PWL starts at zero, has strictly increasing knots, at most 64 pairs, and ends within one microsecond.
- Exactly one `.MODEL <name> JJ(...)` with exactly `RTYPE=1`, VG, CAP, R0, RN, ICRIT, all numeric and positive. No native model kwargs pass through.
- Exactly one `.TRAN <step> <stop>`, positive ordered values, stop at most one microsecond, at most 4096 predicted output rows.
- One to eight unique `.PRINT DEVV|DEVI|PHASE <device>` traces. Every device exists; PHASE references junctions only. Terminal `.END` is mandatory and no executable statement follows it.
- Prototype ceilings: 64 KiB ASCII text, 4096 characters per logical line, 256 logical statements, 32 elements, 16 nodes, 32-character identifiers. Numeric scalar magnitudes are bounded; positive R is at most 1e6 ohm, L at most 1e-3 H, junction AREA at most 100.

These are conservative prototype ceilings sufficient for this fixture. They are **not** calibrated public admission promises. TASK-006/020/021 must finalize class-specific physical bounds, maximum-shape working memory, schema error locations and output prediction, then independently test the production implementation. Shorted elements, duplicate names/models, missing references, orphan continuations and extra model fields fail before launch.

Native parser audit and exclusions:

| Native feature | Pinned source observation | Proposed v1 treatment |
| --- | --- | --- |
| Include/file aliases | `Input::read_input` recognizes `.INCLUDE`/`INCLUDE` and `.FILE`/`FILE` before control parsing, with case folding | Reject all forms everywhere |
| `.CONTROL` | Allows bare control aliases inside block | Reject the entire block and bare commands |
| `.IV` | Can write a separate caller-named file | Reject |
| `.SAVE`, `.PLOT`, print alternatives | Native control language is broader than the selected output contract | Reject; server selects exact CLI output path |
| Continuations | Native joins leading `+`; a blocklist applied before joining is insufficient | Join then full-match; appended directives fail grammar |
| Comments | Native skips whole `*`/`#` lines, removes CR and tokenizes whitespace | Accept whole-line comments; inline comment/semicolon syntax is outside subset |
| Source waveforms | `Function::parse_cus` opens an external waveform file; native also supports PULSE/SIN/EXP/NOISE/PWS | Accept only fully numeric DC/PWL initially |
| Parameters | `.PARAM` and expression parsing are supported natively | Reject parameters, braces and unresolved names in numeric positions |
| Subcircuits | `.SUBCKT`/`.ENDS` and X elements expand native circuits | Reject all subcircuits for v1, including nested/recursive definitions |
| Model/device noise | Native TEMP/NEB and OPTION SEED exist | Tested separately below; not admitted by this subset |

The probe rejects 23 fixed adversarial/unsupported variants, including mixed-case include, no-dot file, control aliases, `.iv`, external CUS waveform, parameterized PWL, a continuation-appended file command, output explosion, unknown trace and content after `.END`. Safe lowercase input and a numeric-only continuation still validate. The native routine additionally refuses unexpected scratch files, nonfinite/nonincreasing output, more than 4096 rows and output above 6 MiB. These checks supplement, rather than replace, pre-execution validation and process isolation.

Sources: [input/control parser](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Input.cpp), [native waveforms/file access](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/src/Function.cpp), [native syntax](https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/docs/syntax.md).

## Native constrained acceptance

The fixture adapts upstream `test/ex_jtl_basic.cir`: two junctions, the same passive network/model, a bias ramp at 0–5 ps, and one 827.13 µV triangular input pulse at 100–105 ps. `.IV` and its output file are removed. Stop is 250 ps and output step 0.25 ps. Selected traces are `V(B01)`, `P(B01)`, `V(B02)`, `P(B02)`, `I(ROUT)` with native SI units V, rad, V, rad, A; time is seconds.

The fixed **50–200 ps** integration window includes the single SFQ event and excludes bias settling. This is a physical distinction: integrating the whole startup gives 1.06433 and 1.09792 Phi0 because the bias ramp changes the junction phase before the pulse. After settling, both junction phase increments are approximately 2π.

| Measurement | Native constrained result |
| --- | --- |
| Rows / columns | 999 / 6, including time |
| Time range | 0 to 249.75 ps, strictly increasing |
| CSV bytes | 78,282 |
| B01 integral, 50–200 ps | `2.067830277728058e-15` V s |
| B01 relative error to h/(2e) | `1.7267992175e-6` |
| B02 integral, 50–200 ps | `2.067832879887255e-15` V s |
| B02 relative error to h/(2e) | `4.6840062862e-7` |
| Acceptance threshold | <0.02 relative error for each junction; both pass |
| Native process elapsed | 0.003875 s, includes process launch and native CSV write |
| Peak child RSS upper observation | 12,852 KiB across the native runs, including forked Python pre-exec footprint |
| CPU / cgroup memory | 2 CPU / 4 GiB |
| Child RLIMIT_AS / RLIMIT_FSIZE | 3 GiB / 12 MiB, unchanged |
| Native stderr / exit | No error / 0 |

No Python numerical simulator, mocked executable or reference output replaces JoSIM. CSV is parsed with the standard library; trapezoidal integration uses `Phi0=2.0678338484619295e-15` V s from SI h/(2e). The native command vector is fixed: `[trusted_binary, '-m', '-o', 'output.csv', 'input.cir']`, without a shell. Each run gets a private mode-0700 scratch directory. The child environment contains PATH and OMP_NUM_THREADS only. No credentials, network, Docker socket or cluster configuration are mounted into the measurement container.

### Noise and reproducibility

As a separate **trusted fixed discovery input**, the probe adds `.TEMP 4.2`, `.NEB 1e12`, `.OPTION SEED=42` to the normalized fixture. Two fresh native processes produce byte-identical CSV; seed 43 produces different CSV, and enabling noise changes the deterministic result. The pinned source derives separate noise/spread RNG streams from the supplied seed. This establishes reproducibility in this pinned image, not bitwise portability across compiler/platform changes.

The safe v1 prototype deliberately rejects these three directives. A future numeric temperature/noise/seed schema can generate only reviewed server-side statements after its own resource, range and reproducibility tests. Do not expose arbitrary `.OPTION` strings. No native GPU execution path is documented or found in this source/build; JoSIM remains CPU. The optional upstream OpenMP build flag is not GPU support.

## Reproduction

Build in a disposable checkout, with no production mounts. `BASE_IMAGE` below denotes the owner-configured CI image whose content ID matches the table, not a public hostname to publish:

```sh
git clone --branch v2.7 --depth 1 https://github.com/JoeyDelp/JoSIM.git josim-source
git -C josim-source rev-parse HEAD
# Assert 02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224 before continuing.
python3 - <<'PY'
from pathlib import Path
path = Path('josim-source/cmake/dependencies.cmake')
path.write_text(path.read_text().replace(
    'refs/heads/master.zip',
    'f4bc35614c99dd1853a807108d18bb56ea6d9348.zip'))
PY
```

Disposable build recipe (build tools only, no service configuration):

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    g++ cmake make ca-certificates git && rm -rf /var/lib/apt/lists/*
COPY . /src
RUN cmake -S /src -B /build -DBUILD_TESTING=OFF \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_STANDARD=99 \
    && cmake --build /build --parallel 2
USER 1001:1001
ENTRYPOINT []
```

Build that context as a local `wqs-josim-discovery:v27` scratch image, then run from the WQS repository:

```sh
docker run --rm --cpus=2 --memory=4g --pids-limit=64 --network=none \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m --cap-drop=ALL \
  --security-opt=no-new-privileges \
  -v "$PWD/spec/physics-engines/probe_josim.py:/probe.py:ro" \
  -v "$PWD/tests/fixtures/physics/josim/sfq.cir:/fixture.cir:ro" \
  wqs-josim-discovery:v27 python3 /probe.py \
  --binary /build/josim-cli --fixture /fixture.cir
```

Observed execution fits the existing worker profile. Still required before public JoSIM admission: packaging/license closure, production validator and adversarial tests, maximum-shape memory/output admission, native failure/cancellation mapping, NPZ metadata/units, lifecycle/backup tests and release acceptance. Existing backup gates and credentials were not changed.
