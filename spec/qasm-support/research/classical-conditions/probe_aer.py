"""Standalone upstream investigation; not a service adapter or CI test.

Run with the pinned isolated environment in evidence.md. No service calls.
"""
import json
from importlib.metadata import version
from qiskit import qasm2, qasm3, transpile
from qiskit_aer import AerSimulator

Q2 = 'OPENQASM 2.0; include "qelib1.inc"; '
Q3 = 'OPENQASM 3.0; include "stdgates.inc"; '
CASES = [
 ('q2_true', 2, 'qreg q[2]; creg c[1]; creg o[1]; x q[0]; measure q[0]->c[0]; if(c==1) x q[1]; measure q[1]->o[0];', {3}),
 ('q2_false', 2, 'qreg q[2]; creg c[1]; creg o[1]; x q[0]; measure q[0]->c[0]; if(c==0) x q[1]; measure q[1]->o[0];', {1}),
 ('q2_unwritten_zero', 2, 'qreg q[1]; creg c[2]; creg o[1]; if(c==0) x q[0]; measure q[0]->o[0];', {4}),
 ('q2_partial_register_little_endian', 2, 'qreg q[2]; creg c[2]; creg o[1]; x q[0]; measure q[0]->c[1]; if(c==2) x q[1]; measure q[1]->o[0];', {6}),
 ('q2_latest_write', 2, 'qreg q[2]; creg c[1]; creg o[1]; x q[0]; measure q[0]->c[0]; x q[0]; measure q[0]->c[0]; if(c==0) x q[1]; measure q[1]->o[0];', {2}),
 ('q2_broadcast', 2, 'qreg q[2]; creg c[1]; creg o[2]; if(c==0) x q; measure q->o;', {6}),
 ('q2_custom_gate', 2, 'gate flip a { x a; } qreg q[1]; creg c[1]; creg o[1]; if(c==0) flip q[0]; measure q[0]->o[0];', {2}),
 ('q3_if_else', 3, 'qubit[2] q; bit c; bit o; h q[0]; c=measure q[0]; if(c==true) { x q[1]; } else { z q[1]; } o=measure q[1];', {0,3}),
 ('q3_conditional_measure_last_write', 3, 'qubit[2] q; bit c; bit o; x q[1]; o=measure q[1]; h q[0]; c=measure q[0]; reset q[1]; if(c==true) { o=measure q[1]; }', {1,2}),
 ('q3_conditional_reset', 3, 'qubit[2] q; bit c; bit o; x q[1]; h q[0]; c=measure q[0]; if(c==true) { reset q[1]; } o=measure q[1];', {1,2}),
 ('q3_register_equality', 3, 'qubit[2] q; bit[2] c; bit o; x q[0]; c[1]=measure q[0]; if(c==2) { x q[1]; } o=measure q[1];', {6}),
 ('q2_teleport_plus', 2, 'qreg q[3]; creg m[1]; creg a[1]; creg o[1]; h q[0]; h q[1]; cx q[1],q[2]; cx q[0],q[1]; h q[0]; measure q[0]->m[0]; measure q[1]->a[0]; if(a==1) x q[2]; if(m==1) z q[2]; h q[2]; measure q[2]->o[0];', {0,1,2,3}),
]
backend = AerSimulator(method='statevector', device='CPU', max_parallel_threads=1)
output = {'versions': {n: version(n) for n in ('qiskit','qiskit-aer','qiskit-qasm3-import','openqasm3','antlr4-python3-runtime')}, 'shots':4096,'seed':42,'cases':[]}
for name, language, body, expected in CASES:
    circuit = qasm2.loads(Q2+body, include_path=()) if language == 2 else qasm3.loads(Q3+body)
    compiled = transpile(circuit, backend, optimization_level=0, seed_transpiler=42)
    result = backend.run(compiled, shots=4096, seed_simulator=42, memory=True).result()
    assert result.success
    memory = result.data(0)['memory']
    values = [int(v,16) for v in memory]
    assert len(values)==4096 and set(values)==expected, (name,set(values),expected)
    counts = {str(v):values.count(v) for v in sorted(expected)}
    if len(expected)>1:
        assert all(abs(n/4096-1/len(expected))<=0.05 for n in counts.values()), counts
    output['cases'].append({'name':name,'passed':True,'counts':counts})
print(json.dumps(output,indent=2))
