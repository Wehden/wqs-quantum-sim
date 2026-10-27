# Combined CPU dependency evidence

TASK-007 prepares the dependency environment only. No physics route, capability, runtime adapter, GPU profile or deployment is enabled. Source baseline: `c761a0eac8874ead2360112c26e2188c81e6d482`, plus this task's manifest, lock and tests. Evidence date: 2026-10-26.

## Pins and platform boundary

The API gains only cross-platform `numpy==2.5.4`. The worker gains QuTiP `5.3.1`, scqubits `4.3.1` and an explicit SciPy `1.18.1` requirement, each conditional on `sys_platform == 'linux'`. NumPy and SciPy were already present at these exact versions in the existing combined worker lock. All existing direct and transitive package versions remain unchanged.

Only the new physics execution is certified for the approved Linux image/profile. Existing circuit engines and existing non-Linux lock targets retain their requirements. A non-Linux installation does not gain physics execution from these changes. CPU dependencies do not establish GPU support.

The regenerated universal lock adds exactly six distributions:

| Distribution | Version | Installed license metadata |
| --- | --- | --- |
| qutip | 5.3.1 | BSD-3-Clause |
| scqubits | 4.3.1 | BSD 3-Clause, Jens Koch and Peter Groszkowski |
| multiprocess | 0.70.19 | BSD-3-Clause |
| pathos | 0.3.5 | BSD-3-Clause |
| pox | 0.3.7 | BSD-3-Clause |
| ppft | 1.7.8 | BSD-3-Clause |

Comparison of the old and new lock's package-name/version maps found **zero existing version changes and zero removals**. The universal lock has 147 entries; the combined installed test environment has 146 distributions, excluding this virtual project.

### Universal-resolution workaround

scqubits' unchanged upstream wheel metadata includes:

```text
scipy>=1.5; sys_platform != "darwin"
scipy>=1.5; sys_platform == "darwin" and python_version < "3.10"
scipy<=1.13.1,>=1.5; sys_platform == "darwin" and python_version >= "3.10"
```

The PyPI wheel is `scqubits-4.3.1-py3-none-any.whl`, SHA256 `dd2b262c2418d3b6a3e1eb2aad83c53220f7cec881bdc6a629026f61bd80ff0d`. Its PEP 658 metadata and installed distribution metadata agree. No dependency metadata is overridden.

uv 0.11.6 nevertheless rejected the following fresh minimal project, even with no existing lock. The GNU host binary and pinned CI image's musl binary reproduced it; changing the fork strategy to `fewest` did not resolve it:

```toml
[project]
name = "wqs-platform-probe"
version = "0.0.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "scqubits==4.3.1; sys_platform == 'linux'",
    "scipy==1.18.1; sys_platform == 'linux'",
]
```

`uv lock` incorrectly encountered the mutually exclusive Darwin SciPy constraint in the Linux-marked dependency path. The approved workaround explicitly partitions resolution without excluding any platform:

```toml
[tool.uv]
environments = ["sys_platform == 'linux'", "sys_platform != 'linux'"]
```

The two sets are disjoint and their union is all platforms. Both the minimal project and the full project then resolve, preserving every preexisting version. Tests verify the marker boundary, coverage of Linux/Darwin/Windows/Emscripten/FreeBSD, existing unmarked engine requirements, cross-platform API NumPy, Linux-only resolved scqubits edges and the retained upstream Darwin restriction. There is no global Linux-only support declaration or metadata waiver.

## Installed notices and JoSIM boundary

All six new distributions retain their installed license files. QuTiP includes `qutip-5.3.1.dist-info/licenses/LICENSE.txt`; scqubits includes `scqubits-4.3.1.dist-info/licenses/LICENSE`; multiprocess and ppft also retain `COPYING`. The deployable worker keeps the complete virtual environment, including distribution metadata and bundled notices.

Existing NumPy `2.5.4` reports `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`; its wheel retains 20 license/notice paths. SciPy `1.18.1` retains five such paths, including its main license and bundled implementation notices. Existing Matplotlib `3.11.2` retains its PSF-based agreement and DejaVu/STIX font notices; SymPy `1.14.0` retains BSD notices and its parser license. Existing dill `0.4.1` retains its BSD-3-Clause license. These installed notices must remain in released images; this inventory does not replace release-level distribution review.

No JoSIM executable or native library is added. The separate [JoSIM discovery receipt](josim.md) pins v2.7 source `02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224`. Its KLU/LGPL linkage and complete third-party notice correspondence remain blocking distribution gates for TASK-023. Python dependency acceptance does not authorize redistribution of the discovery binary.

## Verification and import footprint

The disposable CI container used immutable image ID `sha256:17fec9e20e50023882509aae2c52b536fead249c1e5bab4948daca712d678562`, Python 3.12.3, two CPUs, 4 GiB memory and 4 GiB combined memory/swap. It received a copied source tree, no credentials, cluster configuration, production data, GPU devices or Docker socket. The host project's shared virtual environment was never changed.

Commands inside the copied project:

```bash
export UV_PYTHON=/usr/bin/python3.12
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
uv lock --check
uv sync --frozen --extra worker --extra controller --extra backup
uv pip check --python .venv/bin/python
uv run --frozen --extra worker --extra controller --extra backup pytest -q \
  tests/test_physics_dependency_versions.py tests/test_aer_capabilities.py \
  tests/test_physics_compatibility_baseline.py
bash tools/ci/check.sh
```

The focused suite passed **48 tests in 12.59 seconds**, with one existing TestClient deprecation warning. The full canonical CI script then passed **807 Python tests and 6 subtests in 111.80 seconds**, plus **3/3 console tests**, with 10 warnings and no skipped tests. This includes the existing 803 Python cases and four new dependency cases; no regression or unchanged failure remained. `uv lock --check` passed. The explicit project-interpreter check verified **146 installed packages, all compatible**. Bare `uv pip check` in this CI base selects `/usr` and checks zero packages; its success is not counted as dependency verification. TASK-030 can correct the canonical CI script's interpreter selection without broadening this task's write lease.

The new frozen-version test was first run against the original dependency environment and failed with `PackageNotFoundError: qutip`, then passed with the approved dependencies. An initial container copy was root-owned, preventing nonroot virtual-environment creation; correcting ownership of that disposable copy resolved the harness error. No production workaround or existing assertion weakening was introduced.

Native import/operation evidence also passed in the actual newly built deployable images:

- The API image imports NumPy and `qsim.api`, has no installed QuTiP/scqubits/SciPy distributions and loads none of those solvers. One fresh import took 0.623968 seconds with peak RSS 59,892 KiB.
- A worker process running as UID/GID 1000 applies the existing 3 GiB address-space and 12 MiB file limits before importing QuTiP/scqubits. A small QuTiP SE solve retained unit norm; a Transmon's six-level eigensystem had shape `(61,6)` and E01 agreed with the fixed transmon approximation within 2%. Import plus compute took 2.708167 seconds; peak RSS was 264,264 KiB. This is a small compatibility fixture, not largest-shape certification or a service latency promise.
- The worker-image test harness passed its startup followers, isolated module entry point and all three existing engines/adapters. New dependency tests also execute a sanitized fresh numerical child; native solvers remain absent from an API-only import.

The UID1000 worker probe produced Matplotlib cache-permission/fontconfig warnings and scqubits' existing invalid-escape SyntaxWarning, then completed successfully. Private writable cache/scratch setup belongs to later worker integration; no cache environment or memory limit was relaxed here. The combined CPU environment contains no new CUDA, CuPy or JAX dependency.

## Actual deployable image sizes

Before and after builds used the same existing `deploy/Dockerfile` targets and local base-image references without pulling between builds. The before context was an exact archive of the baseline commit; the after context differed only in this task's manifest, lock and test file. Docker's image `Size` is a logical uncompressed size, not compressed registry transfer size, disk-cache growth or an installed-environment estimate.

| Target | Before bytes | After bytes | Delta bytes |
| --- | ---: | ---: | ---: |
| API | 317126689 | 393740697 | +76614008 |
| Worker | 2207500806 | 2383593757 | +176092951 |

Local image identities:

```text
API before:    sha256:d8f8830ee6bf85f45c6498de145953f60809a576d409845b69095a251c6abbfc
API after:     sha256:78871e817d51e95873597e12ac878aa8692f98321740c0295c7be7aa9854cbb1
Worker before: sha256:05529c3cffb7240521568b109e2dca597f21ee69b7f680a6572fff03b3e5e6d9
Worker after:  sha256:859959a27bec43b336eb0613b09ad9b1c73fcfce809abfbcade0919b95711c45
```

Reproduce from separate before/after source directories:

```bash
docker build --target api --tag wqs-deps-api:local --file deploy/Dockerfile .
docker build --target worker --tag wqs-deps-worker:local --file deploy/Dockerfile .
docker image inspect wqs-deps-api:local wqs-deps-worker:local --format '{{.Id}} {{.Size}}'
bash tests/check_worker_image.sh wqs-deps-worker:local
```

Only task-owned temporary containers/tags are cleaned up. No image was published, production deployment changed or credential rotated.

## Handoff

The four-file lease is limited to `pyproject.toml`, `uv.lock`, `tests/test_physics_dependency_versions.py` and this receipt. No new production symbol or runtime interface is introduced. The tests freeze installed pins and compare any later advertised Lindblad/scqubits version with its installed package; new public capabilities remain gated by later tasks. Largest-shape CPU acceptance, physics adapter behavior, JoSIM packaging and release acceptance remain separate gates.
