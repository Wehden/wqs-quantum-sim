"""Worker completion remains independent of elapsed simulation time."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from qsim import worker
from test_physics_worker import (host_execution_context, host_runtime_policy,
    installed_provenance_fixture, scqubits)


def test_long_simulation_returns_result_without_execution_timeout(monkeypatch):
    callbacks=[]
    monkeypatch.setenv('QSIM_JOB_URL','http://example.invalid/internal/jobs/test')
    monkeypatch.setenv('QSIM_JOB_TOKEN','test-only')
    def response(request, **kwargs):
        if request.data:
            callbacks.append(json.loads(request.data))
            return io.BytesIO(b'{}')
        return io.BytesIO(b'{"input_format":"stim"}')
    monkeypatch.setattr(worker.urllib.request,'urlopen',response)
    def simulate(args, **kwargs):
        assert kwargs.get('timeout') is None
        Path(args[-1]).write_text(json.dumps({'status':'SUCCEEDED','elapsed_seconds':7200}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(worker.subprocess,'run',simulate)
    assert worker.remote()==0
    assert callbacks==[{'status':'RUNNING'},{'status':'SUCCEEDED','elapsed_seconds':7200}]


def test_native_simulation_keeps_memory_limit_without_time_limit(monkeypatch,tmp_path):
    import signal
    import resource
    limits=[]
    monkeypatch.setattr(resource,'setrlimit',lambda kind,value:limits.append((kind,value)))
    monkeypatch.setattr(signal,'alarm',lambda seconds:(_ for _ in ()).throw(AssertionError('wall alarm')))
    monkeypatch.setattr(worker,'simulate_qsim',lambda request,output:None)
    source=tmp_path/'in.json';source.write_text('{"engine":"qsim"}')
    worker.simulate(source,tmp_path/'out.json')
    assert resource.RLIMIT_CPU not in [kind for kind,value in limits]
    assert resource.RLIMIT_AS in [kind for kind,value in limits]


@pytest.mark.parametrize('input_format,operation,expected', [
    ('qasm2','sample','OpenQASM 2 sampling failed'),
    ('qasm3','sample','OpenQASM 3 sampling failed'),
    ('qasm2','basis_probabilities','use unitary qelib1.inc circuits without classical registers'),
    ('qasm3','basis_probabilities','OpenQASM 3 probability simulation failed'),
])
def test_native_child_failure_guidance_matches_operation(monkeypatch,capsys,input_format,operation,expected):
    callbacks=[]
    monkeypatch.setenv('QSIM_JOB_URL','http://example.invalid/internal/jobs/test')
    monkeypatch.setenv('QSIM_JOB_TOKEN','test-only')
    def response(request,**kwargs):
        if request.data:
            callbacks.append(json.loads(request.data))
            return io.BytesIO(b'{}')
        return io.BytesIO(json.dumps(dict(input_format=input_format,operation=operation)).encode())
    monkeypatch.setattr(worker.urllib.request,'urlopen',response)
    monkeypatch.setattr(worker.subprocess,'run',lambda *args,**kwargs:
        SimpleNamespace(returncode=-9,stderr=b'PRIVATE_NATIVE_CIRCUIT'))
    assert worker.remote()==1
    error=callbacks[-1]['error']
    assert callbacks[-1]['status']=='FAILED'
    assert expected in error
    assert 'service limits' in error.lower()
    if operation=='sample':
        assert 'bitstrings' not in error
        assert 'without classical registers' not in error
        assert 'sampling' in error
    if input_format=='qasm3':
        assert 'bindings' in error
    assert 'PRIVATE_NATIVE_CIRCUIT' not in error
    assert 'PRIVATE_NATIVE_CIRCUIT' not in capsys.readouterr().err


def remote_physics_context(monkeypatch, body=None):
    # Unit-only installed-manifest fixture; native admission and profile checks
    # remain real. Mandatory image tests establish actual manifest provenance.
    body = scqubits() if body is None else body
    context = host_execution_context(body)
    monkeypatch.setenv('QSIM_EXECUTION_CONTEXT',json.dumps(context,sort_keys=True))
    return {'request':body,'execution_context':context}


def test_physics_real_child_gets_no_callback_credentials_and_cleans_scratch(monkeypatch):
    callbacks, scratch = [], []
    monkeypatch.setenv('QSIM_JOB_URL', 'http://example.invalid/internal/jobs/test')
    monkeypatch.setenv('QSIM_JOB_TOKEN', 'TEST_ONLY_PRIVATE_TOKEN')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'TEST_ONLY_PRIVATE_AWS')
    monkeypatch.setenv('PYTHONPATH', '/PRIVATE_UNTRUSTED_IMPORT_PATH')
    body = {'engine': 'scqubits', 'input_format': 'scqubits_model', 'schema_version': 1,
        'operation': 'spectrum', 'evals_count': 2,
        'model': {'qubit': 'Transmon', 'params': {'EJ': 15., 'EC': .3, 'ng': 0., 'ncut': 30}}}
    envelope = remote_physics_context(monkeypatch,body)
    def response(request, **kwargs):
        if request.data:
            callbacks.append(json.loads(request.data))
            return io.BytesIO(b'{}')
        return io.BytesIO(json.dumps(envelope).encode())
    monkeypatch.setattr(worker.urllib.request, 'urlopen', response)
    original = worker.subprocess.run
    def execute(args, **kwargs):
        assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
        assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == worker.subprocess.DEVNULL
        assert 'capture_output' not in kwargs and 'timeout' not in kwargs and 'shell' not in kwargs
        scratch.append(Path(args[-1]).parent)
        assert scratch[-1].stat().st_mode & 0o777 == 0o700
        assert set(json.loads(Path(args[-2]).read_text())) == {'request','execution_context'}
        assert 'TEST_ONLY_PRIVATE' not in Path(args[-2]).read_text()
        code = f'from qsim import worker; worker._runtime_policy = lambda: {host_runtime_policy()!r}\n' + '''
import os,sys,resource
from pathlib import Path
from qsim import worker
assert not any(key in os.environ for key in ('QSIM_JOB_TOKEN','QSIM_JOB_URL','AWS_SECRET_ACCESS_KEY','PYTHONPATH'))
worker.simulate(Path(sys.argv[1]), Path(sys.argv[2]))
assert resource.getrlimit(resource.RLIMIT_AS) == (3*1024**3,)*2
assert resource.getrlimit(resource.RLIMIT_FSIZE) == (12*1024**2,)*2
assert resource.getrlimit(resource.RLIMIT_CORE) == (0,0)
'''
        return original([args[0], '-c', code, *args[-2:]], **kwargs)
    monkeypatch.setattr(worker.subprocess, 'run', execute)
    assert worker.remote() == 0, callbacks
    assert callbacks[0] == {'status': 'RUNNING'}
    assert callbacks[1]['status'] == 'SUCCEEDED'
    assert 'TEST_ONLY_PRIVATE' not in json.dumps(callbacks)
    assert all(not path.exists() for path in scratch)


@pytest.mark.parametrize('failure', ['killed', 'missing', 'malformed', 'spawn'])
def test_physics_child_failures_complete_once_and_clean_scratch(monkeypatch, failure, capsys):
    callbacks, scratch = [], []
    monkeypatch.setenv('QSIM_JOB_URL', 'http://example.invalid/internal/jobs/test')
    monkeypatch.setenv('QSIM_JOB_TOKEN', 'TEST_ONLY_PRIVATE_TOKEN')
    envelope = remote_physics_context(monkeypatch)
    def response(request, **kwargs):
        if request.data:
            callbacks.append(json.loads(request.data))
            return io.BytesIO(b'{}')
        return io.BytesIO(json.dumps(envelope).encode())
    monkeypatch.setattr(worker.urllib.request, 'urlopen', response)
    def execute(args, **kwargs):
        assert 'timeout' not in kwargs
        output = Path(args[-1])
        scratch.append(output.parent)
        if failure == 'spawn':
            raise OSError('PRIVATE_PATH_AND_TOKEN')
        if failure == 'malformed':
            output.write_text('PRIVATE_MODEL_NOT_JSON')
        return SimpleNamespace(returncode=-9 if failure == 'killed' else 0, stderr=b'PRIVATE_NATIVE_LOG')
    monkeypatch.setattr(worker.subprocess, 'run', execute)
    assert worker.remote() == 1
    assert callbacks[0] == {'status': 'RUNNING'}
    assert callbacks[1]['status'] == 'FAILED'
    assert len(callbacks) == 2
    assert 'PRIVATE' not in json.dumps(callbacks)+capsys.readouterr().err
    assert all(not path.exists() for path in scratch)


def test_physics_cancellation_propagates_without_false_completion(monkeypatch):
    callbacks, scratch = [], []
    monkeypatch.setenv('QSIM_JOB_URL', 'http://example.invalid/internal/jobs/test')
    monkeypatch.setenv('QSIM_JOB_TOKEN', 'test-only')
    envelope = remote_physics_context(monkeypatch)
    def response(request, **kwargs):
        if request.data:
            callbacks.append(json.loads(request.data))
            return io.BytesIO(b'{}')
        return io.BytesIO(json.dumps(envelope).encode())
    monkeypatch.setattr(worker.urllib.request, 'urlopen', response)
    def interrupted(args, **kwargs):
        scratch.append(Path(args[-1]).parent)
        raise KeyboardInterrupt
    monkeypatch.setattr(worker.subprocess, 'run', interrupted)
    with pytest.raises(KeyboardInterrupt):
        worker.remote()
    assert callbacks == [{'status': 'RUNNING'}]
    assert all(not path.exists() for path in scratch)


@pytest.mark.parametrize('mismatch',['missing_launch','changed_launch','changed_download'])
def test_physics_context_disagreement_refuses_before_running_or_spawn(monkeypatch,mismatch):
    callbacks=[]
    monkeypatch.setenv('QSIM_JOB_URL','http://example.invalid/internal/jobs/test')
    monkeypatch.setenv('QSIM_JOB_TOKEN','test-only')
    envelope=remote_physics_context(monkeypatch)
    if mismatch=='missing_launch':
        monkeypatch.delenv('QSIM_EXECUTION_CONTEXT')
    elif mismatch=='changed_launch':
        launched=json.loads(json.dumps(envelope['execution_context']))
        launched['allocation_plan_digest']='0'*64
        monkeypatch.setenv('QSIM_EXECUTION_CONTEXT',json.dumps(launched))
    else:
        envelope['execution_context']['allocation_plan_digest']='0'*64
    def response(request,**kwargs):
        if request.data:
            callbacks.append(json.loads(request.data))
            return io.BytesIO(b'{}')
        return io.BytesIO(json.dumps(envelope).encode())
    monkeypatch.setattr(worker.urllib.request,'urlopen',response)
    monkeypatch.setattr(worker.subprocess,'run',lambda *a,**k:pytest.fail('unowned native context executed'))
    assert worker.remote()==1
    assert len(callbacks)==1 and callbacks[0]['status']=='FAILED'
    assert callbacks[0]['error'].startswith('execution_policy_mismatch:')
