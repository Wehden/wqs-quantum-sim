import base64
import copy
import io
import json
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np
import pytest

from qsim import release_control, release_probe, settings, store
from qsim.models import Submission
from test_aer_worker import run_worker
from test_cd_release import SHA, config
from test_service import client, auth
from tools.cd import acceptance, release


@pytest.mark.parametrize('name,body', [(n,b) for n,b in acceptance.cases().items() if n in release.CIRCUIT_CASES])
def test_all_smoke_cases_pass_real_workers(tmp_path, name, body, request):
    import os
    if os.environ.get('WQS_JOINED_WORKER_IMAGE'):
        # The mandatory image lane proves circuits in the same packaged image.
        packaged=request.getfixturevalue('joined')
        identifier=complete(packaged,None,body)
        terminal=packaged.client.get('/api/jobs/'+identifier).json()
        raw=packaged.client.get('/api/jobs/'+identifier+'/result').content
        result={'status':terminal['status'],'summary':terminal['summary'],
                'artifact':base64.b64encode(raw).decode()}
    else:
        result = run_worker(tmp_path, Submission(**body).model_dump())
    assert result['status'] == 'SUCCEEDED', result
    artifact = base64.b64decode(result['artifact'])
    acceptance.verify_result(body, result['summary'], artifact)
    broken = copy.deepcopy(result['summary'])
    broken['engine'] = 'unexpected 1.0'
    with pytest.raises(ValueError):
        acceptance.verify_result(body, broken, artifact)
    with pytest.raises(Exception):
        acceptance.verify_result(body, result['summary'], b'not an NPZ')
    if body['operation'] == 'basis_probabilities':
        broken = {**result['summary'], 'probabilities': [1, 0]}
    else:
        broken = {**result['summary'], 'counts': {'0': 16}}
    with pytest.raises(ValueError):
        acceptance.verify_result(body, broken, artifact)


def test_cases_cover_release_contract_without_probability_shots():
    assert set(acceptance.cases()) == release.SMOKE_CASES
    for body in acceptance.cases().values():
        if body and body['operation'] == 'basis_probabilities':
            assert 'shots' not in body and 'seed' not in body


def healthy_files():
    now = time.time()
    (settings.DATA / 'controller-heartbeat').write_text(str(now))
    (settings.DATA / 'skypilot-observation.json').write_text(json.dumps({
        'observed_at': now, 'queues_observed_at': now, 'queues_complete': True, 'queue_errors': []}))


def test_probe_requires_smoke_owner_fresh_observer_and_does_not_change_hold(client):
    healthy_files()
    release_control.control('hold', SHA)
    with pytest.raises(ValueError):
        release_probe.probe(SHA, 'alice', [])
    release_control.control('smoke', SHA, 'alice')
    assert release_probe.probe(SHA, 'alice', [])['cleaned']
    with pytest.raises(ValueError):
        release_probe.probe(SHA, 'bob', [])
    (settings.DATA / 'controller-heartbeat').write_text(str(time.time()-181))
    with pytest.raises(ValueError):
        release_probe.probe(SHA, 'alice', [])
    assert release_control.control('status', SHA)['release'] == SHA


def test_probe_native_identity_matches_exact_job_not_other_success(client):
    healthy_files()
    release_control.control('hold', SHA)
    release_control.control('smoke', SHA, 'alice')
    job = client.post('/api/jobs', headers=auth(), json={'circuit': 'M 0'}).json()['id']
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED',cleaned=1 WHERE id=?", (job,))
        db.execute("INSERT INTO job_dispatch(job_id,cluster,launch_id) VALUES(?,'qs-test','request')", (job,))
    rows = [{'job_id': 2, 'status': 'SUCCEEDED'}]
    sky = SimpleNamespace(get=lambda value: (1, None) if value == 'request' else rows,
                          queue=lambda cluster, **kwargs: 'queue')
    with pytest.raises(ValueError):
        release_probe.probe(SHA, 'alice', [job], native=True, sky=sky)
    rows.append({'job_id': 1, 'status': 'RUNNING'})
    assert release_probe.probe(SHA, 'alice', [job], native=True, sky=sky)['native_succeeded'] is False
    rows[-1]['status'] = 'SUCCEEDED'
    assert release_probe.probe(SHA, 'alice', [job], native=True, sky=sky)['native_succeeded'] is True


def hook(tmp_path):
    conf = config(tmp_path)
    Path(conf['state_dir']).mkdir()
    conf['acceptance'] = {'api_url': 'https://api.example.invalid',
                          'admin_health_url': 'http://admin.example.invalid/healthz',
                          'token_file': str(tmp_path / 'token')}
    (tmp_path / 'token').write_text('synthetic')
    return acceptance.Acceptance(conf, SHA)


def test_hook_rejects_wrong_ledger_before_any_network(tmp_path, monkeypatch):
    verifier = hook(tmp_path)
    (Path(verifier.config['state_dir']) / 'deployment.json').write_text(json.dumps({'sha': SHA, 'status': 'failed'}))
    monkeypatch.setattr(verifier, 'request', lambda *args, **kwargs: pytest.fail('network called'))
    with pytest.raises(ValueError, match='ledger'):
        verifier.execute()


def test_redirects_do_not_forward_credentials():
    assert acceptance.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid') is None


def test_deadline_and_artifact_mismatch_fail_closed(tmp_path):
    verifier = hook(tmp_path)
    verifier.deadline = time.monotonic()-1
    with pytest.raises(TimeoutError):
        verifier.remaining()
    artifact = io.BytesIO()
    np.savez_compressed(artifact, measurements=np.zeros((16, 1), dtype=np.uint8))
    with pytest.raises(ValueError):
        acceptance.verify_result(acceptance.cases()['clifft_stim'],
                                 {'engine': 'clifft test', 'operation': 'sample', 'shots': 16, 'counts': {'1': 16}},
                                 artifact.getvalue())


def test_deployment_generation_and_pod_readiness_are_checked(tmp_path, monkeypatch):
    verifier = hook(tmp_path)
    image = 'registry.example.invalid/image@sha256:' + 'b'*64
    deployment = {'metadata': {'generation': 2}, 'spec': {'replicas': 1,
        'selector': {'matchLabels': {'app': 'example'}},
        'template': {'spec': {'containers': [{'name': role, 'image': image,
            'env': [{'name': 'QSIM_WORKER_IMAGE', 'value': image},{'name':'QSIM_PHYSICS_ENABLED','value':'1'}]} for role in release.ROLES]}}},
        'status': {'observedGeneration': 2, 'replicas': 1, 'readyReplicas': 1, 'updatedReplicas': 1, 'availableReplicas': 1}}
    pod = {'metadata': {}, 'spec': {'containers': [{'name': role, 'image': image} for role in release.ROLES]},
           'status': {'containerStatuses': [{'name': role, 'ready': True, 'imageID': image} for role in release.ROLES]}}
    monkeypatch.setattr(verifier, 'kube', lambda *args: json.dumps(deployment if args[1] == 'deployment' else {'items': [pod]}))
    verifier.roles({target: image for target in release.TARGETS})
    api=next(c for c in deployment['spec']['template']['spec']['containers'] if c['name']=='api')
    for previous in (None,'worker:local','registry.example.invalid/old@sha256:'+'c'*64):
        api['env']=[{'name':'QSIM_PHYSICS_ENABLED','value':'1'}]
        if previous is not None:api['env'].append({'name':'QSIM_WORKER_IMAGE','value':previous})
        with pytest.raises(ValueError,match='API worker image'):
            verifier.roles({target:image for target in release.TARGETS})
    api['env']=[{'name':'QSIM_PHYSICS_ENABLED','value':'1'},
                {'name':'QSIM_WORKER_IMAGE','value':image}]
    deployment['status']['observedGeneration'] = 1
    with pytest.raises(ValueError):
        verifier.roles({target: image for target in release.TARGETS})
    deployment['status']['observedGeneration'] = 2
    pod['status']['containerStatuses'][0]['ready'] = False
    with pytest.raises(ValueError):
        verifier.roles({target: image for target in release.TARGETS})


from test_physics_lifecycle import joined, complete


@pytest.mark.parametrize('name',[name for name in acceptance.cases() if name not in release.CIRCUIT_CASES])
def test_packaged_release_physics_cases(joined,name):
    """Actual HTTP/packaged-native proof; only SkyPilot placement is synthetic."""
    inventory=acceptance.cases();related={}
    def execute(case,body):
        identifier=complete(joined,body['engine'],body)
        summary=joined.client.get('/api/jobs/'+identifier).json()['summary']
        artifact=joined.client.get('/api/jobs/'+identifier+'/result').content
        result=acceptance.verify_result(body,summary,artifact,name=case,related=related)
        related[case]=result
        return body,summary,artifact,result
    if name=='scqubits_export_lindblad':
        parent=execute('scqubits_export_for_lindblad',inventory['scqubits_export_for_lindblad'])[-1]
        body=parent['metadata']['lindblad_request']
    else:body=inventory[name]
    if name=='lindblad_mc_repeat':execute('lindblad_evolve_mc',inventory['lindblad_evolve_mc'])
    body,summary,artifact,result=execute(name,body)
    for key in ('backend','precision','engine_version'):
        altered=copy.deepcopy(summary);altered[key]='wrong'
        with pytest.raises(ValueError):acceptance.verify_result(body,altered,artifact,name=name,related=related)
    with pytest.raises(Exception):acceptance.verify_result(body,summary,b'not NPZ',name=name,related=related)
    # Mutate actual native output while retaining its exact shapes/dtypes/header.
    arrays={**result['arrays'],'metadata_json':np.asarray(json.dumps(result['metadata']))}
    numeric=next(iter(result['arrays']))
    broken={k:v.copy() for k,v in arrays.items()};broken[numeric].flat[0]=np.nan
    bad=io.BytesIO();np.savez(bad,**broken)
    with pytest.raises((ValueError,AssertionError)):
        acceptance.verify_result(body,summary,bad.getvalue(),name=name,related=related)
    broken={k:v.copy() for k,v in arrays.items()};broken[numeric].flat[0]+=1
    bad=io.BytesIO();np.savez(bad,**broken)
    with pytest.raises((ValueError,AssertionError)):
        acceptance.verify_result(body,summary,bad.getvalue(),name=name,related=related)
    if name in ('lindblad_evolve_mc','lindblad_mc_repeat'):
        # Finite, normalized, repeatable but dynamically wrong must also fail.
        broken={k:v.copy() for k,v in arrays.items()}
        broken['states'][:]=np.diag([0,1]);broken['expectations'][:]=1
        bad=io.BytesIO();np.savez(bad,**broken)
        wrong_related={**related,'lindblad_evolve_mc':{'arrays':{k:broken[k] for k in result['arrays']}}}
        with pytest.raises(AssertionError):
            acceptance.verify_result(body,summary,bad.getvalue(),name=name,related=wrong_related)


@pytest.fixture
def named_probe_jobs(client,monkeypatch):
    """Synthetic result records test probe identity only; not native math proof."""
    from qsim.physics_submission import normalize_physics_submission
    from qsim.physics_results import PhysicsExecutionResult,serialize_physics_result
    monkeypatch.setenv('QSIM_PHYSICS_ENABLED','1')
    image='registry.example.invalid/worker@sha256:'+'b'*64
    monkeypatch.setattr(settings,'WORKER_IMAGE',image)
    healthy_files();release_control.control('hold',SHA);release_control.control('smoke',SHA,'alice')
    selected={};inventory=acceptance.cases()
    handoff=inventory['lindblad_spectrum']
    for i,(name,body) in enumerate(inventory.items(),1):
        body=handoff if body is None else body
        response=client.post('/api/jobs',headers={**auth(),'Idempotency-Key':'probe-'+name},json=body)
        assert response.status_code==202,response.text
        job=response.json()['id'];selected[name]=job
        with store.connect() as db:
            context=db.execute('SELECT profile_json FROM job_dispatch WHERE job_id=?',(job,)).fetchone()
        physics=body['engine'] in ('lindblad','scqubits','josim')
        if physics:
            plan=normalize_physics_submission(body).request.admission_plan();ctx=json.loads(context[0])
            arrays={spec.name:np.zeros(spec.shape,dtype=spec.dtype) for spec in plan.result.arrays}
            metadata={k:body[k] for k in ('engine','operation','schema_version')}
            metadata.update(backend='cpu',precision={'lindblad':'complex128','scqubits':'float64/complex128','josim':'float64'}[body['engine']],
                policy_revision=plan.policy_revision,algorithm=plan.algorithm,**ctx['native_identity'])
            if 'solver' in body:metadata['solver']=body['solver']
            if name=='scqubits_export_for_lindblad':metadata['lindblad_request']=handoff
            if body['engine']=='josim':
                metadata.update(trace_names=['V(B01)','P(B01)','V(B02)','P(B02)','I(ROUT)'],
                    trace_units=['V','rad','V','rad','A'],time_units='s')
            summary,encoded=serialize_physics_result(PhysicsExecutionResult(arrays,metadata,{}),plan.result)
            artifact=base64.b64decode(encoded)
        else:summary={};artifact=b'synthetic-circuit-artifact'
        directory=settings.DATA/job;directory.mkdir(exist_ok=True);(directory/'result.npz').write_bytes(artifact)
        with store.connect() as db:
            db.execute("UPDATE jobs SET status='SUCCEEDED',cleaned=1,summary=? WHERE id=?",(json.dumps(summary),job))
            if not physics:db.execute('INSERT INTO job_dispatch(job_id) VALUES(?)',(job,))
            db.execute("UPDATE job_dispatch SET cluster=?,launch_id=?,native_job_id=? WHERE job_id=?",
                ('qs-'+'0'*20,'launch-'+str(i),i,job))
    rows=[{'job_id':i,'status':'SUCCEEDED'} for i in range(1,22)]
    resources=SimpleNamespace(cpus='2',memory='4',accelerators=None,extract_docker_image=lambda:image)
    handle=SimpleNamespace(launched_resources=resources,launched_nodes=1)
    status={'name':'qs-'+'0'*20,'status':'UP','handle':handle}
    def get(value):
        if value=='queue':return rows
        if value=='status':return [status]
        return int(value.split('-')[1]),None
    sky=SimpleNamespace(get=get,queue=lambda *a,**kw:'queue',status=lambda **kw:'status')
    return selected,image,sky,rows,status


def test_named_probe_binds_requests_profiles_artifacts_and_actual_allocations(named_probe_jobs):
    selected,image,sky,rows,status=named_probe_jobs
    result=release_probe.probe(SHA,'alice',selected,native=True,sky=sky,worker_image=image)
    assert result['native_succeeded'] is True and set(result['jobs'])==set(acceptance.cases())
    row=result['jobs']['lindblad_spectrum']
    assert row['worker_image']==image and row['allocation']['backend']=='cpu'
    assert len(row['context_sha256'])==len(row['result_sha256'])==64
    status['handle'].launched_resources.extract_docker_image=lambda:'registry.example.invalid/old@sha256:'+'e'*64
    with pytest.raises(ValueError,match='profile/image'):
        release_probe.probe(SHA,'alice',selected,native=True,sky=sky,worker_image=image)


@pytest.fixture
def distributed_probe_jobs(named_probe_jobs):
    selected,image,sky,rows,status=named_probe_jobs
    allocations={};events=[]
    for index,job in enumerate(selected.values(),1):
        cluster='qs-'+format(index,'020x')
        with store.connect() as db:
            db.execute('UPDATE job_dispatch SET cluster=?,native_job_id=1 WHERE job_id=?',(cluster,job))
        allocations[cluster]={**status,'name':cluster,'handle':copy.deepcopy(status['handle'])}
    queues={cluster:[{'job_id':1,'status':'SUCCEEDED'}] for cluster in allocations}
    def queue(cluster,**kwargs):
        assert kwargs=={'skip_finished':False,'all_users':True}
        events.append(('queue',cluster));return ('queue',cluster)
    def refresh(**kwargs):
        assert kwargs['all_users'] is True and kwargs['refresh'].name=='FORCE'
        events.append(('refresh',kwargs['cluster_names']));return ('refresh',)
    def get(request):
        events.append(('get',request))
        return list(reversed(list(allocations.values()))) if request[0]=='refresh' else queues[request[1]]
    sky=SimpleNamespace(queue=queue,status=refresh,get=get)
    return selected,image,sky,allocations,queues,events


def test_named_probe_checks_21_allocations_with_one_refresh(distributed_probe_jobs):
    selected,image,sky,allocations,queues,events=distributed_probe_jobs
    result=release_probe.probe(SHA,'alice',selected,native=True,sky=sky,worker_image=image)
    assert result['native_succeeded'] is True and len(result['jobs'])==21
    assert {record['cluster'] for record in result['jobs'].values()}==set(allocations)
    assert all(record['native_job_id']==1 and record['native_status']=='SUCCEEDED' for record in result['jobs'].values())
    assert [event for event in events if event[0]=='refresh']==[('refresh',sorted(allocations))]
    first_read=next(index for index,event in enumerate(events) if event[0]=='get')
    assert {event[1] for event in events[:first_read] if event[0]=='queue'}==set(allocations)
    assert len([event for event in events if event[0]=='queue'])==21


@pytest.mark.parametrize('mutation',['missing','duplicate','down','image','cpu','memory','gpu','nodes','wrong-job','pending'])
def test_distributed_probe_keeps_each_allocation_and_native_job_gate(distributed_probe_jobs,mutation):
    selected,image,sky,allocations,queues,events=distributed_probe_jobs
    cluster=next(reversed(allocations));allocation=allocations[cluster];resources=allocation['handle'].launched_resources
    if mutation=='missing':allocations.pop(cluster)
    if mutation=='duplicate':allocations['duplicate']={**allocation}
    if mutation=='down':allocation['status']='STOPPED'
    if mutation=='image':resources.extract_docker_image=lambda:'registry.example.invalid/wrong@sha256:'+'e'*64
    if mutation=='cpu':resources.cpus='1'
    if mutation=='memory':resources.memory='3'
    if mutation=='gpu':resources.accelerators={'T4':1}
    if mutation=='nodes':allocation['handle'].launched_nodes=2
    if mutation=='wrong-job':queues[cluster]=[{'job_id':2,'status':'SUCCEEDED'}]
    if mutation=='pending':
        queues[cluster][0]['status']='RUNNING'
        assert release_probe.probe(SHA,'alice',selected,native=True,sky=sky,worker_image=image)['native_succeeded'] is False
        return
    with pytest.raises(ValueError):
        release_probe.probe(SHA,'alice',selected,native=True,sky=sky,worker_image=image)


@pytest.mark.parametrize('mutation',['request','context','owner','native-failed','native-pending','missing-cluster','unclean','duplicate-selection','extra-case','partial-selection'])
def test_named_probe_rejects_wrong_or_incomplete_evidence(named_probe_jobs,mutation):
    selected,image,sky,rows,status=named_probe_jobs
    job=selected['lindblad_spectrum']
    with store.connect() as db:
        if mutation=='request':db.execute("UPDATE jobs SET request='{}' WHERE id=?",(job,))
        if mutation=='context':db.execute("UPDATE job_dispatch SET profile_json='{}' WHERE job_id=?",(job,))
        if mutation=='owner':db.execute("UPDATE jobs SET owner='bob' WHERE id=?",(job,))
        if mutation=='unclean':db.execute('UPDATE jobs SET cleaned=0 WHERE id=?',(job,))
    if mutation=='native-failed':rows[0]['status']='FAILED_DRIVER'
    if mutation=='native-pending':
        rows[0]['status']='RUNNING'
        assert release_probe.probe(SHA,'alice',selected,native=True,sky=sky,worker_image=image)['native_succeeded'] is False
        return
    if mutation=='missing-cluster':status['name']='qs-'+'f'*20
    if mutation=='duplicate-selection':selected['lindblad_spectrum']=selected['josim_sfq']
    if mutation=='extra-case':selected['arbitrary']=selected.pop('lindblad_spectrum')
    if mutation=='partial-selection':selected.pop('lindblad_spectrum')
    with pytest.raises((ValueError,KeyError)):
        release_probe.probe(SHA,'alice',selected,native=True,sky=sky,worker_image=image)


def test_physics_candidate_is_restricted_to_smoke_owner_until_drained_open(client,monkeypatch):
    monkeypatch.setenv('QSIM_PHYSICS_ENABLED','1')
    monkeypatch.setattr(settings,'WORKER_IMAGE','registry.example.invalid/worker@sha256:'+'b'*64)
    release_control.control('hold',SHA)
    bodies=[acceptance.cases()[key] for key in ('lindblad_spectrum','scqubits_spectrum','josim_sfq')]
    for body in bodies:
        assert client.post('/api/jobs',headers=auth('bob'),json=body).status_code==503
    release_control.control('smoke',SHA,'alice')
    for body in bodies:
        assert client.post('/api/jobs',headers=auth('bob'),json=body).status_code==503
        assert client.post('/api/jobs',headers=auth(),json=body).status_code==202
    with pytest.raises(ValueError):release_control.control('open',SHA)
    # Admission-protocol fixture only; real math/native cleanup is proved separately.
    with store.connect() as db:db.execute("UPDATE jobs SET status='SUCCEEDED',cleaned=1")
    release_control.control('open',SHA)
    assert client.post('/api/jobs',headers=auth('bob'),json=bodies[0]).status_code==202
