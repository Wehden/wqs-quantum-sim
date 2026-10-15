import io
import json
import numpy as np
import pytest
from test_service import client, auth, callback
from test_aer_import import request
from test_aer_worker import run_worker


@pytest.mark.parametrize('fmt', ['qasm2','qasm3'])
def test_aer_durable_roundtrip(client, tmp_path, fmt):
    body = request('qreg q[1]; creg c[1]; if(c==0) x q; measure q->c;' if fmt == 'qasm2'
                   else 'qubit q; bit c; if(c==false) { x q; } c=measure q;', fmt)
    generic = body.copy()
    body.pop('input_format')
    headers = {**auth(), 'Idempotency-Key':'aer-native'}
    response = client.post('/api/jobs/'+fmt, headers=headers, json=body)
    assert response.status_code == 202, response.text
    job = response.json()['id']
    assert client.post('/api/jobs/'+fmt, headers=headers, json=body).json()['id'] == job
    assert client.post('/api/jobs', headers=headers, json=generic).json()['id'] == job
    assert client.post('/api/jobs/'+fmt, headers=headers, json={**body,'engine':'qsim'}).status_code == 409
    assert client.get('/api/jobs/'+job, headers=auth('bob')).status_code == 404
    native = client.get('/internal/jobs/'+job, headers=callback(job)).json()
    result = run_worker(tmp_path, native)
    assert result['status'] == 'SUCCEEDED', result
    assert client.post('/internal/jobs/'+job, headers=callback(job), json=result).status_code == 200
    assert client.post('/internal/jobs/'+job, headers=callback(job), json=result).status_code == 200
    assert client.get('/api/jobs/'+job+'/result', headers=auth('bob')).status_code == 404
    downloaded = client.get('/api/jobs/'+job+'/result', headers=auth())
    assert downloaded.status_code == 200
    with np.load(io.BytesIO(downloaded.content),allow_pickle=False) as artifact:
        assert np.all(artifact['classical_memory'] == 1)


@pytest.mark.parametrize('fields', [
    {'input_format':'stim'}, {'operation':'basis_probabilities','bitstrings':['0']},
    {'bindings':{'theta':1},'input_format':'qasm3'}, {'bit_order':'little'},
    {'bitstrings':['0']}, {'unknown':True},
])
def test_aer_invalid_envelopes_create_no_jobs(client, fields):
    body = request('qreg q[1]; creg c[1]; measure q->c;')
    response = client.post('/api/jobs',headers=auth(),json={**body,**fields})
    assert response.status_code == 422
    assert client.get('/api/jobs',headers=auth()).json() == []


def test_capabilities_are_explicit(client):
    capabilities = client.get('/api/capabilities',headers=auth()).json()
    assert capabilities['engines']['clifft']['default'] is True
    assert capabilities['engines']['qsim']['sampling']['conditions'] is False
    aer = capabilities['engines']['aer']
    assert aer['input_formats'] == {'qasm2':['sample'],'qasm3':['sample']}
    assert aer['condition_subset']['nested_conditions'] is False
    assert aer['features_by_operation']['qasm3']['sample']['bindings'] is False
    assert capabilities['streaming']['incremental_samples'] is False


def test_no_native_libraries_in_api_process():
    import subprocess,sys
    subprocess.run([sys.executable,'-c', 'import sys; import qsim.api; '
                    'assert not any(n in sys.modules for n in ("qiskit", "qiskit_aer", "qiskit_qasm3_import", "cirq", "clifft"))'],check=True)
