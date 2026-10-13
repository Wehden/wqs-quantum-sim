"""Complete QASM sampling artifacts through the real isolated worker entry point."""
import base64
import io
import json
import os
import subprocess
import sys

import numpy as np
import pytest

PREFIX = 'OPENQASM 2.0; include "qelib1.inc"; '


def run_sample(tmp_path, suffix, shots=32):
    source, output = tmp_path/'input.json', tmp_path/'output.json'
    source.write_text(json.dumps(dict(engine='qsim', input_format='qasm2', operation='sample',
        circuit=PREFIX + suffix, shots=shots, seed=42)))
    process = subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
        env={'PATH': os.environ['PATH'], 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'},
        capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr.decode()
    assert not process.stderr
    return json.loads(output.read_text())


def test_complete_sample_artifact_and_final_memory(tmp_path):
    result = run_sample(tmp_path, 'qreg q[2]; creg z[3]; creg a[1]; x q[0]; '
        'measure q[0] -> z[2]; measure q[0] -> a[0]; x q[0]; measure q[0] -> a[0];')
    assert result['status'] == 'SUCCEEDED'
    summary = result['summary']
    assert summary['result_schema'] == 'qasm-samples/1'
    assert summary['registers'] == [dict(name='z',size=3,offset=0),dict(name='a',size=1,offset=3)]
    assert summary['counts'] == {'0010':32}
    assert summary['preview'] == [[0,0,1,0]]*20
    with np.load(io.BytesIO(base64.b64decode(result['artifact']))) as artifact:
        assert artifact.files == ['classical_memory']
        memory = artifact['classical_memory']
        assert memory.shape == (32,4) and memory.dtype == np.uint8
        assert np.all(memory == [0,0,1,0])
    assert summary['engine'].startswith('qsim ')
    assert summary['importer'].startswith('cirq ')


def test_reset_midmeasurement_independent_shots(tmp_path):
    result = run_sample(tmp_path, 'qreg q[2]; creg c[3]; h q[0]; cx q[0],q[1]; '
        'measure q[1] -> c[0]; reset q[0]; measure q[0] -> c[1]; measure q[1] -> c[2];')
    assert result['status'] == 'SUCCEEDED'
    assert set(result['summary']['counts']) == {'000','101'}
    assert sum(result['summary']['counts'].values()) == 32


@pytest.mark.parametrize('circuit,category', [
    ('qreg q[1];','invalid_circuit'),
    ('qreg q[1]; creg c[1]; if(c==0) x q[0]; measure q -> c;','unsupported_feature'),
    ('qreg q[100000000]; h q;','resource_limit'),
    ('qreg q[1]; PRIVATE_CIRCUIT_VALUE q[0];','invalid_circuit'),
    ('gate f t { x t; } qreg q[1]; creg c[1]; f q[0]; measure q -> c;','unsupported_feature'),
])
def test_failed_sample_uses_safe_completion_error(tmp_path,circuit,category):
    result = run_sample(tmp_path,circuit)
    assert result['status'] == 'FAILED'
    assert result['error'].startswith(category + ':')
    assert 'artifact' not in result and 'summary' not in result
    assert 'PRIVATE_CIRCUIT_VALUE' not in json.dumps(result)


def test_counts_truncation_and_lexicographic_ties(tmp_path):
    result = run_sample(tmp_path,'qreg q[9]; creg c[9]; h q; measure q -> c;',shots=2048)
    summary = result['summary']
    with np.load(io.BytesIO(base64.b64decode(result['artifact']))) as artifact:
        memory = artifact['classical_memory']
    from collections import Counter
    counts = Counter(''.join(map(str,row)) for row in memory)
    expected = sorted(counts.items(),key=lambda item:(-item[1],item[0]))[:256]
    assert list(summary['counts'].items()) == expected
    assert summary['counts_truncated'] is True
    assert len(summary['preview']) == 20
    assert memory.shape == (2048,9)


def test_incomplete_native_records_cannot_be_success(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import qsimcirq
    from qsim import worker
    monkeypatch.setattr(qsimcirq.QSimSimulator,'run',lambda *args,**kwargs:SimpleNamespace(records={}))
    output = tmp_path/'failed.json'
    worker.simulate_qasm_sample(dict(engine='qsim',input_format='qasm2',operation='sample',
        circuit=PREFIX+'qreg q[1]; creg c[1]; measure q -> c;',shots=4,seed=42),output)
    payload = json.loads(output.read_text())
    assert payload == {'status':'FAILED','error':'execution_failed: simulation did not produce a complete result'}
