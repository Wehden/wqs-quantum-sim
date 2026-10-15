import base64
import io
import json
import os
import subprocess
import sys
import numpy as np
import pytest
from test_aer_import import request


def run_worker(tmp_path, body):
    source, output = tmp_path/'request.json', tmp_path/'result.json'
    source.write_text(json.dumps(body))
    result = subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
                            env={'PATH':os.environ['PATH'], 'OPENBLAS_NUM_THREADS':'1', 'OMP_NUM_THREADS':'1'},
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode()
    assert not result.stderr
    return json.loads(output.read_text())


def test_isolated_aer_result(tmp_path):
    result = run_worker(tmp_path, request('qreg q[2]; creg c[1]; creg out[1]; if(c==0) x q[1]; measure q[1]->out[0];'))
    assert result['status'] == 'SUCCEEDED', result
    summary = result['summary']
    assert summary['engine'] == 'aer 0.17.2'
    assert summary['method'] == 'statevector' and summary['device'] == 'CPU'
    assert summary['cpu_threads'] == 1
    with np.load(io.BytesIO(base64.b64decode(result['artifact'])), allow_pickle=False) as data:
        assert np.all(data['classical_memory'] == [0,1])


@pytest.mark.parametrize('body', [
    'qreg q[999999999]; creg c[1]; measure q[0]->c[0];',
    'qreg q[1]; creg c[1]; if(c==2) x q; measure q->c;',
    'include "/private/token.inc"; qreg q[1]; creg c[1]; measure q->c;',
])
def test_isolated_safe_failure(tmp_path, body):
    result = run_worker(tmp_path, request(body))
    assert result['status'] == 'FAILED'
    assert '/private/' not in result['error'] and 'token' not in result['error']
    assert 'artifact' not in result
