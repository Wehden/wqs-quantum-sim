import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest
from fastapi.testclient import TestClient
from qsim import settings, store, streams
from qsim.api import app
from qsim.auth import digest

QASM='OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; h q[0]; cx q[0],q[1];'

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'DATA',tmp_path)
    monkeypatch.setattr(settings,'TOKEN_FILE',tmp_path/'users.json')
    settings.TOKEN_FILE.write_text(json.dumps([{'id':u,'sha256':digest(u+'-key'),'active':True} for u in ('alice','bob')]))
    streams.connections.clear()
    with TestClient(app) as c:yield c
    streams.connections.clear()

def headers(key='request-1',user='alice'):
    return {'Authorization':'Bearer '+user+'-key','Idempotency-Key':key}

def submit(client,key='request-1',user='alice',**extra):
    return client.post('/api/jobs/stim',headers=headers(key,user),json={'circuit':'H 0\nCX 0 1\nM 0 1',**extra})

def test_atomic_concurrent_idempotency_and_owner_scope(client):
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses=list(pool.map(lambda _:submit(client),range(8)))
    assert all(r.status_code==202 for r in responses)
    assert len({r.json()['id'] for r in responses})==1
    assert sum(r.headers['Idempotency-Replayed']=='false' for r in responses)==1
    assert submit(client,shots=2).status_code==409
    assert submit(client,user='bob').json()['id']!=responses[0].json()['id']
    store.init() # Reloading schema/process preserves the reservation.
    assert submit(client).json()['id']==responses[0].json()['id']
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM job_events').fetchone()[0]==2

def test_required_key_replay_and_tombstone(client):
    assert client.post('/api/jobs/stim',headers={'Authorization':'Bearer alice-key'},json={'circuit':'M 0'}).status_code==422
    assert submit(client,key='invalid key').status_code==422
    first=submit(client).json();submit(client,key='second')
    assert submit(client,key='third').status_code==202
    assert submit(client).status_code==202
    with store.connect() as db:db.execute('DELETE FROM jobs WHERE id=?',(first['id'],))
    assert submit(client).status_code==410
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM job_events WHERE job_id=?',(first['id'],)).fetchone()[0]==0
    # A new key explicitly requests new work.
    assert submit(client,key='new-work').status_code==202

def test_format_validation_and_qasm_probabilities(client,tmp_path):
    payload={'circuit':QASM,'bitstrings':['00','01','10','11']}
    for invalid in ({'bitstrings':['0','11']},{'bitstrings':['zz']},{'shots':10},{'bitstrings':[]}):
        assert client.post('/api/jobs/qasm2',headers=headers(),json={**payload,**invalid}).status_code==422
    r=client.post('/api/jobs/qasm2',headers=headers(),json=payload);assert r.status_code==202
    job=r.json()['id'];source=tmp_path/'input.json';dest=tmp_path/'output.json'
    with store.connect() as db:source.write_text(db.execute('SELECT request FROM jobs WHERE id=?',(job,)).fetchone()[0])
    subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(dest)],env={**os.environ,'OPENBLAS_NUM_THREADS':'1'},check=True)
    completion=json.loads(dest.read_text());assert completion['summary']['probabilities']==pytest.approx([.5,0,0,.5])
    token=(settings.DATA/job/'callback-token').read_text()
    assert client.post('/internal/jobs/'+job,headers={'Authorization':'Bearer '+token},json=completion).status_code==200
    with np.load(io.BytesIO(client.get('/api/jobs/'+job+'/result',headers=headers()).content),allow_pickle=False) as arrays:
        assert arrays['bitstrings'].tolist()==payload['bitstrings']
        assert arrays['probabilities']==pytest.approx([.5,0,0,.5])
    events=client.get('/api/jobs/'+job+'/events',headers=headers()).text
    assert 'event: result' in events and 'probabilities' in events and '/result' in events
    # Clifft's unitary importer rejects measurement-bearing QASM.
    source.write_text(json.dumps({**json.loads(source.read_text()),'circuit':QASM+' measure q[0] -> c[0];'}))
    bad=subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(dest)],env={**os.environ,'OPENBLAS_NUM_THREADS':'1'},capture_output=True)
    assert bad.returncode!=0 and b'Non-unitary' in bad.stderr

def test_durable_events_replay_auth_and_terminal(client):
    job=submit(client).json()['id']
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='LAUNCHING' WHERE id=?",(job,))
        db.execute("UPDATE jobs SET status='RUNNING' WHERE id=?",(job,))
    client.post('/api/jobs/'+job+'/cancel',headers=headers())
    store.init()
    url='/api/jobs/'+job+'/events'
    assert client.get(url).status_code==401
    assert client.get(url,headers=headers(user='bob')).status_code==404
    for cursor in ('-1','foo','999','１'):
        if cursor.isascii():assert client.get(url,headers={**headers(),'Last-Event-ID':cursor}).status_code==400
    r=client.get(url,headers={**headers(),'Last-Event-ID':'2'})
    assert r.status_code==200 and r.headers['content-type'].startswith('text/event-stream')
    assert 'no-transform' in r.headers['cache-control']
    assert 'id: 1\n' not in r.text and 'id: 2\n' not in r.text
    assert 'id: 3\n' in r.text and 'id: 4\n' in r.text and 'event: terminal' in r.text
    assert not streams.connections
    assert 'id:' not in client.get(url,headers={**headers(),'Last-Event-ID':'4'}).text
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1

def test_stream_disconnect_revocation_and_capacity(client,monkeypatch):
    job=submit(client).json()['id'];monkeypatch.setattr(streams,'POLL_SECONDS',0)
    async def check():
        streams.connections['test']='alice'
        iterator=streams.event_stream(job,'alice','Bearer alice-key',0,'test')
        assert 'connected' in await anext(iterator)
        assert 'QUEUED' in await anext(iterator)
        await iterator.aclose();assert 'test' not in streams.connections
        streams.connections['test']='alice'
        iterator=streams.event_stream(job,'alice','Bearer alice-key',0,'test')
        await anext(iterator);await anext(iterator)
        users=json.loads(settings.TOKEN_FILE.read_text());users[0]['active']=False;settings.TOKEN_FILE.write_text(json.dumps(users))
        assert 'Authentication required' in await anext(iterator)
        await iterator.aclose();assert 'test' not in streams.connections
        users[0]['active']=True;settings.TOKEN_FILE.write_text(json.dumps(users))
    asyncio.run(check())
    assert client.get('/api/jobs/'+job,headers=headers()).json()['status']=='QUEUED'
    streams.connections.update({str(i):'alice' for i in range(8)})
    assert client.get('/api/jobs/'+job+'/events',headers=headers()).status_code==429

def test_legacy_job_gets_current_state_snapshot(client):
    job=submit(client).json()['id']
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='FAILED',error='Legacy failure' WHERE id=?",(job,))
        db.execute('DROP TRIGGER jobs_event_insert');db.execute('DROP TRIGGER jobs_event_update');db.execute('DROP TRIGGER jobs_event_delete')
        db.execute('DROP TABLE job_events')
    store.init()
    response=client.get('/api/jobs/'+job+'/events',headers=headers())
    assert 'id: 1\n' in response.text and 'Legacy failure' in response.text and 'event: terminal' in response.text

@pytest.mark.parametrize('order,expected',[('big',[1,0]),('little',[0,1])])
def test_qasm_bit_order(tmp_path,order,expected):
    source=tmp_path/'request.json';output=tmp_path/'result.json'
    source.write_text(json.dumps({'circuit':'OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; x q[0];','input_format':'qasm2','operation':'basis_probabilities','bitstrings':['10','01'],'bit_order':order}))
    subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(output)],env={**os.environ,'OPENBLAS_NUM_THREADS':'1'},check=True)
    assert json.loads(output.read_text())['summary']['probabilities']==expected

def test_asgi_send_disconnect_releases_stream_without_gc(client):
    from starlette.requests import ClientDisconnect
    job=submit(client).json()['id']
    async def check():
        response=await streams.events(job,user='alice',authorization='Bearer alice-key',last_event_id='0')
        assert len(streams.connections)==1
        async def receive():return {'type':'http.request','body':b'','more_body':False}
        async def send(message):
            if message['type']=='http.response.body':raise OSError('Disconnected socket')
        with pytest.raises(ClientDisconnect):
            await response({'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'method':'GET'},receive,send)
        assert not streams.connections
        assert response.body_iterator.ag_frame is None
    asyncio.run(check())

def test_asgi_stalled_send_obeys_wall_deadline(client,monkeypatch):
    job=submit(client).json()['id'];monkeypatch.setattr(streams,'MAX_STREAM_SECONDS',.03)
    async def check():
        response=await streams.events(job,user='alice',authorization='Bearer alice-key',last_event_id='0')
        async def receive():return {'type':'http.request','body':b'','more_body':False}
        async def send(message):
            if message['type']=='http.response.body':await asyncio.Event().wait()
        await asyncio.wait_for(response({'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'method':'GET'},receive,send),timeout=1)
        assert not streams.connections
        assert response.body_iterator.ag_frame is None
    asyncio.run(check())

@pytest.mark.parametrize('stalled',[False,True])
def test_full_asgi_app_stream_send_cleanup(client,monkeypatch,stalled):
    from starlette.requests import ClientDisconnect
    job=submit(client).json()['id']
    monkeypatch.setattr(streams,'MAX_STREAM_SECONDS',.05 if stalled else 300)
    async def check():
        async def receive():return {'type':'http.request','body':b'','more_body':False}
        async def send(message):
            if message['type']=='http.response.body':
                assert streams.connections
                if stalled:await asyncio.Event().wait()
                else:raise OSError('Disconnected socket')
        path='/api/jobs/'+job+'/events'
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1','method':'GET',
               'scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'','root_path':'',
               'headers':[(b'authorization',b'Bearer alice-key')],'server':('testserver',80),'client':('127.0.0.1',1234)}
        if stalled:await asyncio.wait_for(app(scope,receive,send),timeout=1)
        else:
            with pytest.raises(ClientDisconnect):await app(scope,receive,send)
        assert not streams.connections
    asyncio.run(check())


@pytest.mark.parametrize('engine,expected', [
    ('clifft', '4e0a4598ff406882c24d7201dae48ac02b4d60abe4501461eac95e5906fd1b68'),
    ('qsim', 'b315bb523bd80e9a7a53ff39639da6d90aea492933ba24ba2f495da202a7ea7e'),
])
def test_historical_qasm_hash_and_route_replay(client, engine, expected):
    body = {'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; x q[0];',
            'bitstrings': ['10', '01']}
    if engine != 'clifft':
        body['engine'] = engine
    first = client.post('/api/jobs/qasm2', headers=headers(), json=body)
    assert first.status_code == 202
    explicit = {**body, 'engine': engine, 'name': 'Untitled circuit', 'bit_order': 'big'}
    generic = {**explicit, 'input_format': 'qasm2', 'operation': 'basis_probabilities',
               'shots': 1000, 'seed': 42}
    for route, payload in [('/api/jobs/qasm2', explicit), ('/api/jobs', generic)]:
        replay = client.post(route, headers=headers(), json=payload)
        assert replay.status_code == 202
        assert replay.json()['id'] == first.json()['id']
        assert replay.headers['Idempotency-Replayed'] == 'true'
    with store.connect() as db:
        assert db.execute('SELECT request_hash FROM job_idempotency').fetchone()[0] == expected
    for change in ({'engine': 'qsim' if engine == 'clifft' else 'clifft'},
                   {'shots': 2}, {'seed': 3}, {'bit_order': 'little'},
                   {'bitstrings': ['01', '10']}):
        assert client.post('/api/jobs', headers=headers(), json={**generic, **change}).status_code == 409


@pytest.mark.parametrize('name,expected', [
    ('Untitled circuit', '409c2759f8374110d1b50acebfc1034b9b13637eac55344d0779b5380a139b49'),
    ('é', 'b98fe04521bfaf5c90efc0b010dd762d695b2445658cb1d6cc34e11948ec3ef2'),
])
def test_historical_stim_hash_and_route_replay(client, name, expected):
    body = {'circuit': 'M 0'}
    if name != 'Untitled circuit':
        body['name'] = name
    first = client.post('/api/jobs/stim', headers=headers(), json=body)
    assert first.status_code == 202
    explicit = {**body, 'name': name, 'shots': 1000, 'seed': 42,
                'engine': 'clifft', 'operation': 'sample', 'input_format': 'stim',
                'bitstrings': None, 'bit_order': 'big'}
    replay = client.post('/api/jobs', headers=headers(), json=explicit)
    assert replay.status_code == 202 and replay.json()['id'] == first.json()['id']
    with store.connect() as db:
        assert db.execute('SELECT request_hash FROM job_idempotency').fetchone()[0] == expected
    anonymous_key = {'Authorization': 'Bearer alice-key'}
    assert client.post('/api/jobs', headers=anonymous_key, json=body).json()['id'] != first.json()['id']


def test_legacy_canonical_bytes_survive_empty_binding_default():
    from types import SimpleNamespace
    from qsim.api import canonical_request
    from qsim.models import Submission
    payload = Submission(circuit='M 0').model_dump()
    expected = ('{"bit_order":"big","bitstrings":null,"circuit":"M 0","input_format":"stim",'
                '"name":"Untitled circuit","operation":"sample","seed":42,"shots":1000}')
    assert canonical_request(Submission(circuit='M 0')) == expected
    future_default = SimpleNamespace(model_dump=lambda: {**payload, 'bindings': {}})
    assert canonical_request(future_default) == expected
    bound = SimpleNamespace(model_dump=lambda: {**payload, 'bindings': {'theta': 1.0}})
    assert canonical_request(bound) != expected
    for input_format, operation in [('qasm3', 'sample'), ('qasm3', 'basis_probabilities')]:
        new_operation = SimpleNamespace(model_dump=lambda: {
            **payload, 'input_format': input_format, 'operation': operation, 'bindings': {}})
        assert json.loads(canonical_request(new_operation))['bindings'] == {}


def test_qasm3_bindings_numeric_identity_and_owner_scope(client):
    body = {'engine': 'qsim', 'operation': 'sample', 'circuit': 'parsed only inside worker',
            'bindings': {'theta': 1, 'phi': -0.0}}
    first = client.post('/api/jobs/qasm3', headers=headers(), json=body)
    assert first.status_code == 202, first.text
    equivalent = {**body, 'bindings': {'phi': 0, 'theta': 1.0}}
    replay = client.post('/api/jobs/qasm3', headers=headers(), json=equivalent)
    assert replay.status_code == 202 and replay.json()['id'] == first.json()['id']
    generic = client.post('/api/jobs', headers=headers(), json={**equivalent, 'input_format': 'qasm3'})
    assert generic.status_code == 202 and generic.json()['id'] == first.json()['id']
    assert client.post('/api/jobs/qasm3', headers=headers(), json={**body, 'bindings': {'theta': 2, 'phi': 0}}).status_code == 409
    second = client.post('/api/jobs/qasm3', headers=headers(user='bob'), json=body)
    assert second.status_code == 202 and second.json()['id'] != first.json()['id']
    assert client.get('/api/jobs/' + first.json()['id'], headers=headers(user='bob')).status_code == 404
    with store.connect() as db:
        saved = json.loads(db.execute('SELECT request FROM jobs WHERE id=?', (first.json()['id'],)).fetchone()[0])
    assert saved['bindings'] == {'theta': 1.0, 'phi': 0.0}
    assert '"phi":0.0' in json.dumps(saved, separators=(',', ':'))


@pytest.mark.parametrize('value', [True, '1', None, [1], {}, float('nan'), float('inf'), -float('inf'), 10**400])
def test_qasm3_invalid_binding_values_reject_before_queue(client, value):
    body = {'engine': 'qsim', 'operation': 'sample', 'circuit': 'private-circuit-sentinel', 'bindings': {'private-binding-sentinel': value}}
    for route, extra in [('/api/jobs/qasm3', {}), ('/api/jobs', {'input_format': 'qasm3'})]:
        # Raw JSON reaches the server for nonfinite values that httpx's encoder rejects.
        response = client.post(route, headers={**headers(), 'Content-Type': 'application/json'},
                               content=json.dumps({**body, **extra}))
        assert response.status_code == 422, response.text
        assert 'private-circuit-sentinel' not in response.text
        assert 'private-binding-sentinel' not in response.text
        assert all(set(error) == {'type', 'loc', 'msg'} for error in response.json()['detail'])
    assert client.get('/api/jobs', headers=headers()).json() == []


@pytest.mark.parametrize('change', [
    {'operation': None}, {'engine': 'clifft'}, {'bit_order': 'big'}, {'bitstrings': None},
    {'bindings': None}, {'bindings': []}, {'unexpected': True},
])
def test_qasm3_invalid_envelope(client, change):
    body = {'engine': 'qsim', 'operation': 'sample', 'circuit': 'not parsed', **change}
    assert client.post('/api/jobs/qasm3', headers=headers(), json=body).status_code == 422


def test_qasm3_requires_explicit_operation_and_preserves_default_engine(client):
    for body in ({'engine': 'qsim', 'circuit': 'x'}, {'operation': 'sample', 'circuit': 'x'}):
        assert client.post('/api/jobs/qasm3', headers=headers(), json=body).status_code == 422
        assert client.post('/api/jobs', headers=headers(), json={**body, 'input_format': 'qasm3'}).status_code == 422
    assert client.post('/api/jobs/qasm3', headers={'Authorization': 'Bearer alice-key'},
                       json={'engine': 'qsim', 'operation': 'sample', 'circuit': 'x'}).status_code == 422


def test_qasm2_sample_identity_survives_binding_schema(client):
    body = {'engine': 'qsim', 'operation': 'sample',
            'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; creg c[1]; measure q -> c;'}
    response = client.post('/api/jobs/qasm2', headers=headers(), json=body)
    assert response.status_code == 202
    with store.connect() as db:
        assert db.execute('SELECT request_hash FROM job_idempotency').fetchone()[0] == '780987147dd468bb2c12770b604b1125e8f1376750538e3ad29177dde32bd18c'


def test_qasm3_capabilities_match_operation_scope(client):
    capability = client.get('/api/capabilities', headers=headers()).json()
    qsim = capability['engines']['qsim']
    assert qsim['input_formats']['qasm3'] == ['sample', 'basis_probabilities']
    features = qsim['features_by_operation']
    assert features['qasm2']['basis_probabilities'] == {'custom_gates': False, 'bindings': False}
    assert features['qasm2']['sample'] == {'custom_gates': True, 'bindings': False}
    assert features['qasm3']['sample']['bindings']
    assert not features['qasm3']['sample']['conditions']
    assert qsim['binding_types'] == ['float[32]', 'float[64]', 'angle[32]', 'angle[64]']


@pytest.mark.parametrize('operation', ['sample', 'basis_probabilities'])
def test_bound_qasm3_job_result_roundtrip(client, tmp_path, operation):
    import math
    circuit = 'OPENQASM 3.0; include "stdgates.inc"; input float[64] theta; qubit q; rx(theta) q;'
    body = {'engine': 'qsim', 'operation': operation, 'bindings': {'theta': math.pi}, 'circuit': circuit}
    if operation == 'sample':
        body.update(circuit=circuit + ' bit c; c=measure q;', shots=4)
    else:
        body['bitstrings'] = ['0', '1']
    response = client.post('/api/jobs/qasm3', headers=headers(), json=body)
    assert response.status_code == 202, response.text
    job = response.json()['id']
    token = (settings.DATA / job / 'callback-token').read_text()
    callback_headers = {'Authorization': 'Bearer ' + token}
    source, output = tmp_path / 'request.json', tmp_path / 'completion.json'
    source.write_text(json.dumps(client.get('/internal/jobs/' + job, headers=callback_headers).json()))
    subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
                   env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}, check=True)
    completion = json.loads(output.read_text())
    assert completion['status'] == 'SUCCEEDED', completion
    assert client.post('/internal/jobs/' + job, headers=callback_headers, json=completion).status_code == 200
    downloaded = client.get('/api/jobs/' + job + '/result', headers=headers())
    assert downloaded.status_code == 200
    with np.load(io.BytesIO(downloaded.content), allow_pickle=False) as arrays:
        if operation == 'sample':
            assert arrays['classical_memory'].tolist() == [[1]] * 4
        else:
            assert arrays['probabilities'] == pytest.approx([0, 1], abs=1e-6)
