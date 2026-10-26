# scqubits discovery

Status: CPU discovery passed on 2026-10-25 for scqubits 4.3.1. This is library evidence for TASK-003, not a public capability or production worker acceptance. No runtime dependencies, API routes, deployment settings or credentials changed.

## Reproduction and isolation

The fixed probe is `spec/physics-engines/probe_scqubits.py`. Python 3.12.3 ran inside the existing CI image with immutable image ID `sha256:17fec9e20e50023882509aae2c52b536fead249c1e5bab4948daca712d678562`. The container had 2 CPUs, 4 GiB memory, and 4 GiB combined memory/swap, so it had no additional swap allowance. It had no GPU, credential mounts, Docker socket or production data. Set `CI_IMAGE` to the locally available image with that ID; its registry address is private configuration.

```bash
docker run -d --name wqs-scqubits-discovery \
  --cpus 2 --memory 4g --memory-swap 4g \
  --entrypoint bash "$CI_IMAGE" -c 'sleep 14400'
docker exec wqs-scqubits-discovery bash -euc '
  uv venv --python /usr/bin/python3.12 /tmp/scq
  uv pip install --python /tmp/scq/bin/python \
    scqubits==4.3.1 qutip==5.3.1 numpy==2.5.4 scipy==1.18.1'
docker cp spec/physics-engines/probe_scqubits.py \
  wqs-scqubits-discovery:/tmp/probe_scqubits.py
docker exec wqs-scqubits-discovery bash -euc '
  for cls in Transmon TunableTransmon Fluxonium FluxQubit; do
    for mode in reference boundary refuse; do
      flag=
      [ "$mode" != reference ] && flag=--$mode
      env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
        /tmp/scq/bin/python /tmp/probe_scqubits.py \
        --class-name "$cls" $flag > /tmp/$cls-$mode.json
    done
    env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
      /tmp/scq/bin/python /tmp/probe_scqubits.py --class-name "$cls" \
      --boundary --levels 32 > /tmp/$cls-boundary32.json
  done'
# Inspect the JSON receipts before removing this disposable container.
docker rm -f wqs-scqubits-discovery
```

Every numerical process applies `RLIMIT_AS=3221225472` and `RLIMIT_FSIZE=12582912` before importing NumPy, QuTiP or scqubits. Thread counts are one. Both limits remained in force; no preload or memory-limit workaround was used. Dependency installation is separate from the limited numerical child.

Installed transitive versions: contourpy 1.4.0, cycler 0.12.1, dill 0.4.1, fonttools 4.67.0, kiwisolver 1.5.1, matplotlib 3.11.2, mpmath 1.3.0, multiprocess 0.70.19, packaging 26.3, pathos 0.3.5, pillow 13.0.0, pox 0.3.7, ppft 1.7.8, pyparsing 3.3.3, python-dateutil 3.0.0, six 1.17.0, sympy 1.14.0, tqdm 4.70.1, typing-extensions 4.16.0 and tzdata 2026.5. These are a discovery environment, not the proposed production lock. TASK-007 must reconcile the combined existing-worker dependency set and preserve distribution notices.

## Constructor and operator boundary

These exact numeric constructor fields exist in the installed classes. The public schema should list them explicitly, rather than pass caller dictionaries to native constructors.

| Class | Numeric constructor fields | Raw basis dimension | Tested operator methods |
|---|---|---|---|
| Transmon | `EJ, EC, ng, ncut` | `2*ncut+1` | `n_operator, cos_phi_operator, sin_phi_operator` |
| TunableTransmon | `EJmax, EC, d, flux, ng, ncut` | `2*ncut+1` | `n_operator, cos_phi_operator, sin_phi_operator` |
| Fluxonium | `EJ, EC, EL, flux, cutoff` | `cutoff` | `n_operator, phi_operator, cos_phi_operator, sin_phi_operator` |
| FluxQubit | `EJ1, EJ2, EJ3, ECJ1, ECJ2, ECJ3, ECg1, ECg2, ng1, ng2, flux, ncut` | `(2*ncut+1)^2` | `n_1_operator, n_2_operator, cos_phi_1_operator, cos_phi_2_operator, sin_phi_1_operator, sin_phi_2_operator` |

All 16 class/operator pairs passed. The exponentiated-phase operators also exist upstream but are not needed for this initial n/phi/cos/sin allowlist. Fluxonium's trigonometric methods accept native `alpha` and `beta`; this probe fixes them to defaults 1 and 0. Do not expose extra native options implicitly. `phi_operator` does not exist for Transmon/TunableTransmon/FluxQubit; reject these pairs rather than synthesizing a phase operator.

Native constructors additionally accept `truncated_dim`, `id_str`, eigensolver methods and option dictionaries. The service owns these. Use explicit `esys_scipy_dense` / `evals_scipy_dense` CPU selection. Caller methods, callback objects, Circuit/YAML, `id_str`, and arbitrary kwargs are excluded.

Domain constraints to carry into schema design: all numeric values must be finite; charging energies and Fluxonium `EL` must be strictly positive; Josephson energies may be nonnegative, including the zero-Josephson degenerate charge case tested below. TunableTransmon asymmetry `d` can be confined to `[0,1]` as the chosen junction-label convention. Flux is in flux-quantum units and charge offsets are dimensionless, and either can be signed. There is no universal finite upper energy bound or convergence guarantee supplied by the library. Enforcing an arbitrary physical upper cutoff is not justified by these samples. TASK-006 should distinguish any numerical precision envelope from physical validity; extreme energy ratios, very large offsets and near-singular charging parameters need rejection/conditioning tests in TASK-017/018. The native library's permissive constructor is not an input validator.

The measured candidate allocation envelope is raw dimension at most 512, requested output levels from 1 through `min(32, raw_dimension)`, positive integer `ncut` or `cutoff`, and no booleans in integer fields. This gives `ncut<=255` for Transmons, `cutoff<=512` for Fluxonium, and `ncut<=10` for FluxQubit. The probe exercises 6 and 32 levels; it does not exhaustively validate every level count or physical parameter combination. Refusal fixtures are Transmon/TunableTransmon `ncut=256` (513), Fluxonium `cutoff=513`, and FluxQubit `ncut=11` (529). Each refused before native construction. This is a discovery guard, not an implemented production validator. Small output `truncated_dim` never reduces the Hamiltonian's full dimension.

## Numerical reference and export

The fixed parameters are in the probe. Reference raw dimensions are 61, 61, 110 and 121 respectively, with 6 returned levels. Each result obtains one eigensystem, orders energies ascending, and uses its same eigenvectors for every operator. All direct native `eigenvals` versus `eigensys` comparisons were exact in these runs. All manual `V.conj().T @ O @ V` comparisons with native `energy_esys=(energies,vectors)` were exact as well. The explicit native eigenbasis Hamiltonian matched `diag(energies)` within 1e-9 GHz.

Transmon `EJ=15, EC=0.3, ng=0, ncut=30` gives E01 **5.682575677295066 GHz**. The transmon approximation `sqrt(8*EJ*EC)-EC = 5.7 GHz` differs by **0.30569%**, within the fixed 2% criterion. The approximation is not a general oracle outside the transmon regime.

Exports subtract the ground energy and record that reference, serialize numeric real/imaginary arrays through JSON, reconstruct QuTiP matrices, multiply linear GHz by `2*pi` once, and divide the QuTiP eigenvalues by `2*pi` for comparison. Maximum spectrum error across all 12 execution fixtures was **2.842171e-14 GHz**, below 1e-9 GHz. Numeric NPZ round trips use `allow_pickle=False` and contain numeric arrays plus scalar Unicode metadata only. This proves a native numeric export; the future WQS Lindblad schema and HTTP round trip remain TASK-019 acceptance.

Charge bases run from `-ncut` to `+ncut`. FluxQubit uses the tensor product of the two charge bases. Fluxonium uses its harmonic-oscillator basis; its flux-dependent Hamiltonian is diagonalized in that basis. Eigenvector phases are not stable API data. The probe deliberately rephases eigenvectors and verifies the inverse coordinate transform. It also uses a real native degenerate Transmon (`EJ=0, EC=0.3, ng=0, ncut=3`), rotates its degenerate doublet, and checks the subspace projector and block-operator spectrum. Production tests must compare eigenspace projectors when a tolerance groups degenerate energies, rather than comparing raw eigenvector columns. Individual operator matrix entries inside a degenerate block are basis dependent.

FluxQubit E01 changes from 0.26350447531807175 GHz at `ncut=5` to 0.2616741539057297 GHz at `ncut=10`. The reference/export agreement does not certify charge-cutoff convergence. Clients need to choose and test adequate native cutoffs; the service should return cutoff and basis metadata.

## Constrained measurements

Single-run measurements on the pinned image, after final probe changes. Times exclude Python/library imports and scheduling. Earlier fresh-process wall measurements were roughly 0.56 seconds for small fixtures, 0.61 seconds for FluxQubit at 441 dimensions and 1.56 seconds for Fluxonium at 512; these are observations, not a latency guarantee or repeated performance study.

| Class | Raw D: reference / boundary | Reference RSS KiB | Boundary RSS KiB: 6 / 32 levels | Boundary compute seconds: 6 / 32 | Boundary NPZ bytes: 6 / 32 | Boundary numeric JSON bytes: 6 / 32 |
|---|---:|---:|---:|---:|---:|---:|
| Transmon | 61 / 511 | 242488 | 262908 / 262760 | 0.018074 / 0.027500 | 4042 / 67278 | 3563 / 98129 |
| TunableTransmon | 61 / 511 | 243360 | 262072 / 261472 | 0.017791 / 0.027167 | 4050 / 67282 | 3511 / 97964 |
| Fluxonium | 110 / 512 | 246240 | 293180 / 294220 | 0.985312 / 0.999364 | 4882 / 83922 | 4532 / 122145 |
| FluxQubit | 121 / 441 | 244660 | 257548 / 258620 | 0.049235 / 0.069026 | 6578 / 117238 | 10611 / 291690 |

At the boundary, native Hamiltonian buffers were 2,088,968 bytes for each Transmon, 2,097,152 for Fluxonium and 3,111,696 for FluxQubit. Complex operators can double the real-matrix allocation. Fluxonium's `n_operator` used 4,194,304 bytes. RSS includes imports and temporary native allocations, and is more informative than the returned 6x6 matrix alone. None approached the current 3 GiB address-space or 4 GiB container limits. These independent scqubits environments do not prove that the final combined worker imports fit; TASK-007/023 must rerun there.

Output measurements are the probe's NPZ and numeric JSON separately. They do not yet include HTTP/base64 duplication or a final WQS export schema; TASK-008/019 must account for those exact encodings and reject predictable over-allocation before construction.

## GPU suitability handoff

Installed `scqubits/core/diag.py` SHA256: `11d283c199721534032a9f0d1b8094471b849f0648a72d74b61fcb8c8dcee13b`. Its registered methods include `esys_cupy_dense`, `evals_cupy_dense`, `esys_cupy_sparse`, `evals_cupy_sparse`, and JAX dense equivalents. [Upstream diagonalization documentation](https://scqubits.readthedocs.io/en/latest/guide/settings/ipynb/custom_diagonalization.html) documents these optional backends and warns about eigenvalue-order selection.

For a controlled GPU probe, use server-selected `esys_method='esys_cupy_dense'` on these same class fixtures. The installed implementation constructs/casts the Hamiltonian on the CPU, calls `cp.linalg.eigh(cp.asarray(matrix))`, synchronizes, then copies selected energies/vectors back with `.get()`. Dense CuPy computes the full eigensystem even when only a few levels are requested. The sparse CuPy path uses `cupyx.scipy.sparse.linalg.eigsh` with `which='SA'`; it still transfers the host matrix. The 512-dimensional CPU examples are small, so transfer/context overhead may dominate, but this is a hypothesis to measure, not a reason to disable GPUs. Larger GPU cases require their own resource-policy evidence. No CUDA package or GPU execution is claimed by this report. TASK-005 owns actual hardware, limit compatibility, precision, cold/warm and scheduling measurements. JoSIM suitability is separate.

## Provenance, failures and next contracts

The installed scqubits 4.3.1 metadata contains the BSD 3-Clause license with Jens Koch and Peter Groszkowski attribution. QuTiP 5.3.1 identifies BSD-3-Clause; NumPy/SciPy wheels include additional bundled-library notices. Matplotlib, SymPy and transitive licenses must be preserved in the worker distribution review; importing a numerical class does not remove these installed dependencies. No upstream source was vendored.

Verification: **12 execution fixtures and 4 expected dimension refusals passed**, including all four required classes, all 16 allowlisted class/operator pairs, one-eigensystem exports, numerical residuals, native degeneracy and JSON/NPZ round trips. No existing tests were modified or assertions weakened. The initially attempted `/usr/bin/time` command failed because that tool is absent in the CI image; Bash's timer and Python `getrusage` supplied measurements instead. No native numerical failures remained. An upstream invalid-escape SyntaxWarning appears on the first scqubits import; it did not affect execution.

Open implementation work is the explicit schema/conditioning boundary, combined worker dependency/license validation, export wire format, and real GPU evidence. No required class or operator from the chosen initial allowlist was dropped. The 512-dimensional guard is a measured candidate for TASK-006, not evidence that larger bases fail or that this profile is the only usable allocation.
