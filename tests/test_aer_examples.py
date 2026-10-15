import html
import io
import json
from pathlib import Path
import re
import numpy as np
import pytest
from test_service import client, auth, callback
from test_aer_worker import run_worker

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('path', ['README.md','qsim/static/agents.md','qsim/static/index.html'])
@pytest.mark.parametrize('name', ['aer-qasm2','aer-qasm3'])
def test_documented_aer_roundtrip(client, tmp_path, path, name):
    document = (ROOT/path).read_text()
    pattern = (r'<pre id="example-'+name+r'"><code>(.*?)</code></pre>' if path.endswith('.html')
               else r'<!-- example: '+name+r' -->\s*```json\n(.*?)\n```')
    match = re.search(pattern,document,re.S)
    assert match
    body = json.loads(html.unescape(match[1]))
    response = client.post('/api/jobs/'+name.split('-')[1],headers={**auth(),'Idempotency-Key':'example'},json=body)
    assert response.status_code == 202,response.text
    job = response.json()['id']
    request = client.get('/internal/jobs/'+job,headers=callback(job)).json()
    result = run_worker(tmp_path,request)
    assert result['status'] == 'SUCCEEDED',result
    assert result['summary']['counts'] == {'1':128}
    assert client.post('/internal/jobs/'+job,headers=callback(job),json=result).status_code == 200
    download = client.get('/api/jobs/'+job+'/result',headers=auth())
    assert download.status_code == 200
    with np.load(io.BytesIO(download.content),allow_pickle=False) as arrays:
        assert arrays['classical_memory'].shape == (128,1)
        assert np.all(arrays['classical_memory'] == 1)
