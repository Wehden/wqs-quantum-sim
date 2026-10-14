import pytest
from qsim.aer_adapter import _prepare
from test_aer_import import request


@pytest.mark.parametrize('body,fmt', [
    ('qreg q[999999999999]; creg c[1]; measure q[0]->c[0];', 'qasm2'),
    ('qreg q[1]; creg c[999999999999]; measure q[0]->c[0];', 'qasm2'),
    ('gate bad a { x a; } qreg q[1]; creg c[1]; bad q; measure q->c;', 'qasm2'),
    ('qreg q[1]; creg c[1]; if(c==2) x q; measure q->c;', 'qasm2'),
    ('qubit q; bit c; if(c==true) { if(c==false) { x q; } } c=measure q;', 'qasm3'),
    ('qubit q; bit c; for int i in [0:999999999] { x q; } c=measure q;', 'qasm3'),
    ('input float theta; qubit q; bit c; rx(theta) q; c=measure q;', 'qasm3'),
    ('qubit q; bit c; c=true; c=measure q;', 'qasm3'),
    ('qubit q; bit c; if(c==1) { x q; } c=measure q;', 'qasm3'),
    ('qubit q; bit c; let a=q; c=measure q;', 'qasm3'),
    ('qubit q; bit c; @example\nx q; c=measure q;', 'qasm3'),
    ('qreg q[1]; creg c[1]; rx(2^100) q; measure q->c;', 'qasm2'),
    ('qreg q[1]; creg c[1]; rx(1e999) q; measure q->c;', 'qasm2'),
    ('qreg q[' + '9'*100 + ']; creg c[1]; measure q[0]->c[0];', 'qasm2'),
    ('qreg q[1]; creg c[1]; rx(' + '('*33 + '1' + ')'*33 + ') q; measure q->c;', 'qasm2'),
    ('include "/tmp/private.inc"; qreg q[1]; creg c[1]; measure q->c;', 'qasm2'),
    ('include "https://example.com/x"; qubit q; bit c; c=measure q;', 'qasm3'),
])
def test_rejects_before_native_conversion(body, fmt, monkeypatch, capsys):
    import qiskit.qasm2
    import qiskit_qasm3_import
    def forbidden(*args, **kwargs):
        pytest.fail('native conversion must not run')
    monkeypatch.setattr(qiskit.qasm2, 'loads', forbidden)
    monkeypatch.setattr(qiskit_qasm3_import, 'convert', forbidden)
    with pytest.raises(ValueError):
        _prepare(request(body, fmt))
    assert not capsys.readouterr().err


def test_nonfinite_import_fails_without_stderr(capsys):
    with pytest.raises(ValueError):
        _prepare(request('qreg q[1]; creg c[1]; rx(1/0) q; measure q->c;'))
    assert not capsys.readouterr().err


def test_metadata_import_does_not_load_native_dependencies():
    import subprocess
    import sys
    subprocess.run([sys.executable, '-c', 'import sys; import qsim.aer_adapter; '
                    'assert not any(n in sys.modules for n in ("qiskit", "qiskit_aer", "openqasm3"))'], check=True)
