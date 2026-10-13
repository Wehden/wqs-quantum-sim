"""Native importer boundaries before service admission is enabled."""
import subprocess
import sys

import numpy as np
import pytest
import qsimcirq

from qsim.qasm_adapter import final_memory, prepare

PREFIX = 'OPENQASM 2.0; include "qelib1.inc"; '


def request(circuit, **kwargs):
    return dict(engine='qsim', input_format='qasm2', operation='sample', shots=4,
                circuit=PREFIX + circuit, **kwargs)


def test_metadata_import_does_not_load_native_libraries():
    subprocess.run([sys.executable, '-c',
        'import sys; from qsim.qasm_adapter import SUPPORTED_COMBINATIONS; '
        'assert not SUPPORTED_COMBINATIONS; '
        'assert not ({"cirq", "numpy", "qsimcirq", "clifft"} & sys.modules.keys())'], check=True)


def test_final_memory_register_order_and_repeated_write():
    circuit, metadata = prepare(request('qreg q[1]; creg z[12]; creg a[1]; '
        'x q[0]; measure q[0] -> z[10]; measure q[0] -> a[0]; '
        'x q[0]; measure q[0] -> a[0];'))
    result = qsimcirq.QSimSimulator(seed=42).run(circuit, repetitions=4)
    memory = final_memory(result, metadata, 4)
    assert metadata['registers'] == [dict(name='z', size=12, offset=0), dict(name='a', size=1, offset=12)]
    assert memory.dtype == np.uint8
    assert memory.shape == (4, 13)
    assert np.all(memory[:, 10] == 1)
    assert np.sum(memory) == 4


@pytest.mark.parametrize('source,category', [
    ('qreg q[100000000]; h q;', 'resource_limit'),
    ('qreg q[1]; creg c[100000000]; measure q[0] -> c[0];', 'resource_limit'),
    ('qreg q[1]; creg c[1]; if(c==0) x q[0];', 'unsupported_feature'),
    ('qreg q[1]; rx(2^3) q[0];', 'unsupported_feature'),
    ('qreg q[1]; rx(1e999) q[0];', 'invalid_circuit'),
])
def test_preparse_guards(source, category):
    with pytest.raises(ValueError, match='^' + category):
        prepare(request(source))


@pytest.mark.parametrize('include', ['qelib1Xinc', '/etc/passwd', 'https://example.invalid/gates.inc'])
def test_include_confinement(include):
    payload = request('qreg q[1];')
    payload['circuit'] = f'OPENQASM 2.0; include "{include}"; qreg q[1];'
    with pytest.raises(ValueError, match='^(invalid_circuit|unsupported_feature)'):
        prepare(payload)


def test_nonunitary_probability_rejected():
    payload = request('qreg q[1]; creg c[1]; measure q[0] -> c[0];')
    payload['operation'] = 'basis_probabilities'
    with pytest.raises(ValueError, match='unsupported_feature'):
        prepare(payload)


def test_record_budget_counts_repeated_events():
    payload = request('qreg q[1]; creg c[1]; ' + 'measure q[0] -> c[0]; ' * 101)
    payload['shots'] = 10000
    with pytest.raises(ValueError, match='resource_limit'):
        prepare(payload)


def test_unknown_and_incomplete_records_rejected():
    from types import SimpleNamespace
    _, metadata = prepare(request('qreg q[1]; creg c[1]; measure q[0] -> c[0];'))
    for records in ({}, {'bad': np.zeros((4, 1, 1))}, {'c_0': np.zeros((3, 1, 1))}):
        with pytest.raises(ValueError, match='execution_failed'):
            final_memory(SimpleNamespace(records=records), metadata, 4)


@pytest.mark.parametrize('depth,allowed', [(15, True), (16, True), (17, False)])
def test_custom_expansion_boundary(depth, allowed):
    source = 'gate g0 t { x t; } ' + ''.join(
        f'gate g{i} t {{ g{i-1} t; g{i-1} t; }} ' for i in range(1, depth + 1))
    payload = request(source + f'qreg q[1]; creg c[1]; g{depth} q[0]; measure q[0] -> c[0];')
    # Measurement itself consumes a primitive operation from the same budget.
    if depth == 16:
        allowed = False
    if allowed:
        prepare(payload)
    else:
        with pytest.raises(ValueError, match='resource_limit'):
            prepare(payload)


def test_exact_expansion_limit_for_unitary_circuit():
    source = 'gate g0 t { x t; } ' + ''.join(
        f'gate g{i} t {{ g{i-1} t; g{i-1} t; }} ' for i in range(1, 17))
    payload = request(source + 'qreg q[1]; g16 q[0];')
    payload['operation'] = 'basis_probabilities'
    prepare(payload)


@pytest.mark.parametrize('depth,allowed', [(32, True), (33, False)])
def test_custom_nesting_boundary(depth, allowed):
    source = 'gate g0 t { x t; } ' + ''.join(
        f'gate g{i} t {{ g{i-1} t; }} ' for i in range(1, depth))
    payload = request(source + f'qreg q[1]; creg c[1]; g{depth-1} q[0]; measure q -> c;')
    if allowed:
        prepare(payload)
    else:
        with pytest.raises(ValueError, match='resource_limit'):
            prepare(payload)


def test_parameterized_custom_gate_is_not_yet_enabled():
    with pytest.raises(ValueError, match='invalid_binding'):
        prepare(request('gate turn(a) t { rx(a) t; } qreg q[1]; creg c[1]; '
                        'turn(pi) q[0]; measure q -> c;'))


def test_custom_gate_cannot_hide_measurement():
    with pytest.raises(ValueError, match='invalid_circuit'):
        prepare(request('gate f t { measure t; } qreg q[1]; creg c[1]; f q[0]; measure q -> c;'))


def test_remote_child_has_package_cwd_and_no_credentials(monkeypatch, capsys):
    import io
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from qsim import worker
    callbacks = []
    monkeypatch.setenv('QSIM_JOB_URL', 'http://example.invalid/internal/jobs/test')
    monkeypatch.setenv('QSIM_JOB_TOKEN', 'test-only')
    def response(request, **kwargs):
        if request.data:
            callbacks.append(json.loads(request.data))
            return io.BytesIO(b'{}')
        return io.BytesIO(b'{"input_format":"qasm2"}')
    def run(args, **kwargs):
        assert args[1:4] == ['-m', 'qsim.worker', 'simulate']
        assert kwargs['cwd'] == Path(worker.__file__).resolve().parent.parent
        assert set(kwargs['env']) == {'PATH', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS'}
        assert 'timeout' not in kwargs
        return SimpleNamespace(returncode=1, stderr=b'private circuit source')
    monkeypatch.setattr(worker.urllib.request, 'urlopen', response)
    monkeypatch.setattr(worker.subprocess, 'run', run)
    assert worker.remote() == 1
    assert callbacks[-1]['status'] == 'FAILED'
    assert 'private circuit source' not in capsys.readouterr().err
