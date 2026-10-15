import io
import numpy as np
import pytest
from test_service import client, auth, callback
from test_aer_import import request
from test_aer_worker import run_worker


def test_wide_memory_callback_and_complete_artifact(client, tmp_path):
    body = request('qreg q[1]; creg c[40000]; x q; measure q[0]->c[39999];', shots=24)
    response = client.post('/api/jobs',headers={**auth(),'Idempotency-Key':'wide-aer'},json=body)
    assert response.status_code == 202
    job = response.json()['id']
    accepted = client.get('/internal/jobs/'+job,headers=callback(job)).json()
    completion = run_worker(tmp_path,accepted)
    assert completion['status'] == 'SUCCEEDED',completion
    summary = completion['summary']
    assert summary['preview_truncated'] is True
    assert len(next(iter(summary['counts']))) == 40000
    assert client.post('/internal/jobs/'+job,headers=callback(job),json=completion).status_code == 200
    downloaded = client.get('/api/jobs/'+job+'/result',headers=auth())
    assert downloaded.status_code == 200
    with np.load(io.BytesIO(downloaded.content),allow_pickle=False) as arrays:
        memory = arrays['classical_memory']
        assert memory.shape == (24,40000)
        assert np.all(memory[:,-1]==1) and np.all(memory[:,:-1]==0)


@pytest.mark.parametrize('engine', ['qsim','aer'])
def test_classical_conditions_do_not_trigger_engine_fallback(client, tmp_path, engine):
    body = request('qreg q[1]; creg c[1]; if(c==0) x q; measure q->c;')
    body['engine'] = engine
    response = client.post('/api/jobs',headers=auth(),json=body)
    assert response.status_code == 202
    job = response.json()['id']
    completion = run_worker(tmp_path,client.get('/internal/jobs/'+job,headers=callback(job)).json())
    assert completion['status'] == ('SUCCEEDED' if engine=='aer' else 'FAILED')
    if engine == 'aer':
        assert completion['summary']['engine'].startswith('aer ')
    else:
        assert 'artifact' not in completion
    assert client.post('/internal/jobs/'+job,headers=callback(job),json=completion).status_code == 200


def test_unsupported_construct_fails_accepted_job_safely(client, tmp_path):
    body = request('gate confidential a { x a; } qreg q[1]; creg c[1]; confidential q; measure q->c;')
    response = client.post('/api/jobs',headers=auth(),json=body)
    assert response.status_code == 202
    job = response.json()['id']
    completion = run_worker(tmp_path,client.get('/internal/jobs/'+job,headers=callback(job)).json())
    assert completion['status'] == 'FAILED' and 'confidential' not in completion['error']
    assert completion['error'].startswith('unsupported_feature:')
    assert client.post('/internal/jobs/'+job,headers=callback(job),json=completion).status_code == 200
    assert client.get('/api/jobs/'+job,headers=auth()).json()['status'] == 'FAILED'
