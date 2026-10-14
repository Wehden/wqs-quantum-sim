import pytest
from qsim.aer_adapter import _prepare, _preflight

Q2 = 'OPENQASM 2.0; include "qelib1.inc"; '
Q3 = 'OPENQASM 3.0; include "stdgates.inc"; '


def request(body, fmt='qasm2', shots=32):
    return {'engine': 'aer', 'input_format': fmt, 'operation': 'sample',
            'circuit': (Q2 if fmt == 'qasm2' else Q3) + body, 'shots': shots, 'seed': 42}


@pytest.mark.parametrize('body,fmt', [
    ('qreg q[2]; creg c[2]; x q[0]; measure q->c; if(c==1) x q;', 'qasm2'),
    ('qubit[2] q; bit flag; bit[2] mem; flag=measure q[0]; if(flag==true) { x q[1]; } else { reset q[1]; } mem=measure q;', 'qasm3'),
])
def test_native_register_mapping(body, fmt):
    circuit, metadata = _prepare(request(body, fmt))
    assert circuit.num_clbits == metadata['classical_bits']
    assert metadata['registers'][0]['offset'] == 0


def test_register_budget_boundary():
    base = 'qreg q[1]; creg c[{n}]; measure q[0]->c[0];'
    _prepare(request(base.format(n=999), shots=1000))
    with pytest.raises(ValueError, match='sample cells'):
        _prepare(request(base.format(n=1000), shots=1000))


def test_quantum_bound():
    _preflight(request('qreg q[26]; creg c[1]; measure q[0]->c[0];'))
    with pytest.raises(ValueError, match='register allocation'):
        _preflight(request('qreg q[27]; creg c[1]; measure q[0]->c[0];'))


def test_branches_count_all_measurements():
    body = 'qubit q; bit c; if(c==true) { c=measure q; } else { c=measure q; }'
    _preflight(request(body, 'qasm3', 10000))
    # Repeated branch measurements must count even though only one branch runs.
    body = 'qubit q; bit[998] c; if(c==0) { c[0]=measure q; } else { c[0]=measure q; c[0]=measure q; }'
    with pytest.raises(ValueError, match='sample cells'):
        _preflight(request(body, 'qasm3', 1000))
