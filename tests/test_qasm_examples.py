"""Submit the published JSON examples and execute their accepted worker payloads."""
import html
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pytest

from test_service import auth, callback, client

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = [
    (path, name) for path in ('qsim/static/index.html', 'qsim/static/agents.md')
    for name in ('qasm2-sample', 'qasm3-sample', 'qasm3-probabilities')
] + [('README.md', 'qasm3-probabilities')]


@pytest.mark.parametrize('path,name', EXAMPLES)
def test_documented_json_roundtrip(client, tmp_path, path, name):
    document = (ROOT / path).read_text()
    if path.endswith('.html'):
        match = re.search(r'<pre id="example-' + name + r'"><code>(.*?)</code></pre>',
                          document, re.DOTALL)
        assert match is not None, 'Missing developer example'
        body = json.loads(html.unescape(match[1]))
    else:
        match = re.search(r'<!-- example: ' + name + r' -->\s*```json\n(.*?)\n```',
                          document, re.DOTALL)
        assert match is not None, 'Missing Markdown example'
        body = json.loads(match[1])
    endpoint = '/api/jobs/' + name.split('-')[0]
    headers = {**auth(), 'Idempotency-Key': 'documented-example'}
    response = client.post(endpoint, headers=headers, json=body)
    assert response.status_code == 202, response.text
    job = response.json()['id']
    assert client.post(endpoint, headers=headers, json=body).json()['id'] == job
    source, output = tmp_path / 'input.json', tmp_path / 'output.json'
    source.write_text(json.dumps(client.get('/internal/jobs/' + job, headers=callback(job)).json()))
    process = subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
        env={'PATH': os.environ['PATH'], 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'},
        capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr.decode()
    assert not process.stderr
    completion = json.loads(output.read_text())
    assert completion['status'] == 'SUCCEEDED', completion
    assert client.post('/internal/jobs/' + job, headers=callback(job), json=completion).status_code == 200
    result = client.get('/api/jobs/' + job + '/result', headers=auth())
    assert result.status_code == 200
    with np.load(io.BytesIO(result.content), allow_pickle=False) as artifact:
        if name.endswith('probabilities'):
            assert artifact['bitstrings'].tolist() == ['0', '1']
            assert artifact['probabilities'] == pytest.approx([.75, .25], abs=1e-6)
            assert completion['summary']['probabilities'] == pytest.approx([.75, .25], abs=1e-6)
        else:
            expected = [0, 1, 0] if name == 'qasm2-sample' else [1, 0]
            memory = artifact['classical_memory']
            assert memory.dtype == np.uint8
            assert memory.shape == (128, len(expected))
            assert np.all(memory == expected)
            assert completion['summary']['counts'] == {''.join(map(str, expected)): 128}
