"""Worker completion remains independent of elapsed simulation time."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from qsim import worker


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
