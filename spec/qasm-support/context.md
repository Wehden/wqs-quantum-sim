# Context: broader circuit support

Status: draft for owner review.

## Objective

Enable familiar quantum circuits using established OpenQASM, Qiskit and Cirq tooling through the existing wqsim service. Cover the current exception list: OpenQASM 3, classical registers, measurement, reset, conditional operations, custom gate declarations and unresolved parameter handling.

The owner explicitly requests reuse of established implementations, including those used by IBM Quantum. This is an integration effort. Existing provider support establishes useful semantics and examples, but does not establish execution support in our pinned engines.

## Baseline inspected

Application baseline: `0e850ec8e7b108d26f7f86485f1386df5bb481a0`.

- `qsim/models.py` and `qsim/api.py`: QASM2 requests expose basis probabilities; the request requires bitstrings. Stim exposes sampling. Request models reject unknown fields.
- `qsim/worker.py`: Clifft 0.11.0 is the default engine. Google qsimcirq 0.22.1 uses Cirq import and amplitude computation. Its adapter rejects classical registers, measurement, reset, conditions and gate declarations before import. Service restrictions must be distinguished from upstream limitations.
- `qsim/store.py`, `qsim/controller.py`: durable jobs, owner-scoped idempotency, SkyPilot dispatch and warm-worker reuse. Clients own sequences, batches, optimizers and state between requests.
- `qsim/streams.py`: resumable status and final-result events. Completed sample arrays are not incremental measurement streaming.
- `qsim/settings.py`, `qsim/worker.py`, `deploy/sky-config.yaml`: active simulations have no wall/CPU-time or worker-lifetime deadline. Memory, output and input protections remain; explicit cancellation and idle cleanup remain.
- Existing result delivery uses JSON summaries and NPZ artifacts. Existing bit-order behavior is a compatibility constraint.

## Scope

Use maintained parsers, library circuit objects, binding/decomposition facilities and engine adapters where available. Add only the service integration needed to validate requests, select a supported engine operation and preserve results through durable jobs.

Consider Qiskit QASM2/QASM3 import/export, the existing Cirq/qsim path, and Clifft companion integrations. Verify availability, licenses and supported operations at pinned versions before selecting them. Any version upgrade needs its own justified compatibility evidence.

Use local reference simulators and analytic fixtures for correctness. Hardware credentials and paid hardware runs are not required to establish acceptance.

## Exclusions

No new circuit language, general compiler IR, handwritten classical execution engine, arbitrary Python submission, silent backend substitution, hardware-provider integration, server-managed batching, persistent quantum state, or optimizer service. No simulation TTL or fixed concurrency cap is introduced.

Not every engine must support every feature. The service must make tested capabilities and unsupported combinations explicit.

## Questions for the design stage

1. Which restrictions are only in wqsim validation, and which arise in importers or engines?
2. Which maintained adapters preserve measurement/control dependencies and register mapping without custom execution code?
3. Can one existing parser serve both engines without making a larger dependency or maintenance commitment than separate adapters?
4. How should an additive sampling request fit the existing API while preserving current probability clients?
5. Which library-native representation preserves final classical memory and repeated measurement records?
6. Which standard parameter forms are supported? Do not invent free global parameters in OpenQASM 2.
7. What OpenQASM 3 subset can be exposed immediately through selected adapters, independent of later features?
8. Can either engine supply incremental samples with stable retry and seed semantics?

## Sources to verify during design

- [Qiskit QASM2 APIs](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qasm2)
- [Qiskit QASM3 APIs](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qasm3)
- [qsimcirq integration guide](https://quantumai.google/qsim/tutorials/qsimcirq)
- [Clifft guide and companion integration entry points](https://unitaryfoundation.github.io/clifft/stable/guide/)

These live references are discovery inputs, not evidence that a capability exists in the installed versions. The design artifact must record tested versions and circuits.
