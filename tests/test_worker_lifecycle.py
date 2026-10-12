"""Worker completion remains independent of elapsed simulation time."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
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
