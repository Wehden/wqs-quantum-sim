# Lindblad native discovery

Status: CPU reference probes complete; production integration and final admission policy remain TASK-006 decisions. No capability, dependency lock, worker image or service configuration changed.

## Reproduction and environment

Date: 2026-10-25. Source base: `83ffac2ebc55423cc804b62342337399b17373c3`, plus the adjacent discovery probe. All inputs are fixed synthetic models.

The original nine probes passed before extension, both locally and in the isolated CI runtime. The extended probe passes in that same runtime with actual CPU, container memory, child address-space and child file limits:

```bash
docker run --rm --name wqs-lindblad-discovery --cpus=2 --memory=4g \
  --entrypoint bash \
  --mount type=bind,source="$PWD/spec/physics-engines",target=/probe,readonly \
  sha256:17fec9e20e50023882509aae2c52b536fead249c1e5bab4948daca712d678562 \
  -euc 'uv venv --python /usr/bin/python3.12 /tmp/lindblad-env
  uv pip install --python /tmp/lindblad-env/bin/python qutip==5.3.1 numpy==2.5.4 scipy==1.18.1
  OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /tmp/lindblad-env/bin/python /probe/probe_lindblad.py --constrained
  du -sb /tmp/lindblad-env
  cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max'
```

The command uses the verified local image ID; provision the matching CI base image locally before running it. No private registry hostname is required in source. The image is a CI base, **not a completed combined physics worker image**. No credentials, Docker socket, host datasets or cluster configuration are mounted. Dependencies are installed only into disposable container scratch, with no repository lock changes.

Python 3.12.3; QuTiP 5.3.1; NumPy 2.5.4; SciPy 1.18.1; packaging 26.3. Measured cgroup `memory.max=4294967296`, `cpu.max=200000 100000`. The probe applies `RLIMIT_AS=3221225472` and `RLIMIT_FSIZE=12582912` **before native imports**. Solver tolerances are `atol=1e-10`, `rtol=1e-9`; serial BLAS/MC. Matplotlib is absent, producing the expected graphics warning without affecting these operations.

Measured solver-section wall time: **0.352327 s**, peak process RSS **120408 KiB**. This excludes environment installation/import time and is not a service latency claim. Disposable environment footprint: **309184805 bytes**. The example numeric/Unicode NPZ is **1203 bytes**, not a maximum output-size measurement. The container is removed on exit.

## Results

| Fixed case | Measured error/result | Acceptance |
| --- | --- | --- |
| Two-level Rabi | 7.4855e-9 maximum absolute | <1e-4 |
| T1 decay | 1.5742e-8 maximum relative | <1e-4 |
| Unitary propagator | 2.3922e-8 norm | <1e-6 |
| Explicit zero-collapse Liouvillian propagator | 9.6536e-8 norm; 4x4 superoperator | <1e-6 and explicit superoperator |
| Numeric array-coefficient Rabi | 7.4855e-9 absolute | <1e-4 |
| 30-level coherent ringdown | 8.1774e-10 relative | <1e-3 |
| Thermal steady occupation | 5.9924e-13 absolute | <1e-6 |
| Dispersive shift | 1.3688% relative | <5% |
| 1000 serial MC trajectories, seed 42 | 2.2503 maximum standard errors at 20 noninitial times | <=3 |
| Same MC call and seed repeated | 0 maximum difference | exact within pinned environment |
| Complex coefficient with conjugated adjoint | 9.3540e-10 state norm error; Hermitian | <1e-6 |
| Pure ket converted to density, pure density, maximally mixed density | <=2.4420e-8 norm error | <1e-6; trace 1 and eigenvalues >=-1e-8 |
| Pure/mixed tensor product | 6x6, trace 1 | exact dimensions and normalization |
| Invalid density `diag(1.1,-0.1)` | negative eigenvalue detected | reject before native dispatch |
| Degenerate dephasing-only stationary problem | Liouvillian nullity 2; native steady-state raises ValueError | explicitly refuse nonunique steady state |
| Dense 256-level tridiagonal spectrum | 2.4425e-15 absolute against analytic spectrum | <1e-12 |
| Sparse 64-level Liouvillian | 4096x4096; CSR data/index bytes 177668 | finite construction under limits |

Density positivity tests use `numpy.linalg.eigvalsh` on fixed tiny matrices. They establish semantics, **not** an API dependency choice or an unbounded validation allowance. Choose and bound API validation separately in TASK-006/007.

QuTiP `mesolve` with a ket and no collapse operators takes its Schrödinger fast path and returns a ket. The service must convert a ket to a density matrix before calling the native ME solver. For ME propagators, explicitly construct a Liouvillian even with no collapse operators. Neither behavior can be inferred from the operation name alone.

The degenerate stationary fixture fails natively, but not every nonunique problem is guaranteed to fail identically. Detect the nullspace multiplicity on the admitted small static problem; do not accept an arbitrary returned stationary state. The tolerance must scale with operator magnitude; this fixed fixture's 1e-12 singular-value threshold is not a universal service rule.

## Pulses, grid policy and limits

A square drive occupies 40..42 ns while requested output times are only `[0,100]`. Coefficient grids include exact support endpoints and 0.02/0.01 ns interior spacing, respectively 103/203 total knots. Endpoints are zero and QuTiP uses `order=1`, so these are explicitly resolved trapezoidal approximations to a discontinuous ideal square.

Final measurement error against the exact integrated interpolated pulse is **5.0660e-10 / 4.0601e-11**. Error against the ideal square is **2.4672e-4 / 6.1684e-5**, improving approximately fourfold. This is a measurement error near a population maximum; it does **not** establish a universal 1e-4 state error for every drive. A grid policy needs an operator-norm/state convergence criterion, rather than relying on this unusually insensitive probability alone.

The tests exposed two real hazards:

- A grid made with floating-point `arange` can leave an intended terminal endpoint slightly inside the support, followed by a long linear ramp to the next output time. Use exact declared endpoint values and interior `linspace`; explicitly zero outside support.
- With `max_step=0.005 ns` over a 100 ns output gap, default `nsteps=10000` raises native `IntegratorException: Excess work done`. The discovery probe uses `nsteps=100000`. This is a solver work limit, not a wall-clock/job-duration cap.

Candidate contract for TASK-006, **not an implemented public contract**:

1. Union output times, declared support edges and supplied sample knots. Resolve each smooth analytic feature with at least 200 subintervals and each modulation period with at least 32. Double the feature grid and check convergence on the required state/observable norm. Reject an unresolved input rather than silently coarsening it.
2. Bound the combined coefficient grid to **65536 knots** and simultaneous scalar coefficient channels to **16** before constructing arrays. Complex coefficient values alone occupy at most `16*65536*16 = 16777216` bytes. Include the independent time vector, coefficient conjugates and interpolation copies in allocation accounting.
3. For analytic shapes with a bounded second derivative, linear-interpolation error is bounded by `h^2*max(abs(f''))/8`. Combine this with Hamiltonian operator norms, 2π GHz conversion and interval duration to select spacing for a state-error budget. Discontinuous square edges need explicit transition-width/area-error handling or segmentation; the smooth-envelope bound cannot justify them.
4. Maximum integration step must resolve the smallest feature, independently of output spacing. A very long empty interval should be segmented at support edges rather than forcing tiny steps across the entire duration. Preserve the native state between segments inside this one job; this is not cross-request state.
5. `nsteps` bounds internal integration steps per advance; it does **not** expose an audited bound on coefficient/RHS callback evaluations. The fixed array backend provides no callback counter. A fixed universal callback ceiling is therefore **not established** by this probe. Before freezing a worst-case policy, pin the chosen integrator's evaluation bound or use a separately reviewed bounded integration strategy. Do not claim 100000 steps means 100000 RHS evaluations, add a simulation wall-time cap, or silently drop limits.

**DRAG and positive square rise remain deferred discovery variants.** Their sign/units/edge definitions were proposals, not established upstream semantics. Keep them unadvertised until TASK-006 records the owner's choice and dedicated convergence/analytic tests. Constant, sampled and resolved plain square coefficients are tested here; Gaussian/flattop still need implementation-level convergence checks before admission.

## Allocation and output refusal boundaries

Useful conservative preallocation accounting is operation-specific:

- A dense complex Hilbert matrix is `16*D^2` bytes, a dense Liouvillian or ME propagator is `16*D^4` bytes, a retained density series is `16*T*D^2` bytes, and a ket series is `16*T*D` bytes. Count concurrent matrices, eigensolver workspaces, array copies, sparse conversions, coefficients and serialization buffers, not just final output dimensions.
- Candidate workspace accounting multiplier: reserve **8 dense arrays** (`128*D^4` for dense ME, `128*D^2` for dense Hilbert operations), plus explicit states/coefficient/output buffers and a **512 MiB native/import headroom**. Refuse a computed total above **2 GiB** under the 3 GiB address-space limit. This leaves additional virtual-address slack; it is a conservative proposal, not a proof for every native algorithm.
- Example: dense ME at D=64 uses 2 GiB for that workspace reservation alone and is refused once headroom is included; D=32 reserves 128 MiB before other buffers. The tested sparse D=64 object is much smaller, but densifying it changes the accounting by orders of magnitude. A successful sparse construction does not authorize dense D=64 propagation.
- Dense D=256 input in this run is 1048576 bytes and the tested sparse allocation is 177668 bytes. These representative passes do not establish a maximum safe dimension or worst-case solver RSS. Benchmark the selected native algorithms at their proposed boundary before runtime admission.
- Keep the planned raw-array result budget at **6 MiB**, count dtype/shape products before allocating, then measure actual NPZ and JSON/base64 sizes. Existing limits still apply: child file 12 MiB and callback artifact base64 12 MiB. Compressibility cannot justify admission.

A separate stdlib process in the same CPU/memory container and child resource profile verified both refusal mechanisms: allocating a 3 GiB `bytearray` raises `MemoryError`; writing 12 MiB+1 writes only 12582912 bytes, and a further write raises `OSError` errno 27 (`EFBIG`). No limit was increased. These are hard-boundary checks, not a claim that every native OOM failure will be graceful.

## Verification status and handoff

- Baseline: original nine fixed native cases passed unchanged.
- New signal: between-output pulse/convergence, complex conjugation, pure/mixed density semantics, positivity, tensor density, stationary degeneracy, repeat seed, dense analytic spectrum and sparse construction pass in the constrained runtime.
- Corrected discovery-harness failures: low `nsteps` exhaustion; native ket fast path; floating endpoint causing a false long pulse tail. All are documented above and final verification passes.
- Regressions: none observed in the original probes. No production files or current service tests changed by this task.
- Remaining contract work: final universal pulse accuracy and callback-work bound, operation-specific maximum allocations validated at selected limits, density validator choice and DRAG/rise decision. These are explicit TASK-006 inputs; this report does not waive them or assert production readiness.
- GPU handoff: the fixed CPU Rabi, explicit ME propagator, dense spectrum and seeded MC expectations provide reproducible reference cases. GPU solver coverage, precision, transfer/context cost and resource-limit compatibility are separate TASK-005 measurements.

Changed files: `probe_lindblad.py` adds fixed adversarial cases and an opt-in child-limit mode; `discovery/lindblad.md` records actual evidence and remaining boundaries. No assertions were weakened; new checks add missing approved numerical coverage.
