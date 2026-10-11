import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest
from fastapi.testclient import TestClient
from qsim import settings, store
from qsim.api import app
from qsim.auth import digest
from qsim import controller


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'DATA', tmp_path)
    monkeypatch.setattr(settings, 'TOKEN_FILE', tmp_path / 'users.json')
    settings.TOKEN_FILE.write_text(json.dumps([{'id': who, 'sha256': digest(who+'-key'), 'active': True} for who in ('alice','bob')]))
    with TestClient(app) as c:
        yield c


def auth(who='alice'):
    return {'Authorization': 'Bearer '+who+'-key'}


def submit(client, **extra):
    response = client.post('/api/jobs', headers=auth(), json={'circuit': 'H 0\nCX 0 1\nM 0 1', **extra})
    assert response.status_code == 202, response.text
    return response.json()['id']


def callback(job):
    return {'Authorization': 'Bearer '+(settings.DATA/job/'callback-token').read_text()}


def test_auth_ownership_and_revocation(client):
    assert client.get('/api/jobs').status_code == 401
    assert client.get('/api/jobs', headers=auth('bad')).status_code == 401
    job = submit(client)
    assert client.get('/api/jobs', headers=auth('bob')).json() == []
    for suffix in ('', '/result'):
        assert client.get('/api/jobs/'+job+suffix, headers=auth('bob')).status_code == 404
    assert client.post('/api/jobs/'+job+'/cancel', headers=auth('bob')).status_code == 404
    users = json.loads(settings.TOKEN_FILE.read_text()); users[0]['active'] = False
    settings.TOKEN_FILE.write_text(json.dumps(users))
    assert client.get('/api/jobs', headers=auth()).status_code == 401


def test_limits_and_untrusted_input(client):
    for fields in ({'shots': 10001}, {'shots': True}, {'seed': -1}, {'circuit': 'x'*65537}, {'run': 'echo exploit'}):
        assert client.post('/api/jobs', headers=auth(), json={'circuit':'M 0', **fields}).status_code == 422
    job = submit(client, circuit='$(touch /tmp/unsafe)\nM 0')
    with store.connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()
    config = controller.task_config(row)
    assert 'touch' not in json.dumps(config)
    submit(client)
    assert client.post('/api/jobs', headers=auth(),json={'circuit':'M 0'}).status_code == 202


def test_cancel_and_no_resurrection(client):
    job = submit(client)
    assert client.get('/internal/jobs/'+job,headers=auth()).status_code == 401
    assert client.get('/internal/jobs/'+job,headers=callback(job)).status_code == 200
    assert client.post('/api/jobs/'+job+'/cancel',headers=auth()).json()['status'] == 'CANCELLED'
    assert client.post('/internal/jobs/'+job,headers=callback(job),json={'status':'RUNNING'}).status_code == 409
    assert client.get('/api/jobs/'+job+'/result',headers=auth()).status_code == 409


def test_real_clifft_and_result_roundtrip(client, tmp_path):
    job = submit(client)
    source, dest = tmp_path/'input.json', tmp_path/'output.json'
    source.write_text(json.dumps(client.get('/internal/jobs/'+job, headers=callback(job)).json()))
    env = {**os.environ,'OPENBLAS_NUM_THREADS':'1'}
    subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(dest)], env=env,check=True)
    payload = json.loads(dest.read_text())
    assert set(payload['summary']['counts']) == {'00','11'}
    assert sum(payload['summary']['counts'].values()) == 1000
    for _ in range(2):
        assert client.post('/internal/jobs/'+job,headers=callback(job),json=payload).status_code == 200
    downloaded = client.get('/api/jobs/'+job+'/result',headers=auth())
    arrays = np.load(io.BytesIO(downloaded.content), allow_pickle=False)
    assert arrays['measurements'].shape == (1000,2)
    assert np.all(arrays['measurements'][:,0] == arrays['measurements'][:,1])
    assert client.post('/internal/jobs/'+job,headers=callback(job),json={'status':'FAILED'}).status_code == 409
    assert client.get('/api/jobs/'+job,headers=auth()).json()['status'] == 'SUCCEEDED'


def test_body_bound_and_readiness(client, monkeypatch):
    monkeypatch.setattr(settings, 'MAX_BODY', 100)
    assert client.post('/api/jobs', content=b'x'*101).status_code == 413
    assert client.get('/readyz').status_code == 200
    settings.TOKEN_FILE.unlink()
    assert client.get('/readyz').status_code == 503


def test_simulator_rejects_excessive_output(tmp_path):
    source, dest = tmp_path/'input.json', tmp_path/'output.json'
    source.write_text(json.dumps({'circuit':'M '+' '.join(str(i) for i in range(200)), 'shots':10000,'seed':42,'input_format':'stim'}))
    proc = subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(dest)],capture_output=True,env={**os.environ,'OPENBLAS_NUM_THREADS':'1'})
    assert proc.returncode != 0
    assert b'1,000,000 cells' in proc.stderr


def test_more_than_100_saved_jobs_per_identity(client):
    first = submit(client)
    with store.connect() as db:
        for index in range(99):
            db.execute('INSERT INTO jobs (id,owner,name,status,created,updated,request,callback_hash) '
                       'SELECT ?,owner,name,status,created,updated,request,callback_hash FROM jobs WHERE id=?',
                       (f'saved-{index}', first))
    headers = {**auth(), 'Idempotency-Key': 'beyond-100'}
    body = {'circuit': 'M 0'}
    response = client.post('/api/jobs/stim', headers=headers, json=body)
    assert response.status_code == 202
    newest = response.json()['id']
    replay = client.post('/api/jobs/stim', headers=headers, json=body)
    assert replay.status_code == 202 and replay.json()['id'] == newest
    saved = client.get('/api/jobs', headers=auth()).json()
    assert len(saved) == 101 and saved[0]['id'] == newest
    assert client.get('/api/jobs', headers=auth('bob')).json() == []
    # Submission also remains available beyond the former service-wide cap.
    with store.connect() as db:
        for index in range(399):
            db.execute('INSERT INTO jobs (id,owner,name,status,created,updated,request,callback_hash) '
                       'SELECT ?,owner,name,status,created,updated,request,callback_hash FROM jobs WHERE id=?',
                       (f'global-{index}', first))
    assert client.post('/api/jobs/stim', headers={**auth(), 'Idempotency-Key': 'beyond-500'}, json=body).status_code == 202
    assert len(client.get('/api/jobs', headers=auth()).json()) == 501
    assert client.post('/api/jobs/stim', headers=headers, json=body).json()['id'] == newest
