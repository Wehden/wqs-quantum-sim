"""Durable, server-owned placement; fake SkyPilot never allocates resources."""
import copy
import json
import sqlite3
import subprocess
import sys

import pytest
from qsim import controller, settings, store
from test_service import client, submit, auth
from test_dispatch import sky, dispatch, pool, CapacityError

IMAGE = 'registry.example.invalid/worker@sha256:' + 'a'*64


def context(monkeypatch, **changes):
    monkeypatch.setattr(settings, 'WORKER_IMAGE', IMAGE)
    profile = settings.physics_execution_profile('scqubits', 'spectrum',
        admission_policy_revision='scqubits-cpu-evr-v1')
    result = dict(version=1, worker_profile=profile,
                  admission_policy_revision='scqubits-cpu-evr-v1',
                  allocation_plan_digest='b'*64, algorithm='scqubits-evr-v1')
    result.update(changes)
    return result


def job(client, context):
    # Public admission remains closed. Seed exactly the existing enqueue transaction
    # shape which the API integration task will own; do not add a runtime bypass.
    identifier = submit(client)
    with store.connect() as db:
        db.execute('UPDATE jobs SET request=? WHERE id=?',
                   (json.dumps(dict(engine='scqubits',operation='spectrum')), identifier))
        db.execute('INSERT INTO job_dispatch(job_id,profile_json) VALUES(?,?)',
                   (identifier,json.dumps(context)))
    return identifier


def complete(identifier, sky):
    rid=dispatch(identifier)['launch_id'];sky.finish(rid)
    sky.results[rid]=(int(rid),None)
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?", (identifier,))
    controller.tick()
    if dispatch(identifier)['profile_json'] is not None:
        check=dispatch(identifier)['native_check_id']
        sky.results[check]=[{'job_id':int(dispatch(identifier)['launch_id']),'status':'SUCCEEDED'}]
        sky.finish(check);controller.tick()


def test_pure_policy_is_native_and_scheduler_free(monkeypatch):
    ctx = context(monkeypatch)
    profile = ctx['worker_profile']
    assert profile['resources']['cpus'] == 2 and profile['resources']['memory'] == 4
    assert profile['image'] == IMAGE
    assert profile['isolation']['address_space_bytes'] == 3*1024**3
    assert profile['isolation']['file_bytes'] == 12*1024**2
    assert profile['backend'] == 'cpu'
    profile['resources']['memory'] = 99
    assert context(monkeypatch)['worker_profile']['resources']['memory'] == 4
    subprocess.run([sys.executable,'-c',
        "import sys; from qsim import settings; assert not any(n in sys.modules for n in ('sky','numpy','scipy','qutip','scqubits'))"],check=True)


@pytest.mark.parametrize('engine,operation,revision',[
    ('scqubits','spectrum','unknown'),('scqubits','export_for_lindblad','unknown'),
    ('lindblad','evolve','unknown'),('josim','transient','unknown'),('gpu','spectrum','unknown')])
def test_uncertified_policy_has_no_fallback(monkeypatch,engine,operation,revision):
    monkeypatch.setattr(settings,'WORKER_IMAGE',IMAGE)
    with pytest.raises(ValueError):
        settings.physics_execution_profile(engine,operation,admission_policy_revision=revision)


@pytest.mark.parametrize('image',['worker:latest','docker:'+IMAGE,IMAGE+'suffix','', 'worker@sha256:'+'g'*64])
def test_physics_requires_immutable_image(monkeypatch,image):
    monkeypatch.setattr(settings,'WORKER_IMAGE',image)
    with pytest.raises(ValueError):
        settings.physics_execution_profile('scqubits','spectrum',admission_policy_revision='scqubits-cpu-evr-v1')


def test_exact_profile_reuses_across_job_plan_differences(client,sky,monkeypatch):
    first=job(client,context(monkeypatch));controller.tick();complete(first,sky)
    second=job(client,context(monkeypatch,allocation_plan_digest='c'*64,algorithm='scqubits-matrix-v1'))
    controller.tick()
    assert dispatch(second)['method']=='exec'
    assert dispatch(second)['cluster']==dispatch(first)['cluster']
    assert json.loads(pool(dispatch(first)['cluster'])['profile_json']) == context(monkeypatch)['worker_profile']
    assert json.loads(sky.calls[-1][2]['envs']['QSIM_EXECUTION_CONTEXT'])['allocation_plan_digest']=='c'*64


@pytest.mark.parametrize('field',['resources','precision','isolation','policy_revision','algorithm_set_revision','backend','image'])
def test_complete_tuple_mismatch_never_reuses(client,sky,monkeypatch,field):
    ctx=context(monkeypatch);first=job(client,ctx);controller.tick();complete(first,sky)
    cluster=dispatch(first)['cluster'];bad=copy.deepcopy(ctx['worker_profile'])
    if field=='resources':bad[field]['cpus']=8
    elif field=='isolation':bad[field]['address_space_bytes']*=2
    elif field=='image':bad[field]='registry.example.invalid/worker@sha256:'+'d'*64
    else:bad[field]='incompatible'
    with store.connect() as db:
        db.execute('UPDATE worker_pool SET profile_json=? WHERE cluster=?',(json.dumps(bad),cluster))
    second=job(client,ctx);controller.tick()
    assert dispatch(second)['method']=='launch'
    assert dispatch(second)['cluster']!=cluster


def test_legacy_circuit_and_physics_pools_are_separate(client,sky,monkeypatch):
    context(monkeypatch)
    legacy=submit(client);controller.tick();complete(legacy,sky)
    physical=job(client,context(monkeypatch));controller.tick();complete(physical,sky)
    assert dispatch(physical)['cluster']!=dispatch(legacy)['cluster']
    circuit=submit(client);controller.tick()
    assert dispatch(circuit)['cluster']==dispatch(legacy)['cluster']


def test_persisted_profile_survives_rollout_and_lost_ack(client,sky,monkeypatch):
    ctx=context(monkeypatch);identifier=job(client,ctx)
    monkeypatch.setattr(settings,'WORKER_IMAGE','worker:new-tag')
    monkeypatch.setenv('QSIM_SKY_INFRA','other-place')
    original=sky.launch
    def lost(*args,**kwargs): original(*args,**kwargs);raise TimeoutError()
    monkeypatch.setattr(sky,'launch',lost)
    controller.tick();assert dispatch(identifier)['intent']=='launch'
    assert sky.calls[0][2]['resources']['image_id']=='docker:'+IMAGE
    assert sky.calls[0][2]['resources']['infra']==ctx['worker_profile']['resources']['infra']
    store.init();controller.tick();controller.tick()
    assert len(sky.calls)==1 and dispatch(identifier)['launch_id']=='1'
    assert json.loads(dispatch(identifier)['profile_json'])==ctx


def test_capacity_retry_keeps_profile_and_identity(client,sky,monkeypatch):
    ctx=context(monkeypatch);identifier=job(client,ctx);controller.tick()
    sky.finish(dispatch(identifier)['launch_id'],CapacityError());controller.tick();controller.tick()
    sky.finish(dispatch(identifier)['down_id']);controller.tick()
    monkeypatch.setattr(settings,'WORKER_IMAGE','worker:new-tag')
    controller.save_dispatch(identifier,retry_after=0);controller.tick()
    assert len([c for c in sky.calls if c[0]=='launch'])==2
    assert sky.calls[-1][2]['resources']['image_id']=='docker:'+IMAGE
    assert json.loads(dispatch(identifier)['profile_json'])==ctx


@pytest.mark.parametrize('mutation',['missing','malformed','gpu','plan','policy','extra'])
def test_invalid_context_fails_before_claim_or_scheduling(client,sky,monkeypatch,mutation):
    ctx=context(monkeypatch)
    if mutation=='gpu':ctx['worker_profile']['backend']='gpu'
    if mutation=='plan':ctx['allocation_plan_digest']='not-a-digest'
    if mutation=='policy':ctx['admission_policy_revision']='unknown'
    if mutation=='extra':ctx['worker_profile']['node']='forbidden-host'
    identifier=job(client,ctx)
    if mutation in ('missing','malformed'):
        with store.connect() as db:
            db.execute('UPDATE job_dispatch SET profile_json=? WHERE job_id=?',
                       (None if mutation=='missing' else '{',identifier))
    controller.tick()
    assert not sky.calls
    assert client.get('/api/jobs/'+identifier,headers=auth()).json()['status']=='FAILED'
    with store.connect() as db:assert db.execute('SELECT count(*) FROM worker_pool').fetchone()[0]==0


def test_legacy_schema_migration_preserves_jobs_and_ownership(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'DATA',tmp_path)
    with sqlite3.connect(tmp_path/'jobs.sqlite3') as db:
        db.execute('CREATE TABLE job_dispatch(job_id TEXT PRIMARY KEY,launch_id TEXT,down_id TEXT,cancel_id TEXT,intent TEXT,intent_at REAL,retry_pending INTEGER NOT NULL DEFAULT 0,retry_after REAL NOT NULL DEFAULT 0)')
        db.execute("INSERT INTO job_dispatch(job_id,launch_id,intent) VALUES('old','ack','launch')")
        db.execute('CREATE TABLE worker_pool(cluster TEXT PRIMARY KEY,job_id TEXT UNIQUE,created REAL NOT NULL,released REAL,retired INTEGER NOT NULL DEFAULT 0)')
        db.execute("INSERT INTO worker_pool VALUES('owned','old',1,NULL,0)")
    store.init();store.init()
    assert dispatch('old')['launch_id']=='ack' and dispatch('old')['profile_json'] is None
    assert pool('owned')['job_id']=='old' and pool('owned')['profile_json'] is None


def test_profile_dispatch_uses_nonroot_account_and_only_scheduled_resources(client,sky,monkeypatch):
    ctx=context(monkeypatch);identifier=job(client,ctx);controller.tick()
    config=sky.calls[-1][2]
    assert config['run'].startswith('cd /opt/qsim && /usr/bin/sudo -n -H -u qsim ')
    assert '--preserve-env=QSIM_JOB_URL,QSIM_JOB_TOKEN,QSIM_EXECUTION_CONTEXT' in config['run']
    assert set(config['resources'])=={'cpus','memory','infra','image_id','labels'}
    assert 'accelerators' not in config['resources']
    assert config['envs']['QSIM_JOB_URL'].endswith(identifier)
    assert json.loads(config['envs']['QSIM_EXECUTION_CONTEXT'])==ctx


def test_null_string_pool_metadata_is_not_a_legacy_identity(client,sky):
    first=submit(client);controller.tick();complete(first,sky)
    with store.connect() as db:
        db.execute("UPDATE worker_pool SET profile_json='null'")
    second=submit(client);controller.tick()
    assert dispatch(second)['method']=='launch'


def test_profile_cancellation_retains_pinned_intent_until_settled(client,sky,monkeypatch):
    ctx=context(monkeypatch);identifier=job(client,ctx);controller.tick()
    client.post('/api/jobs/'+identifier+'/cancel',headers=auth());controller.tick()
    assert sky.calls[-1][0]=='cancel'
    controller.tick();assert sky.calls[-1][0]=='down'
    sky.finish(dispatch(identifier)['down_id']);controller.tick()
    assert client.get('/api/jobs/'+identifier,headers=auth()).json()['status']=='CANCELLED'
    assert json.loads(dispatch(identifier)['profile_json'])==ctx
    assert client.delete('/api/jobs/'+identifier,headers=auth()).status_code==204


def test_semantically_identical_pool_json_reuses_without_allocation(client,sky,monkeypatch):
    ctx=context(monkeypatch);identifier=job(client,ctx);controller.tick();complete(identifier,sky)
    with store.connect() as db:
        db.execute('UPDATE worker_pool SET profile_json=?',(json.dumps(ctx['worker_profile'],indent=4),))
    second=job(client,ctx);controller.tick()
    assert dispatch(second)['method']=='exec'
    assert len([c for c in sky.calls if c[0]=='launch'])==1


def test_claim_and_context_are_durable_before_external_call(client,sky,monkeypatch):
    ctx=context(monkeypatch);identifier=job(client,ctx)
    original=sky.launch
    def checked(task,cluster_name,**kwargs):
        saved=dispatch(identifier)
        assert saved['intent']=='launch' and saved['cluster']==cluster_name
        assert json.loads(saved['profile_json'])==ctx
        assert pool(cluster_name)['job_id']==identifier
        assert json.loads(pool(cluster_name)['profile_json'])==ctx['worker_profile']
        return original(task,cluster_name=cluster_name,**kwargs)
    monkeypatch.setattr(sky,'launch',checked)
    controller.tick();controller.tick()
    assert len(sky.calls)==1


def test_busy_physics_worker_is_not_claimed_twice(client,sky,monkeypatch):
    ctx=context(monkeypatch);first=job(client,ctx);controller.tick();complete(first,sky)
    second=job(client,ctx);third=job(client,ctx);controller.tick()
    assert dispatch(second)['method']=='exec'
    assert dispatch(third)['method']=='launch'
    assert dispatch(second)['cluster']!=dispatch(third)['cluster']


def test_unknown_requested_gpu_resources_cannot_change_server_profile(client,sky,monkeypatch):
    ctx=context(monkeypatch);identifier=job(client,ctx)
    with store.connect() as db:
        db.execute('UPDATE jobs SET request=? WHERE id=?',(json.dumps(dict(engine='scqubits',operation='spectrum',
            resources={'accelerators':'A100:8','memory':1024},node='untrusted-host')),identifier))
    controller.tick()
    assert sky.calls[0][2]['resources']['cpus']==2
    assert 'accelerators' not in sky.calls[0][2]['resources']
    assert 'untrusted-host' not in json.dumps(sky.calls[0][2])


@pytest.mark.parametrize('native',['SUCCEEDED','FAILED','FAILED_SETUP','FAILED_DRIVER','CANCELLED'])
def test_native_terminal_without_callback_becomes_failure_without_reexecution(client,sky,monkeypatch,native):
    identifier=job(client,context(monkeypatch));controller.tick()
    rid=dispatch(identifier)['launch_id'];sky.results[rid]=(17,None);sky.finish(rid)
    controller.tick();check=dispatch(identifier)['native_check_id'];assert check
    sky.results[check]=[{'job_id':17,'status':native}];sky.finish(check)
    controller.tick()
    assert client.get('/api/jobs/'+identifier,headers=auth()).json()['status']=='FAILED'
    assert len([c for c in sky.calls if c[0] in ('launch','exec')])==1


def test_pending_unknown_or_other_native_job_never_fails_owned_job(client,sky,monkeypatch):
    identifier=job(client,context(monkeypatch));controller.tick()
    rid=dispatch(identifier)['launch_id'];sky.results[rid]=(17,None);sky.finish(rid)
    controller.tick();check=dispatch(identifier)['native_check_id']
    sky.results[check]=[{'job_id':18,'status':'FAILED'}];sky.finish(check);controller.tick()
    assert client.get('/api/jobs/'+identifier,headers=auth()).json()['status']=='LAUNCHING'
    assert dispatch(identifier)['native_check_id'] is None


def test_callback_race_wins_native_terminal_reconciliation(client,sky,monkeypatch):
    identifier=job(client,context(monkeypatch));controller.tick()
    rid=dispatch(identifier)['launch_id'];sky.results[rid]=(17,None);sky.finish(rid)
    controller.tick();check=dispatch(identifier)['native_check_id']
    sky.results[check]=[{'job_id':17,'status':'SUCCEEDED'}];sky.finish(check)
    original=sky.get
    def committed(request):
        result=original(request)
        if request==check:
            with store.connect() as db:db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(identifier,))
        return result
    monkeypatch.setattr(sky,'get',committed)
    controller.tick()
    assert client.get('/api/jobs/'+identifier,headers=auth()).json()['status']=='SUCCEEDED'


@pytest.mark.parametrize('native',['FAILED_SETUP','FAILED_DRIVER'])
def test_unhealthy_native_worker_is_torn_down_not_reused(client,sky,monkeypatch,native):
    identifier=job(client,context(monkeypatch));controller.tick()
    rid=dispatch(identifier)['launch_id'];sky.results[rid]=(17,None);sky.finish(rid)
    controller.tick();check=dispatch(identifier)['native_check_id']
    sky.results[check]=[{'job_id':17,'status':native}];sky.finish(check)
    controller.tick();controller.tick()
    assert dispatch(identifier)['down_id']
    assert pool(dispatch(identifier)['cluster'])['job_id']==identifier


def test_confirmed_native_absence_cleans_terminal_job_without_success_attestation(client,sky,monkeypatch):
    from test_dispatch import GoneError
    identifier=job(client,context(monkeypatch));controller.tick()
    rid=dispatch(identifier)['launch_id'];sky.results[rid]=(17,None);sky.finish(rid)
    with store.connect() as db:db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(identifier,))
    controller.tick();check=dispatch(identifier)['native_check_id'];sky.finish(check,GoneError())
    controller.tick()
    assert pool(dispatch(identifier)['cluster']) is None
    with store.connect() as db:assert db.execute('SELECT cleaned FROM jobs WHERE id=?',(identifier,)).fetchone()[0]==1
    assert not (settings.DATA/identifier/'callback-token').exists()
    assert not [call for call in sky.calls if call[0]=='down']


def test_native_observation_failure_preserves_running_ownership_and_never_relaunches(client,sky,monkeypatch):
    identifier=job(client,context(monkeypatch));controller.tick()
    rid=dispatch(identifier)['launch_id'];sky.results[rid]=(17,None);sky.finish(rid)
    controller.tick();check=dispatch(identifier)['native_check_id']
    original=sky.api_status
    def unavailable(**kwargs):
        if kwargs.get('request_ids')==[check]:raise RuntimeError('private status lookup failed')
        return original(**kwargs)
    monkeypatch.setattr(sky,'api_status',unavailable);controller.tick()
    row=client.get('/api/jobs/'+identifier,headers=auth()).json()
    assert row['status']=='LAUNCHING' and 'unverified' in row['error']
    assert dispatch(identifier)['retry_pending']==0
    assert [call[0] for call in sky.calls]==['launch','queue']
    assert pool(dispatch(identifier)['cluster'])['job_id']==identifier


def test_native_ack_survives_api_request_expiration_without_reexecution(client,sky,monkeypatch):
    identifier=job(client,context(monkeypatch));controller.tick()
    rid=dispatch(identifier)['launch_id'];sky.results[rid]=(17,None);sky.finish(rid)
    controller.tick();check=dispatch(identifier)['native_check_id']
    assert dispatch(identifier)['native_job_id']==17
    del sky.requests[rid];del sky.results[rid]
    sky.results[check]=[{'job_id':17,'status':'FAILED'}];sky.finish(check)
    controller.tick();controller.tick()
    assert client.get('/api/jobs/'+identifier,headers=auth()).json()['status']=='FAILED'
    with store.connect() as db:assert db.execute('SELECT cleaned FROM jobs WHERE id=?',(identifier,)).fetchone()[0]==1
    assert len([call for call in sky.calls if call[0] in ('launch','exec')])==1


def test_skypilot_root_shell_function_breaks_bare_sudo_flags():
    from sky.utils.command_runner import ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD
    assert 'function sudo() { "$@"; }' in ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD
    # Reproduce its effective root branch without needing host root privileges.
    result=subprocess.run(['/bin/bash','-c','function sudo() { "$@"; }; sudo -n true'],capture_output=True,text=True)
    assert result.returncode==127 and '-n: command not found' in result.stderr
