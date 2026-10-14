# Aer implementation decisions

TASK-011, 2026-10-14. Design and task plan approved by owner. Source admission remains closed.

## Combined dependencies

The frozen project resolution adds exactly eight packages without changing any previously locked package version. Existing Clifft 0.11.0, qsimcirq 0.22.1 and Cirq 1.7.0 remain pinned. `uv pip check` reports compatibility. Candidate pins coexist in the project Python 3.12 environment.

| Added distribution | Version | Installed metadata license |
| --- | --- | --- |
| qiskit | 2.5.2 | Apache-2.0 |
| qiskit-aer | 0.17.2 | Apache-2.0 |
| qiskit-qasm3-import | 0.6.0 | Apache-2.0 |
| openqasm3 | 1.0.1 | Apache-2.0 |
| antlr4-python3-runtime | 4.13.2 | BSD |
| dill | 0.4.1 | BSD-3-Clause |
| rustworkx | 0.18.1 | Apache-2.0 |
| stevedore | 5.9.1 | Apache-2.0 |

No new credentials/provider SDK or hardware account is required. Distribution license files remain in the packaged environment. This inventory is not a security-vulnerability scan.

## CPU and image contract

Current controller code requests two CPUs and four GB; worker reuse compares the configured image. Select one native Aer thread, matching the existing restricted child's OMP/BLAS environment and fitting that allocation. Pass `cpu_threads=1` as a fixed internal worker argument, not a public field. No new configuration variable, resource request or controller policy is required. Regression tests will protect compatible-image selection.

TASK-014 must verify the actual Aer child under the existing 3 GiB address-space limit before admission. Dependency compatibility and a baseline image build alone do not prove memory-boundary execution, hostile-input safety or release readiness.
