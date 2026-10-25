# Discovery evidence

Status: local library probes, 2026-10-25. These are feasibility checks, not WQS API/worker support or production acceptance.

## Sources and baseline

- WQS main: `d941d3f5c3fc3360b92477ff4df681d83cc869f7`; PR #35 is merged. Existing rollout and backup acceptance remain separate operational work.
- FluxxedUp: `ae0cea45d244f9a6ee3204516fe6aef44340a34f`. The inspected tree contains documentation and project tooling, not an executable simulation backend. Its cluster documents propose Lindblad, scqubits and JoSIM; no upstream application source is vendored.
- [QuTiP releases](https://qutip.org/download.html), [solver documentation](https://qutip.readthedocs.io/en/qutip-5.3.x/apidoc/solver.html), and [upstream project metadata](https://github.com/qutip/qutip/blob/master/pyproject.toml). QuTiP uses BSD-3-Clause. Packaging must retain required distribution notices.

## Reproduction

Run the fixed discovery script in a disposable environment:

```sh
uv venv --python 3.12 /tmp/wqs-physics-probe-env
uv pip install --python /tmp/wqs-physics-probe-env/bin/python \
  qutip==5.3.1 numpy==2.5.4 scipy==1.18.1
/tmp/wqs-physics-probe-env/bin/python spec/physics-engines/probe_lindblad.py
```

Observed Python 3.12.13, QuTiP 5.3.1, NumPy 2.5.4 and SciPy 1.18.1. No production settings, credentials, queues, worker images or services are used. Missing matplotlib produces an expected graphics warning; these numerical probes do not use graphics.

| Probe | Observed error | Acceptance |
| --- | --- | --- |
| Resonant Rabi population | 7.49e-9 max absolute | ≤1e-4 |
| Two-level T1 relaxation | 1.58e-8 max relative | ≤1e-4 |
| Analytic unitary propagator | 2.40e-8 matrix norm | ≤1e-6 |
| Explicit Liouvillian propagator vs unitary superoperator | 9.66e-8 matrix norm | ≤1e-6, native superoperator shape verified |
| Numeric array coefficient Rabi | 7.49e-9 max absolute | ≤1e-4 |
| Coherent resonator ringdown | 8.18e-10 max relative | ≤1e-3 |
| Thermal steady-state occupation | 6.00e-13 absolute | ≤1e-6 |
| Dispersive shift vs perturbative expression | 1.37% relative | ≤5% |
| T1 Monte Carlo, 1,000 serial trajectories, seed 42 | 2.26 maximum analytic standard errors | ≤3 at the 20 noninitial time points |

One complete probe run took approximately 0.19 seconds after imports, with process peak RSS approximately 101 MiB. This was an unconstrained local process. It does not demonstrate worst-case behavior, startup cost, execution on the 2 CPU/4 GiB worker, or any API timing guarantee. Version and numerical convergence tests remain necessary in the pinned worker image.

QuTiP accepts numeric array coefficients and preserves complex conjugation without user strings. Passing an explicit Liouvillian yields a superoperator even with no collapse operators, avoiding an ambiguous `me` propagator return type.

## WQS integration constraints verified from source

- The durable job record already stores arbitrary normalized request JSON. New physics inputs need validation/worker dispatch changes, rather than a new queue or database-backed session.
- `canonical_request` currently normalizes legacy defaults. Preserve those exact circuit identities when adding model-based requests.
- Capabilities currently expose an engine-name mapping. Changing it to the proposed list would break clients.
- The worker already uses a child process without callback credentials for native simulation; preserve that boundary.
- The worker image currently packages several pinned engines. No separate per-engine image-selection infrastructure exists.
- The artifact field is limited to 12 MiB of base64. That bounds raw NPZ to 9 MiB. The HTTP limit is 16 MiB and result summaries are capped at 2 MiB. Ten million complex128 values require about 153 MiB before ZIP/base64 overhead, so the proposed output limit is incompatible with current transport.
- The existing release acceptance contract names all supported circuit cases explicitly. Physics support needs an intentional acceptance-contract extension.
- Native recovery validates numeric/Unicode NPZ members and rejects object arrays. Physics metadata must use Unicode rather than object dictionaries.

## Remaining discovery gates

No service support is claimed until strict request validation, density-state checks, pulse-grid accuracy, dimension/memory admission, serialization bounds, seeded reproducibility, full request lifecycle, constrained worker tests, and release acceptance pass. scqubits and JoSIM have not been installed or benchmarked in this investigation.

## Confirmed scope and GPU discovery

On 2026-10-25 the owner confirmed all three backends are in scope and explicitly included addressable GPUs where useful. That changes the earlier Lindblad-first-only scope proposal and overrides the upstream GPU exclusion. It does not claim that implementation, numerical limits or deployment have already passed their gates.

Dependency candidates established from primary upstream metadata:

| Component | Candidate / license | Evidence status |
| --- | --- | --- |
| QuTiP | 5.3.1 / BSD-3-Clause | Local numerical probes passed |
| scqubits | 4.3.1 / BSD-3-Clause | [Package metadata](https://pypi.org/project/scqubits/); installation and native adapter probes pending |
| JoSIM | v2.7 / MIT | [Upstream releases](https://github.com/JoeyDelp/JoSIM/releases), [license](https://github.com/JoeyDelp/JoSIM/blob/master/LICENSE); proposed 2.6.x vs current 2.7 choice must be verified and pinned |

[scqubits' diagonalization documentation](https://scqubits.readthedocs.io/en/latest/guide/settings/ipynb/custom_diagonalization.html) documents native CuPy/JAX GPU methods. These can be selected by a fixed server policy without exposing native callbacks or arbitrary method options to callers. [QuTiP's CuPy backend](https://github.com/qutip/qutip-cupy) provides a GPU candidate; solver/operation compatibility and useful end-to-end acceleration remain to be measured. No supported GPU path was established for JoSIM in this investigation.

A read-only Kubernetes inventory confirms that the cluster advertises NVIDIA GPU resources. This is a capacity snapshot, not a reservation, current free capacity, a successful allocation or a performance result. No GPU jobs, worker profiles or cluster configuration were changed. Required discovery includes actual resource-constrained CPU/GPU comparisons with precision and cold/warm latency accounting before an acceleration capability is published.
