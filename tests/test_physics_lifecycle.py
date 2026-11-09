"""Joined real HTTP/native-worker tests; only SkyPilot placement is synthetic.

Run mandatorily with WQS_JOINED_WORKER_IMAGE and WQS_JOINED_WORKER_REF after
building the exact worker. These tests never allocate a cluster or GPU. Docker
uses the inspected local image ID; the synthetic scheduler ref is not registry
or live-SkyPilot attestation. The bounded waits are harness deadlines only.
"""
import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading
from types import SimpleNamespace
import uuid

import httpx
import numpy as np
import pytest

from qsim import controller, settings, store
from test_physics_backup import assert_physics_restore, backup


class DockerPlacement:
    """Translate the approved task into a disposable local CPU worker."""
    exceptions = SimpleNamespace(ResourcesUnavailableError=type('CapacityError',(Exception,),{}),
        ClusterNotUpError=type('NotUpError',(Exception,),{}),
        ClusterDoesNotExist=type('AbsentError',(Exception,),{}))

    class Task:
        @staticmethod
        def from_yaml_config(config):
            import sky
            actual=sky.Task.from_yaml_config(copy.deepcopy(config))
            exported=actual.to_yaml_config()
            assert exported['run']==config['run'] and exported['envs']==config['envs']
            return config

    def __init__(self, image_id, scheduler_ref):
        self.image_id=image_id
        self.scheduler_ref=scheduler_ref
        self.requests={}
        self.results={}
        self.native={}
        self.placements=[]
        self.containers=[]
        self.output=[]
        self.job_tokens={}
        self.tamper=None
        self.callback_relay=None

    def send(self,kind,cluster):
        identifier=str(len(self.requests)+1)
        self.requests[identifier]=SimpleNamespace(request_id=identifier,name='sky.'+kind,
            created_at=time.time(),cluster_name=cluster,status=SimpleNamespace(value='SUCCEEDED'))
        return identifier

    def execute(self,kind,task,cluster):
        request_id=self.send(kind,cluster)
        resources=task['resources']
        assert resources['image_id']=='docker:'+self.scheduler_ref
        assert float(resources['cpus'])==2 and float(resources['memory'])==4
        assert set(resources)=={'infra','cpus','memory','image_id','labels'}
        assert resources['infra']=='k8s' and 'accelerators' not in resources
        env=dict(task['envs'])
        physics='QSIM_EXECUTION_CONTEXT' in env
        if physics:
            assert 'sudo -n -H -u qsim' in task['run']
            context=json.loads(env['QSIM_EXECUTION_CONTEXT'])
            assert context['worker_profile']['image']==self.scheduler_ref
            assert context['worker_profile']['isolation']['uid']==1000
            if self.tamper is not None:
                self.tamper(context)
                env['QSIM_EXECUTION_CONTEXT']=json.dumps(context)
        else:
            assert task['run']=='cd /opt/qsim && /opt/qsim-venv/bin/python -m qsim.worker'
        identifier=env['QSIM_JOB_URL'].rsplit('/',1)[1]
        self.job_tokens[identifier]=env['QSIM_JOB_TOKEN']
        with store.connect() as db:
            row=db.execute('SELECT * FROM job_dispatch WHERE job_id=?',(identifier,)).fetchone()
            assert row['intent']=='launch' and row['cluster']==cluster
            if physics and self.tamper is None:
                assert json.loads(row['profile_json'])==json.loads(env['QSIM_EXECUTION_CONTEXT'])
        if self.callback_relay is not None:
            env['QSIM_JOB_URL']=self.callback_relay+'/internal/jobs/'+identifier
        native_id=len(self.native.get(cluster,[]))+1
        name='wqs-joined-'+uuid.uuid4().hex[:16]
        self.containers.append(name)
        command=['docker','run','--rm','--pull=never','--name',name,'--network','host',
            '--user','1000:1000','--cpus','2','--memory','4g','--memory-swap','4g',
            '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777',
            '--tmpfs','/home/qsim:rw,nosuid,nodev,size=16m,uid=1000,gid=1000,mode=700',
            '--env','HOME=/home/qsim','--env','PYTHONDONTWRITEBYTECODE=1',
            '--env','OPENBLAS_NUM_THREADS=1','--env','OMP_NUM_THREADS=1','--env','MKL_NUM_THREADS=1']
        assert not any(arg in command for arg in ('--mount','--volume','-v'))
        for key,value in env.items():
            assert key in ('QSIM_JOB_URL','QSIM_JOB_TOKEN','QSIM_EXECUTION_CONTEXT')
            command.extend(['--env',key+'='+value])
        command.extend(['--entrypoint','/opt/qsim-venv/bin/python',self.image_id,'-m','qsim.worker'])
        process=subprocess.run(command,capture_output=True,timeout=120)
        self.output.append((process.stdout+process.stderr).decode(errors='replace'))
        self.placements.append(dict(kind=kind,cluster=cluster,job_id=identifier,
            scheduler_ref=self.scheduler_ref,container_image_id=self.image_id,returncode=process.returncode))
        self.native.setdefault(cluster,[]).append({'job_id':native_id,
            'status':'SUCCEEDED' if process.returncode==0 else 'FAILED'})
        self.results[request_id]=(native_id,None)
        return request_id

    def launch(self, task, cluster_name, **kwargs):
        assert kwargs=={'idle_minutes_to_autostop':10,'down':True}
        return self.execute('launch',task,cluster_name)

    def exec(self, task, cluster_name):
        return self.execute('exec',task,cluster_name)

    def queue(self, cluster, **kwargs):
        assert kwargs=={'skip_finished':False,'all_users':True}
        identifier=self.send('queue',cluster)
        self.results[identifier]=copy.deepcopy(self.native.get(cluster,[]))
        return identifier

    def down(self, cluster):
        identifier=self.send('down',cluster)
        self.results[identifier]=None
        return identifier

    def api_status(self, request_ids=None, cluster_name=None, **kwargs):
        return [request for request in self.requests.values()
            if (not request_ids or request.request_id in request_ids)
            and (not cluster_name or request.cluster_name==cluster_name)]

    def get(self, request_id):
        return self.results.get(request_id)

    def api_cancel(self, request_id, **kwargs):
        self.requests[request_id].status.value='CANCELLED'
        identifier=self.send('cancel',self.requests[request_id].cluster_name)
        self.results[identifier]=None
        return identifier


@pytest.fixture
def joined(tmp_path,monkeypatch,record_property):
    image=os.environ.get('WQS_JOINED_WORKER_IMAGE')
    if not image:
        pytest.skip('Mandatory built-worker integration uses WQS_JOINED_WORKER_IMAGE and WQS_JOINED_WORKER_REF')
    if not sys.platform.startswith('linux'):
        pytest.fail('Joined Docker host-network harness requires Linux')
    scheduler_ref=os.environ.get('WQS_JOINED_WORKER_REF','')
    assert '@sha256:' in scheduler_ref, 'Explicit synthetic immutable scheduler ref required'
    inspected=subprocess.run(['docker','image','inspect',image,'--format','{{.Id}}'],
                             check=True,capture_output=True,text=True)
    image_id=inspected.stdout.strip()
    assert image_id.startswith('sha256:') and len(image_id)==71
    record_property('joined_container_image_id',image_id)
    record_property('joined_synthetic_scheduler_ref',scheduler_ref)
    record_property('joined_placement','synthetic SkyPilot placement; real Docker native execution')
    data=tmp_path/'data';data.mkdir(mode=0o700)
    token='synthetic-joined-owner-'+uuid.uuid4().hex
    other='synthetic-joined-other-'+uuid.uuid4().hex
    registry=data/'users.json'
    registry.write_text(json.dumps([{'id':name,'sha256':hashlib.sha256(value.encode()).hexdigest(),'active':True}
        for name,value in [('joined-owner',token),('other-owner',other)]]))
    home=tmp_path/'api-home';home.mkdir(mode=0o700)
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
    base='http://127.0.0.1:'+str(listener.getsockname()[1])
    root=Path(__file__).resolve().parents[1]
    env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(home),'PYTHONPATH':str(root),
         'PYTHONDONTWRITEBYTECODE':'1','QSIM_DATA':str(data),'QSIM_TOKEN_FILE':str(registry),
         'QSIM_WORKER_IMAGE':scheduler_ref,'QSIM_PHYSICS_ENABLED':'1','QSIM_SKY_INFRA':'k8s',
         'QSIM_CALLBACK_URL':base,'QSIM_PUBLIC_URL':base,'LANG':'C.UTF-8','TZ':'UTC',
         'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    api_log=(tmp_path/'api.log').open('w+')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','qsim.api:app','--fd',str(listener.fileno()),
        '--no-access-log','--log-level','warning'],cwd=root,env=env,pass_fds=(listener.fileno(),),
        stdout=api_log,stderr=subprocess.STDOUT)
    listener.close()
    transport=DockerPlacement(image_id,scheduler_ref)
    monkeypatch.setattr(settings,'DATA',data)
    monkeypatch.setattr(settings,'TOKEN_FILE',registry)
    monkeypatch.setattr(settings,'WORKER_IMAGE',scheduler_ref)
    monkeypatch.setattr(settings,'CALLBACK_URL',base)
    monkeypatch.setenv('QSIM_SKY_INFRA','k8s')
    monkeypatch.setattr(controller,'sky_client',lambda:transport)
    client=httpx.Client(base_url=base,headers={'Authorization':'Bearer '+token},timeout=15)
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            assert process.poll() is None, 'Joined API process exited before readiness'
            try:
                if client.get('/readyz').status_code==200:break
            except httpx.TransportError:pass
            time.sleep(.05)
        else:pytest.fail('Joined API process never became ready')
        yield SimpleNamespace(client=client,sky=transport,data=data,token=token,other=other,api_log=api_log)
    finally:
        record_property('joined_successful_native_processes',sum(p['returncode']==0 for p in transport.placements))
        client.close()
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=5)
        api_log.close()
        for name in transport.containers:
            subprocess.run(['docker','rm','--force',name],capture_output=True,timeout=10)


def complete(joined,engine,body,*,expect='SUCCEEDED',key=None):
    response=joined.client.post('/api/jobs' if engine is None else '/api/jobs/'+engine,
        headers={'Idempotency-Key':key or uuid.uuid4().hex},json=body)
    assert response.status_code==202,response.text
    identifier=response.json()['id']
    for _ in range(8):
        controller.tick()
        with store.connect() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(identifier,)).fetchone()
        if row['status'] in settings.TERMINAL and row['cleaned']:break
    diagnostic='\n'.join(joined.sky.output).replace(joined.token,'<synthetic-token>').replace(joined.other,'<synthetic-token>')[-4096:]
    assert row['status']==expect,(dict(row),diagnostic)
    assert row['cleaned']==1
    assert not (joined.data/identifier/'callback-token').exists()
    return identifier


def arrays(joined,identifier):
    response=joined.client.get('/api/jobs/'+identifier+'/result')
    assert response.status_code==200,response.text
    with np.load(io.BytesIO(response.content),allow_pickle=False) as archive:
        result={name:archive[name].copy() for name in archive.files}
    assert all(not value.dtype.hasobject for value in result.values())
    metadata=json.loads(str(result.pop('metadata_json')))
    assert joined.token not in json.dumps(metadata) and joined.other not in json.dumps(metadata)
    assert joined.token not in ''.join(joined.sky.output)
    assert joined.other not in ''.join(joined.sky.output)
    for token in joined.sky.job_tokens.values():
        assert token not in json.dumps(metadata) and token not in ''.join(joined.sky.output)
    joined.api_log.flush();joined.api_log.seek(0)
    api_log=joined.api_log.read()
    assert joined.token not in api_log and joined.other not in api_log
    assert all(token not in api_log for token in joined.sky.job_tokens.values())
    return result,metadata


def lindblad(operation='evolve',solver='se'):
    body={'schema_version':1,'operation':operation,
          'model':{'modes':[{'name':'q','kind':'oscillator','dimension':2,'frequency':.017}]}}
    if operation in ('evolve','propagator'):body.update(solver=solver,times=[0.,.5,1.])
    if operation=='evolve':body.update(initial_state={'kind':'product','modes':{'q':{'kind':'fock','index':1}}},
        observables=[{'name':'n','operator':{'kind':'product','factors':{'q':'n'}}}],store_states='all')
    if solver in ('me','mc') or operation=='steadystate':
        body['model']['collapse']=[{'kind':'relaxation','mode':'q','T1':3.}]
    if solver=='mc':body.update(trajectories=8,seed=42)
    if operation=='spectrum':body.update(evals_count=2,return_vectors=True)
    if operation=='steadystate':body.update(return_state=True,
        observables=[{'name':'n','operator':{'kind':'product','factors':{'q':'n'}}}])
    return body


@pytest.mark.parametrize('operation,solver',[
    ('evolve','se'),('evolve','me'),('evolve','mc'),('propagator','se'),('propagator','me'),
    ('spectrum','se'),('steadystate','se')])
def test_joined_lindblad_operation_families(joined,operation,solver):
    body=lindblad(operation,solver)
    key='joined-'+operation+'-'+solver
    identifier=complete(joined,'lindblad',body,key=key)
    replay=joined.client.post('/api/jobs',headers={'Idempotency-Key':key},
        json={**body,'engine':'lindblad','input_format':'lindblad_model'})
    assert replay.status_code==202 and replay.json()['id']==identifier
    assert replay.headers['Idempotency-Replayed']=='true'
    result,metadata=arrays(joined,identifier)
    assert metadata['engine']=='lindblad' and metadata['backend']=='cpu'
    assert all(np.isfinite(value).all() for value in result.values())
    if operation=='evolve':
        np.testing.assert_allclose(result['times'],[0.,.5,1.])
        if solver=='se':
            np.testing.assert_allclose(result['expectations'][:,0],1.,atol=1e-7)
            np.testing.assert_allclose(result['states'][:,1],np.exp(-2j*np.pi*.017*result['times']),atol=1e-7)
        elif solver=='me':
            np.testing.assert_allclose(result['expectations'][:,0],np.exp(-result['times']/3),atol=1e-7)
        else:
            repeated,_=arrays(joined,complete(joined,'lindblad',body))
            for name in result:np.testing.assert_array_equal(result[name],repeated[name])
        if solver in ('me','mc'):
            np.testing.assert_allclose(np.trace(result['states'],axis1=1,axis2=2),1.,atol=1e-7)
    elif operation=='spectrum':np.testing.assert_allclose(result['eigenvalues'],[0.,.017],atol=1e-12)
    elif operation=='steadystate':
        np.testing.assert_allclose(result['steady_state'],[[1.,0.],[0.,0.]],atol=1e-10)
    else:np.testing.assert_allclose(result['propagators'][0],np.eye(2 if solver=='se' else 4),atol=1e-10)
    events=joined.client.get('/api/jobs/'+identifier+'/events').text
    assert 'event: result' in events and '"status":"SUCCEEDED"' in events
    resumed=joined.client.get('/api/jobs/'+identifier+'/events',headers={'Last-Event-ID':'1'}).text
    assert 'id: 1\n' not in resumed and 'event: result' in resumed
    assert joined.token not in events
    assert joined.client.get('/api/jobs/'+identifier,headers={'Authorization':'Bearer '+joined.other}).status_code==404
    if operation=='evolve' and solver=='se':
        terminal=joined.client.get('/api/jobs/'+identifier).json()
        downloaded=joined.client.get('/api/jobs/'+identifier+'/result').content
        callback_headers={'Authorization':'Bearer '+joined.sky.job_tokens[identifier]}
        repeated=joined.client.post('/internal/jobs/'+identifier,headers=callback_headers,
            json={'status':'SUCCEEDED','summary':terminal['summary'],
                  'artifact':base64.b64encode(downloaded).decode()})
        assert repeated.status_code==200
        assert joined.client.post('/internal/jobs/'+identifier,headers=callback_headers,
            json={'status':'FAILED','error':'synthetic late failure'}).status_code==409
        assert joined.client.get('/api/jobs/'+identifier).json()==terminal


SCQ_PARAMS={
    'Transmon':{'EJ':15.,'EC':.3,'ng':0.,'ncut':30},
    'TunableTransmon':{'EJmax':20.,'EC':.3,'d':.01,'flux':.2,'ng':0.,'ncut':30},
    'Fluxonium':{'EJ':8.9,'EC':2.5,'EL':.5,'flux':.3,'cutoff':110},
    'FluxQubit':{'EJ1':1.,'EJ2':1.,'EJ3':.8,'ECJ1':.016,'ECJ2':.016,'ECJ3':.021,
                 'ECg1':.83,'ECg2':.83,'ng1':0.,'ng2':0.,'flux':.4,'ncut':5},
}


@pytest.mark.parametrize('qubit',SCQ_PARAMS)
def test_joined_scqubits_native_spectrum_and_matrix_elements(joined,qubit):
    body={'schema_version':1,'operation':'spectrum','evals_count':4,'return_vectors':True,
          'model':{'qubit':qubit,'params':SCQ_PARAMS[qubit]}}
    spectrum,metadata=arrays(joined,complete(joined,'scqubits',body))
    assert metadata['engine']=='scqubits' and metadata['units']['eigenvalues']=='GHz'
    values=spectrum['eigenvalues'];vectors=spectrum['eigenvectors']
    assert values.shape==(4,) and np.all(np.diff(values)>=0)
    np.testing.assert_allclose(vectors.conj().T@vectors,np.eye(4),atol=1e-9)
    body.pop('return_vectors')
    body.update(operation='matrix_elements',operator='n_1_operator' if qubit=='FluxQubit' else 'n_operator')
    transformed,_=arrays(joined,complete(joined,'scqubits',body))
    np.testing.assert_allclose(transformed['eigenvalues'],values,atol=1e-9,rtol=0)
    matrix=transformed['matrix_elements']
    assert matrix.shape==(4,4) and np.isfinite(matrix).all()
    np.testing.assert_allclose(matrix,matrix.conj().T,atol=1e-12)
    assert joined.sky.placements[-1]['kind']=='exec'


@pytest.mark.parametrize('qubit',SCQ_PARAMS)
def test_joined_scqubits_export_to_lindblad_is_a_client_sequence(joined,qubit):
    operator='n_1_operator' if qubit=='FluxQubit' else 'n_operator'
    body={'schema_version':1,'operation':'export_for_lindblad','evals_count':4,
          'model':{'qubit':qubit,'params':SCQ_PARAMS[qubit]},'operators':[operator]}
    exported,metadata=arrays(joined,complete(joined,'scqubits',body))
    assert metadata['engine']=='scqubits' and metadata['energy_reference']=='ground'
    assert exported['eigenvalues'][0]==0
    np.testing.assert_array_equal(exported['hamiltonian'],np.diag(exported['eigenvalues']))
    handoff=metadata['lindblad_request']
    result,_=arrays(joined,complete(joined,'lindblad',handoff))
    np.testing.assert_allclose(result['eigenvalues'],exported['eigenvalues'],atol=1e-9,rtol=0)
    assert len(joined.sky.placements)==2
    assert joined.sky.placements[-1]['kind']=='exec'


def test_joined_josim_sfq_and_circuit_pool_separation(joined):
    netlist=(Path(__file__).parent/'fixtures/physics/josim/sfq.cir').read_text()
    identifier=complete(joined,'josim',{'schema_version':1,'netlist':netlist})
    result,metadata=arrays(joined,identifier)
    assert metadata['engine']=='josim'
    assert result['traces'].shape==(999,5)
    times=result['times'];window=(times>=50e-12)&(times<=200e-12)
    for column in (0,2):
        assert abs(np.trapezoid(result['traces'][window,column],times[window])/2.0678338484619295e-15-1)<.02
    response=joined.client.post('/api/jobs/stim',headers={'Idempotency-Key':uuid.uuid4().hex},
        json={'circuit':'X 0\nM 0','shots':16})
    assert response.status_code==202
    circuit=response.json()['id']
    for _ in range(5):controller.tick()
    state=joined.client.get('/api/jobs/'+circuit).json()
    assert state['status']=='SUCCEEDED' and state['summary']['counts']=={'1':16}
    assert joined.sky.placements[-1]['cluster']!=joined.sky.placements[0]['cluster']


@pytest.mark.parametrize('field',['allocation_plan_digest','algorithm','admission_policy_revision','image'])
def test_joined_launch_context_tamper_is_not_native_execution(joined,field):
    def tamper(context):
        if field=='image':context['worker_profile']['image']='registry.example.invalid/wrong@sha256:'+'f'*64
        else:context[field]='changed'
    joined.sky.tamper=tamper
    identifier=complete(joined,'scqubits',{'schema_version':1,'operation':'spectrum','evals_count':4,
        'model':{'qubit':'Transmon','params':SCQ_PARAMS['Transmon']}},expect='FAILED')
    row=joined.client.get('/api/jobs/'+identifier).json()
    assert row['error'].startswith('execution_policy_mismatch:')
    assert joined.client.get('/api/jobs/'+identifier+'/result').status_code==409
    events=joined.client.get('/api/jobs/'+identifier+'/events').text
    assert '"status":"RUNNING"' not in events and '"status":"SUCCEEDED"' not in events
    assert joined.sky.placements[0]['returncode']!=0


def test_joined_lost_final_http_callback_is_reconciled_without_rerun(joined):
    observed={'gets':0,'running':0,'final_attempts':0}
    target=str(joined.client.base_url).rstrip('/')

    class Relay(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def forward(self,body=None):
            headers={'Authorization':self.headers.get('Authorization','')}
            if body is not None:headers['Content-Type']='application/json'
            response=httpx.request(self.command,target+self.path,headers=headers,content=body,timeout=15)
            self.send_response(response.status_code)
            self.send_header('Content-Type',response.headers.get('Content-Type','application/json'))
            self.send_header('Content-Length',str(len(response.content)))
            self.end_headers();self.wfile.write(response.content)

        def do_GET(self):
            observed['gets']+=1
            self.forward()

        def do_POST(self):
            size=int(self.headers.get('Content-Length','0'))
            assert 0<size<16*1024**2
            body=self.rfile.read(size)
            payload=json.loads(body)
            if payload.get('status')=='RUNNING':
                observed['running']+=1
                self.forward(body)
            else:
                # Native computation and auth are real. Only final HTTP delivery
                # is refused, leaving the API without a committed terminal event.
                assert payload.get('status')=='SUCCEEDED'
                observed['final_attempts']+=1
                self.send_response(503);self.send_header('Content-Length','0');self.end_headers()

    relay=ThreadingHTTPServer(('127.0.0.1',0),Relay)
    thread=threading.Thread(target=relay.serve_forever,daemon=True);thread.start()
    joined.sky.callback_relay='http://127.0.0.1:'+str(relay.server_port)
    try:
        identifier=complete(joined,'scqubits',{'schema_version':1,'operation':'spectrum','evals_count':4,
            'model':{'qubit':'Transmon','params':SCQ_PARAMS['Transmon']}},expect='FAILED')
    finally:
        joined.sky.callback_relay=None
        relay.shutdown();relay.server_close();thread.join(timeout=5)
    assert observed=={'gets':1,'running':1,'final_attempts':5}
    assert len(joined.sky.placements)==1
    assert joined.sky.placements[0]['returncode']!=0
    assert joined.sky.native[joined.sky.placements[0]['cluster']]==[{'job_id':1,'status':'FAILED'}]
    state=joined.client.get('/api/jobs/'+identifier).json()
    assert state['error']=='Native physics job ended without a committed result'
    assert state['summary'] is None and not (joined.data/identifier/'result.npz').exists()
    assert joined.client.get('/api/jobs/'+identifier+'/result').status_code==409
    events=joined.client.get('/api/jobs/'+identifier+'/events').text
    assert '"status":"RUNNING"' in events and '"status":"FAILED"' in events
    assert 'event: result' not in events and 'event: terminal' in events
    assert joined.token not in events and joined.token not in ''.join(joined.sky.output)
    assert all(token not in events and token not in ''.join(joined.sky.output)
               for token in joined.sky.job_tokens.values())


def test_joined_agreeing_launch_and_store_cannot_forge_allocation_plan(joined):
    response=joined.client.post('/api/jobs/scqubits',headers={'Idempotency-Key':uuid.uuid4().hex},
        json={'schema_version':1,'operation':'spectrum','evals_count':4,
              'model':{'qubit':'Transmon','params':SCQ_PARAMS['Transmon']}})
    assert response.status_code==202,response.text
    identifier=response.json()['id']
    with store.connect() as db:
        context=json.loads(db.execute('SELECT profile_json FROM job_dispatch WHERE job_id=?',(identifier,)).fetchone()[0])
        context['allocation_plan_digest']='f'*64
        db.execute('UPDATE job_dispatch SET profile_json=? WHERE job_id=?',(json.dumps(context),identifier))
    # Stored and launched contexts now agree, but neither matches the real
    # recomputed admission plan. No production validator or numerical path is patched.
    for _ in range(8):controller.tick()
    state=joined.client.get('/api/jobs/'+identifier).json()
    assert state['status']=='FAILED' and state['error'].startswith('execution_policy_mismatch:')
    assert joined.client.get('/api/jobs/'+identifier+'/result').status_code==409
    events=joined.client.get('/api/jobs/'+identifier+'/events').text
    assert '"status":"RUNNING"' not in events and 'event: result' not in events
    with store.connect() as db:
        assert db.execute('SELECT cleaned FROM jobs WHERE id=?',(identifier,)).fetchone()[0]==1
    assert len(joined.sky.placements)==1 and joined.sky.placements[0]['returncode']!=0


@pytest.mark.parametrize('backup',[True],indirect=True,ids=['pending-invitation'])
def test_joined_native_lindblad_scqubits_josim_results_restore_with_invitation(backup,joined,tmp_path,monkeypatch):
    import shutil
    assert shutil.which('age') and shutil.which('age-keygen'), 'Joined native recovery requires actual age binaries'
    # Fixture setup order is immaterial: the API subprocess always owns joined
    # DATA, and the parent controller is explicitly pointed there until all
    # actual native jobs finish. Backup source is selected only afterwards.
    monkeypatch.setattr(settings,'DATA',joined.data)
    bodies=[('lindblad',lindblad('evolve','se')),
        ('scqubits',{'schema_version':1,'operation':'matrix_elements','evals_count':4,
                    'model':{'qubit':'Transmon','params':SCQ_PARAMS['Transmon']},'operator':'n_operator'}),
        ('josim',{'schema_version':1,'netlist':(Path(__file__).parent/'fixtures/physics/josim/sfq.cir').read_text()})]
    bodies[0][1]['name']='Native Ω ψ backup'
    bodies[0][1]['observables'][0]['name']='number Ω ψ'
    records=[]
    for engine,body in bodies:
        identifier=complete(joined,engine,body)
        downloaded=joined.client.get('/api/jobs/'+identifier+'/result')
        assert downloaded.status_code==200
        with store.connect() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(identifier,)).fetchone()
            context=db.execute('SELECT profile_json FROM job_dispatch WHERE job_id=?',(identifier,)).fetchone()[0]
        assert row['status']=='SUCCEEDED' and row['cleaned']==1
        records.append({'id':identifier,'request':json.loads(row['request']),
            'context':json.loads(context),'summary':json.loads(row['summary']),'artifact':downloaded.content})
    assert {record['request']['engine'] for record in records}=={'lindblad','scqubits','josim'}
    with np.load(io.BytesIO(records[0]['artifact']),allow_pickle=False) as native:
        assert native['states'].dtype==np.dtype('complex128')
        assert np.any(np.abs(native['states'].imag)>0)
        assert native['metadata_json'].dtype.kind=='U'
        assert 'Ω ψ' in str(native['metadata_json'])
    source=backup[0]
    assert source.resolve()!=joined.data.resolve()
    restore_work=tmp_path/'native-restore';restore_work.mkdir()
    with monkeypatch.context() as restore_settings:
        restore_settings.setattr(settings,'DATA',source)
        destinations,_=assert_physics_restore(backup,restore_work,records)
    assert settings.DATA==joined.data
    assert Path(destinations['data_dir']).resolve()!=joined.data.resolve()
    # The production backup helper verifies exact artifact bytes/arrays, request,
    # summary, durable context and reservations, plus pending invitation decryptability.
    for record in records:
        restored=(Path(destinations['data_dir'])/record['id']/'result.npz').read_bytes()
        assert restored==record['artifact']
