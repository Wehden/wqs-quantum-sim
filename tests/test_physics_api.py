"""Physics normalization and admission share the existing durable job path."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from qsim import api, settings, store
from test_service import client, auth, callback, submit


def scqubits():
    return dict(schema_version=1,operation='spectrum',evals_count=4,
                model=dict(qubit='Transmon',params=dict(EJ=15.,EC=.3,ng=0.,ncut=30)))


def enabled(monkeypatch):
    monkeypatch.setenv('QSIM_PHYSICS_ENABLED','1')
    monkeypatch.setattr(settings,'WORKER_IMAGE','registry.example.invalid/worker@sha256:'+'a'*64)


def headers(key='physics', who='alice'):
    return {**auth(who),'Idempotency-Key':key}


def test_closed_admission_and_hidden_capabilities(client,monkeypatch):
    monkeypatch.delenv('QSIM_PHYSICS_ENABLED',raising=False)
    response=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    assert response.status_code==503
    assert not {'scqubits','lindblad','josim'} & client.get('/api/capabilities',headers=auth()).json()['engines'].keys()
    assert client.get('/api/jobs',headers=auth()).json()==[]


@pytest.mark.parametrize('path',['/api/jobs/scqubits','/api/jobs'])
def test_authentication_precedes_admission(client,monkeypatch,path):
    enabled(monkeypatch)
    body={**scqubits(),'engine':'scqubits','input_format':'scqubits_model'}
    assert client.post(path,headers={'Idempotency-Key':'a'},json=body).status_code==401


def test_explicit_generic_canonical_replay_and_context(client,monkeypatch):
    enabled(monkeypatch)
    first=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    assert first.status_code==202,first.text
    identifier=first.json()['id']
    body={**scqubits(),'engine':'scqubits','input_format':'scqubits_model',
          'name':'Untitled simulation','return_vectors':False}
    with store.connect() as db:
        row=db.execute('SELECT * FROM jobs WHERE id=?',(identifier,)).fetchone()
        saved=db.execute('SELECT profile_json FROM job_dispatch WHERE job_id=?',(identifier,)).fetchone()[0]
        ctx=json.loads(saved)
        assert ctx['worker_profile']['id']=='physics-cpu-v1'
        assert len(ctx['allocation_plan_digest'])==64
        assert ctx['admission_policy_revision']=='scqubits-cpu-evr-v1'
        assert 'worker_profile' not in json.loads(row['request'])
    # Current policy/image/activation changes must not reject a durable retry.
    monkeypatch.delenv('QSIM_PHYSICS_ENABLED')
    monkeypatch.setattr(settings,'WORKER_IMAGE','unavailable-new-default')
    retry=client.post('/api/jobs',headers=headers(),json=body)
    assert retry.status_code==202 and retry.json()['id']==identifier
    assert retry.headers['Idempotency-Replayed']=='true'
    conflict=client.post('/api/jobs/scqubits',headers=headers(),json={**scqubits(),'evals_count':5})
    assert conflict.status_code==409
    owner=client.post('/api/jobs/scqubits',headers=headers(who='bob'),json=scqubits())
    assert owner.status_code==503
    assert client.get('/internal/jobs/'+identifier,headers=callback(identifier)).json()=={'request':json.loads(row['request']),'execution_context':ctx}


def test_generic_key_optional_explicit_key_required(client,monkeypatch):
    enabled(monkeypatch)
    assert client.post('/api/jobs/scqubits',headers=auth(),json=scqubits()).status_code==422
    body={**scqubits(),'engine':'scqubits','input_format':'scqubits_model'}
    one=client.post('/api/jobs',headers=auth(),json=body)
    two=client.post('/api/jobs',headers=auth(),json=body)
    assert one.status_code==two.status_code==202
    assert one.json()['id']!=two.json()['id']


@pytest.mark.parametrize('patch,pointer',[
    ({'schema_version':True},'/schema_version'),({'schema_version':'1'},'/schema_version'),
    ({'schema_version':2},'/schema_version'),({'shots':4},'/shots'),
    ({'engine':'lindblad'},'/engine'),({'input_format':'stim'},'/input_format'),
    ({'backend':'gpu'},'/backend'),({'name':''},'/name'),
    ({'strange~/field':1},'/strange~0~1field')])
def test_strict_validation_pointer_no_job(client,monkeypatch,patch,pointer):
    enabled(monkeypatch)
    result=client.post('/api/jobs/scqubits',headers=headers(),json={**scqubits(),**patch})
    assert result.status_code==422,result.text
    assert pointer in [e['pointer'] for e in result.json()['detail']]
    assert client.get('/api/jobs',headers=auth()).json()==[]


def test_nested_pointer_does_not_include_discriminator_label(client,monkeypatch):
    enabled(monkeypatch)
    body=scqubits();body['model']['params']['ncut']='bad'
    result=client.post('/api/jobs/scqubits',headers=headers(),json=body)
    assert result.status_code==422
    assert '/model/params/ncut' in [e['pointer'] for e in result.json()['detail']]


@pytest.mark.parametrize('engine,body',[
    ('lindblad',dict(schema_version=1,operation='spectrum',evals_count=2,model={'modes':[{'name':'q','kind':'oscillator','dimension':2,'frequency':1.}]})),
    ('josim',dict(schema_version=1,netlist=Path('tests/fixtures/physics/josim/sfq.cir').read_text())),
    ('scqubits',{**scqubits(),'operation':'export_for_lindblad','operators':['n_operator']}),
])
def test_certified_cpu_operations_enqueue(client,monkeypatch,engine,body):
    enabled(monkeypatch)
    result=client.post('/api/jobs/'+engine,headers=headers(),json=body)
    assert result.status_code==202,result.text
    assert result.json()['status']=='QUEUED'
    with store.connect() as db:
        ctx=json.loads(db.execute('SELECT profile_json FROM job_dispatch').fetchone()[0])
        assert ctx['version']==2 and ctx['result_plan']['arrays']


def test_maintenance_holds_new_work_but_replays_old(client,monkeypatch):
    enabled(monkeypatch)
    old=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    with store.connect() as db:db.execute("UPDATE release_control SET release='test-maintenance'")
    replay=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    assert replay.status_code==202 and replay.json()['id']==old.json()['id']
    assert client.post('/api/jobs/scqubits',headers=headers('other'),json=scqubits()).status_code==503


def test_atomic_recheck_closes_validation_race(client,monkeypatch):
    enabled(monkeypatch)
    original=api.admit_physics_request
    entered=False
    def racing(normalized,**kwargs):
        nonlocal entered
        if not entered:
            entered=True
            response=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
            assert response.status_code==202
        return original(normalized,**kwargs)
    monkeypatch.setattr(api,'admit_physics_request',racing)
    result=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    assert result.status_code==202
    assert result.headers['Idempotency-Replayed']=='true'
    assert len(client.get('/api/jobs',headers=auth()).json())==1
    with store.connect() as db:assert db.execute('SELECT count(*) FROM job_dispatch').fetchone()[0]==1


def test_body_size_limit_and_legacy_error_shape(client,monkeypatch):
    enabled(monkeypatch)
    monkeypatch.setattr(settings,'MAX_BODY',128)
    result=client.post('/api/jobs/scqubits',headers=headers(),json={'padding':'x'*256})
    assert result.status_code==413
    monkeypatch.setattr(settings,'MAX_BODY',16*1024**2)
    legacy=client.post('/api/jobs',headers=auth(),json={'circuit':'M 0','shots':True})
    assert legacy.status_code==422
    assert all(set(e)=={'type','loc','msg'} for e in legacy.json()['detail'])


def test_concurrent_physics_retries_create_one_job_and_context(client,monkeypatch):
    enabled(monkeypatch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:client.post('/api/jobs/scqubits',headers=headers(),json=scqubits()),range(6)))
    assert all(result.status_code==202 for result in results)
    assert len({result.json()['id'] for result in results})==1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM job_dispatch WHERE profile_json IS NOT NULL').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM job_idempotency').fetchone()[0]==1


def test_context_digest_seals_actual_allocation_plan(client,monkeypatch):
    from dataclasses import asdict
    import hashlib
    from qsim.physics_submission import normalize_physics_submission
    enabled(monkeypatch)
    result=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    assert result.status_code==202
    model=normalize_physics_submission(scqubits(),explicit_engine='scqubits').request
    expected=hashlib.sha256(json.dumps(asdict(model.admission_plan()),sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()
    with store.connect() as db:
        ctx=json.loads(db.execute('SELECT profile_json FROM job_dispatch').fetchone()[0])
        assert ctx['allocation_plan_digest']==expected
        assert ctx['algorithm']==model.admission_plan().algorithm
        canonical=db.execute('SELECT request FROM jobs').fetchone()[0]
        request_hash=db.execute('SELECT request_hash FROM job_idempotency').fetchone()[0]
        assert hashlib.sha256(canonical.encode()).hexdigest()==request_hash


def test_deleted_physics_reservation_replays_tombstone_before_policy(client,monkeypatch):
    enabled(monkeypatch)
    result=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    with store.connect() as db:db.execute('DELETE FROM jobs WHERE id=?',(result.json()['id'],))
    monkeypatch.delenv('QSIM_PHYSICS_ENABLED')
    assert client.post('/api/jobs/scqubits',headers=headers(),json=scqubits()).status_code==410


def test_conflicting_concurrent_body_is_rechecked_atomically(client,monkeypatch):
    enabled(monkeypatch)
    original=api.admit_physics_request
    entered=False
    def racing(normalized,**kwargs):
        nonlocal entered
        if not entered:
            entered=True
            response=client.post('/api/jobs/scqubits',headers=headers(),json={**scqubits(),'evals_count':5})
            assert response.status_code==202
        return original(normalized,**kwargs)
    monkeypatch.setattr(api,'admit_physics_request',racing)
    assert client.post('/api/jobs/scqubits',headers=headers(),json=scqubits()).status_code==409
    assert len(client.get('/api/jobs',headers=auth()).json())==1


@pytest.mark.parametrize('value',[True,'1',None,0])
def test_strict_schema_version_all_physics_routes(client,monkeypatch,value):
    enabled(monkeypatch)
    for engine,payload in [('scqubits',scqubits()),
            ('lindblad',dict(operation='spectrum',model={'modes':[]})),
            ('josim',dict(netlist=Path('tests/fixtures/physics/josim/sfq.cir').read_text()))]:
        response=client.post('/api/jobs/'+engine,headers=headers(),json={**payload,'schema_version':value})
        assert response.status_code==422
        assert '/schema_version' in [e['pointer'] for e in response.json()['detail']]


def test_unknown_legacy_fields_keep_exact_error_without_physics_pointer(client):
    response=client.post('/api/jobs',headers=auth(),json={'circuit':'M 0','backend':'gpu'})
    assert response.status_code==422
    assert response.json()=={'detail':[{'type':'extra_forbidden','loc':['body','backend'],'msg':'Extra inputs are not permitted'}]}


@pytest.mark.parametrize('key',['bad key','', 'x'*129])
def test_physics_key_errors_have_pointer_without_changing_legacy_errors(client,monkeypatch,key):
    enabled(monkeypatch)
    result=client.post('/api/jobs/scqubits',headers=headers(key),json=scqubits())
    assert result.status_code==422
    assert all('pointer' in item for item in result.json()['detail'])
    assert not client.get('/api/jobs',headers=auth()).json()


def test_finite_structure_still_validated_before_replay(client,monkeypatch):
    enabled(monkeypatch)
    response=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    assert response.status_code==202
    malformed={**scqubits(),'schema_version':True}
    assert client.post('/api/jobs/scqubits',headers=headers(),json=malformed).status_code==422
    assert len(client.get('/api/jobs',headers=auth()).json())==1


def test_current_resource_policy_is_not_run_for_matching_reservation(client,monkeypatch):
    enabled(monkeypatch)
    response=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    assert response.status_code==202
    def changed(*args,**kwargs):raise AssertionError('policy must not run for replay')
    monkeypatch.setattr(api,'admit_physics_request',changed)
    retry=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    assert retry.status_code==202 and retry.json()['id']==response.json()['id']


def test_explicit_generic_mismatches_have_pointer_and_no_job(client,monkeypatch):
    enabled(monkeypatch)
    result=client.post('/api/jobs',headers=headers(),json={**scqubits(),'engine':'scqubits','input_format':'lindblad_model'})
    assert result.status_code==422
    assert '/input_format' in [item['pointer'] for item in result.json()['detail']]
    assert not client.get('/api/jobs',headers=auth()).json()


def test_physics_callback_validates_stored_contract_before_success(client,monkeypatch):
    import base64
    import io
    import numpy as np
    from qsim.physics_results import PhysicsExecutionResult,serialize_physics_result
    from qsim.physics_submission import normalize_physics_submission
    enabled(monkeypatch)
    response=client.post('/api/jobs/scqubits',headers=headers(),json=scqubits())
    identifier=response.json()['id']
    model=normalize_physics_submission(scqubits(),explicit_engine='scqubits').request
    plan=model.admission_plan()
    # Real serializer output for the HTTP boundary; this fixture is not a native numerical proof.
    summary,encoded=serialize_physics_result(PhysicsExecutionResult({'eigenvalues':np.arange(4,dtype=float)},
        {'engine':'scqubits','operation':'spectrum','schema_version':1,'backend':'cpu',
         'precision':'float64/complex128','policy_revision':plan.policy_revision,'algorithm':plan.algorithm},{}),plan.result)
    bad=io.BytesIO();np.savez(bad,eigenvalues=np.array([object()],dtype=object))
    callback_url='/internal/jobs/'+identifier
    rejected=client.post(callback_url,headers=callback(identifier),json={'status':'SUCCEEDED','summary':summary,
        'artifact':base64.b64encode(bad.getvalue()).decode()})
    assert rejected.status_code==422
    assert client.get('/api/jobs/'+identifier,headers=auth()).json()['status']=='QUEUED'
    assert not (settings.DATA/identifier/'result.npz').exists()
    payload={'status':'SUCCEEDED','summary':summary,'artifact':encoded}
    assert client.post(callback_url,headers=callback(identifier),json=payload).status_code==200
    assert client.get('/api/jobs/'+identifier+'/result',headers=auth()).content==base64.b64decode(encoded)
    # Terminal callback replay does not re-run a newly changed admission policy.
    with store.connect() as db:db.execute("UPDATE job_dispatch SET profile_json='{}' WHERE job_id=?",(identifier,))
    assert client.post(callback_url,headers=callback(identifier),json=payload).status_code==200
    assert client.post(callback_url,headers=callback(identifier),json={'status':'FAILED','error':'late'}).status_code==409
