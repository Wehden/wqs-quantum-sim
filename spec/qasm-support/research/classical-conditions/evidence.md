# Classical-condition investigation

Date: 2026-10-14. Scope: public upstream source/docs and small local CPU probes. No service submissions, SkyPilot calls, production checks, application edits or project dependency changes.

## Reproduce

Use Python 3.12 in an isolated environment with [requirements-research.txt](requirements-research.txt), then run [probe_aer.py](probe_aer.py). Run [probe_existing.py](probe_existing.py) in the existing frozen project environment. Outputs are [aer-results.json](aer-results.json) and [existing-results.json](existing-results.json).

```sh
uv venv /tmp/wqs-conditions-research --python 3.12
uv pip install --python /tmp/wqs-conditions-research/bin/python -r spec/qasm-support/research/classical-conditions/requirements-research.txt
/tmp/wqs-conditions-research/bin/python spec/qasm-support/research/classical-conditions/probe_aer.py
.venv/bin/python spec/qasm-support/research/classical-conditions/probe_existing.py
```

Candidate versions: Qiskit 2.5.2, Aer 0.17.2, QASM3 importer 0.6.0, openqasm3 1.0.1, ANTLR runtime 4.13.2. The first four report Apache licenses; ANTLR reports BSD. Full transitive license/security and combined worker compatibility review remains an implementation gate. The local lock has 14 packages and is not the application lock.

## Results

Twelve Aer cases passed through maintained import, transpilation and CPU statevector execution: QASM2 true/false branches, unwritten zero memory, partially written register integer order, latest-write conditions, broadcast gates, a custom gate; QASM3 if/else, conditional measurement retaining previous memory, conditional reset, register equality; and QASM2 teleportation of |+> with correction and X-basis verification. Each used 4,096 shots and seed 42. Exact expected output sets and a 0.05 frequency tolerance were fixed in the probe before execution. The custom-gate probe establishes feasibility but does not establish safe expansion; the proposed initial Aer subset therefore defers it.

The pinned Cirq 1.7.0 importer plus Cirq simulator executes a scalar measured condition. An unwritten key raises `ValueError`; QASM classical bits should instead start at zero. A register-wide conditional flips only the first qubit. Inspection confirms `p_if` uses `next(p[5])`, dropping the remaining generated operations. qsimcirq 0.22.1 rejects the classically controlled operation in all three cases. These are upstream-only probes; the service already rejects controls, so they do not demonstrate a live incorrect-result incident.

Additional exploratory checks: Aer accepted seeds 0, 2**32 and 2**63-1 and repeated each 128-shot run identically. This does not prove high-bit discrimination; add that regression before admission. Both importer paths rejected a nonstandard absolute include. This is not an exhaustive filesystem/network confinement test. QASM3 `bit == 1` failed while `bit == true` passed, consistent with the importer's documented restricted condition forms.

## Sources and limits

- [Cirq classical controls](https://quantumai.google/cirq/build/classical_control): maintained native operations and simulator examples. Local probes, not these examples, establish the pinned import-path problems.
- [Pinned Cirq parser](https://github.com/quantumlib/Cirq/blob/v1.7.0/cirq-core/cirq/contrib/qasm_import/_parser.py): `p_if` and per-bit condition construction.
- [qsim upstream translation at inspected revision](https://github.com/quantumlib/qsim/blob/84a0d4e54614d13084daa9729abfddb0f697c771/qsimcirq/qsim_circuit.py): main source inspected; it is not a tested upgrade. GitHub's latest-release API returned v0.22.0 despite the installed distribution being 0.22.1, so a release listing alone cannot establish a suitable upgrade.
- [Clifft 0.12 QASM2 parser](https://github.com/unitaryfoundation/clifft/blob/v0.12.0/src/clifft/circuit/qasm2_parser.cc): rejects `creg`, `measure`, `reset` and `if`. [Release notes](https://github.com/unitaryfoundation/clifft/releases/tag/v0.12.0) describe removal of an unused permissive Qiskit converter. Neither establishes a maintained conditional QASM execution adapter. No Clifft upgrade was installed or tested.
- [Aer backend options](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.AerSimulator.html): explicit method, CPU device, precision and resource controls. Installed 0.17.2 execution was tested locally; the rendered reference identifies 0.17.1.
- [Qiskit QASM3 importer 0.6.0](https://qiskit.github.io/qiskit-qasm3-import/): maintained but expressly limited conversion, including bit/boolean and register/integer equality. Inspecting `visit_Include` confirms it accepts only `stdgates.inc`. It is not unrestricted OpenQASM3 support.
- [IBM dynamic circuits](https://quantum.cloud.ibm.com/docs/en/guides/classical-feedforward-and-control-flow): semantics reference only. No hardware execution or provider account was used.

This evidence supports an explicit Aer design proposal. It does not validate service integration, hostile-input protection, project dependency coexistence, worker packaging, production timing or rollout readiness.
