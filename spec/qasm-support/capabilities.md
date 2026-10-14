# Capability evidence

Status: TASK-001 local discovery, 2026-10-13. These results establish pinned-library behavior, not deployed service support. No dependency, production, or deployment changes were made for this report. The first implementation subset must apply the safeguards below before advertising a capability.

## Environment and licenses

Inspected the existing repository `.venv` using `importlib.metadata.distribution` and the installed package sources. The worker dependencies are pinned in `pyproject.toml` and `uv.lock`.

| Distribution | Installed version | Distribution license metadata |
| --- | --- | --- |
| clifft | 0.11.0 | Apache-2.0 |
| qsimcirq | 0.22.1 | Apache-2.0 |
| cirq-core | 1.7.0 | Apache-2.0 |
| ply | 3.11 | BSD |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |

Clifft, qsimcirq and Cirq distributions include `licenses/LICENSE`; NumPy includes its bundled-library license notices. PLY reports BSD in package metadata. This records the existing dependency footprint; it does not authorize adding a dependency or replace distribution notice obligations.

## Candidate matrix

The executable semantic fixtures are maintained in [test_qasm_capabilities.py](../../tests/test_qasm_capabilities.py). Parser acceptance alone is insufficient. This report's source/metadata probes supplement those fixtures.

| Construct | Cirq importer | Google qsim execution | First-subset disposition |
| --- | --- | --- | --- |
| Classical registers and terminal measurements | Accepted; register metadata retained | Verified by semantic fixtures | Eligible with final-memory mapping |
| Mid-circuit measurement | Accepted | Verified by semantic fixtures | Eligible with collapse tests |
| Reset, including entangled input | Accepted | Verified by semantic fixtures | Eligible with reset tests |
| Repeated writes | Repeated measurement keys retained | Native records retain separate events | Eligible; use last write for final memory |
| Classical conditions | Accepted as controlled operations | Rejected by qsim | Defer and reject explicitly |
| Custom gates | Accepted as nested CircuitOperation objects | Simple cases execute | Eligible only after approved expansion guards and boundary tests |
| QASM3 numeric inputs | Typed input metadata and symbolic expressions retained | See semantic fixtures for execution | Eligible only for tested declaration forms and strict binding validation |
| General QASM3 control flow | No broad support established | No broad support established | Reject outside tested subset |

Clifft's existing QASM probability path remains separate. Semantic probes show that classical-register/measurement circuits are rejected there; do not route new sampling to Clifft or change the default engine silently. Existing Stim sampling remains unchanged.

## Register and write mapping

A local probe parsed:

```qasm
OPENQASM 2.0;
include "qelib1.inc";
qreg z[12];
creg zed[3];
creg a[2];
x z[10];
measure z[10] -> a[1];
measure z[0] -> zed[2];
```

`QasmParser().parse(source)` returned `qregs == {'z': 12}` and `cregs == {'zed': 3, 'a': 2}` in declaration order. Measurement keys were `a_1` and `zed_2`. The importer may reorder independent operations into circuit moments; operation iteration order must not substitute for source register order. Generate the destination mapping from each declared name and numeric index, rather than splitting or sorting key strings. Initialize all declared classical columns to zero and overwrite measured columns with the final recorded event. The declaration list also preserves unused bits.

Cirq names quantum bits using the same `register_index` convention. The parsed circuit contains only referenced qubits, so probability evaluation needs an explicit declaration-order qubit list to retain idle qubits and avoid lexical `q_10` versus `q_2` ordering. This must not alter the legacy probability contract.

The pinned importer wraps only `next(p[5])` when constructing a conditional gate operation. Register-broadcast condition semantics therefore cannot be assumed preserved even in a different simulator. All conditions remain outside this subset.

## Includes and preallocation

The installed Cirq lexer recognizes builtin include tokens and the parser installs its predefined gates. This path contains no general include-file reader or network resolver. Probes with `/etc/passwd` and `https://example.invalid/gates.inc` were rejected with `QasmException` before import.

However, the lexer regex uses an unescaped dot: `include "qelib1Xinc";` and `include "stdgatesXinc";` are accepted as builtin tokens. No external content is read, but the service must enforce exact approved include names. The lexer preserves the original include token string, permitting an exact quoted-name check without building another parser. Comments and whitespace should follow the existing lexer rather than source-string keyword matching.

Register declarations store integer lengths without allocating every bit. Register-wide operations later construct lists in the parser. A post-parse size check is too late for maliciously large declarations followed by register-wide operations. Use the existing lexer tokens to bound declared quantum size and `shots * total_classical_bits` before parser construction; reject unsupported declaration shapes rather than guess. Keep existing source-size and isolated-child resource limits. This is structural admission validation, not a circuit interpreter or an active-job deadline.

## Custom gate expansion

The importer stores a custom definition as a `FrozenCircuit` and a call as a `CircuitOperation`; nested definitions form a compact graph. A direct self-reference was rejected as an unknown gate. Redefinition can reference the prior definition and still create exponential expansion, so recursion rejection alone is insufficient.

A local doubling probe defined `g0 t { x t; }`, then `gN t { g(N-1) t; g(N-1) t; }`. It parsed the following without decomposition:

| Definition depth | Source characters | Expanded primitive count | Distinct circuit objects traversed | Parse + count seconds, one local observation |
| --- | --- | --- | --- | --- |
| 10 | 336 | 1,024 | 12 | 0.0055 |
| 20 | 626 | 1,048,576 | 22 | 0.0050 |
| 30 | 916 | 1,073,741,824 | 32 | 0.0053 |

Counts used memoization by native circuit object identity. This demonstrates a possible bounded traversal before native decomposition, without introducing an intermediate representation. It does not establish a safe production expansion/depth budget. TASK-002 proposes a service policy of 65,536 expanded primitive operations and nesting depth 32. These are conservative admission choices, not measured engine maxima. Enabling custom gates requires boundary tests, accounting for repetitions and decomposition growth, and a traversal that rejects excessive depth before Python recursion exhaustion. The subset can defer gates if these safeguards are not demonstrated.

## Numeric binding probe

This source parsed with `input_params == {'theta': 'angle[64]', 'beta': 'float[32]'}`:

```qasm
OPENQASM 3.0;
include "stdgates.inc";
input angle[64] theta;
input float[32] beta;
qubit[1] q;
rx(theta+beta) q[0];
```

`cirq.resolve_parameters(parsed.circuit, {'theta': 1.0, 'beta': 2.0})` produced `Rx(rads=3.0)`. Only these maintained resolver semantics are proposed; no source substitution is needed. The API/adapter must reject missing/extra names, bool/string/null/nonfinite inputs, unsupported declared types, unresolved symbols and nonfinite resolved gate parameters. Width syntax is parser acceptance evidence, not evidence of enforced IEEE width-specific arithmetic. Advertise the service's approved numeric contract explicitly.

## Reproduction

The test executor ran the following command: **29 passed** (24 new capability cases and five existing engine cases), with one existing Starlette deprecation warning and no skips/xfails:

```sh
uv run --frozen --extra worker --extra controller pytest -q tests/test_qasm_capabilities.py tests/test_qsim_engine.py
```

For metadata/license reproduction:

```python
from importlib.metadata import distribution
from cirq.contrib.qasm_import._parser import QasmParser
for name in ('clifft', 'qsimcirq', 'cirq-core', 'ply', 'numpy'):
    item = distribution(name)
    print(name, item.version,
          item.metadata.get('License-Expression') or item.metadata.get('License'))
parsed = QasmParser().parse(source)  # use the complete fixture above
print(parsed.qregs, parsed.cregs, parsed.input_params)
print(list(parsed.circuit.all_operations()))
```

The compact graph probe used this code (discovery only; production traversal additionally needs explicit depth/count checks and repetition accounting):

```python
import cirq
from time import perf_counter
for depth in (10, 20, 30):
    source = 'OPENQASM 2.0; include "qelib1.inc"; gate g0 t { x t; } '
    source += ''.join(f'gate g{i} t {{ g{i-1} t; g{i-1} t; }} '
                      for i in range(1, depth + 1))
    source += f'qreg q[1]; g{depth} q[0];'
    start = perf_counter()
    parsed = QasmParser().parse(source)
    cache = {}
    def count(circuit):
        if id(circuit) not in cache:
            children = [count(op.circuit) if isinstance(op, cirq.CircuitOperation)
                        else (1, 0) for op in circuit.all_operations()]
            cache[id(circuit)] = (sum(n for n, _ in children),
                                  1 + max((d for _, d in children), default=0))
        return cache[id(circuit)]
    print(depth, len(source), count(parsed.circuit), len(cache),
          perf_counter() - start)
```

No decomposition or simulator call occurs in this probe. Qubit and parameter remapping remain native `CircuitOperation` responsibilities; the semantic fixtures execute a nested parameterized gate. Timing observations are not a throughput benchmark.

## Remaining gates

- TASK-002 selects the exact first-subset contract and records deferred capabilities.
- Structural guards, exact include checks, safe errors and final-memory reconstruction need production tests before admission is enabled.
- No fixture here proves worker-image packaging, API persistence, authentication, deployment compatibility or production performance; those retain their dedicated task gates.
