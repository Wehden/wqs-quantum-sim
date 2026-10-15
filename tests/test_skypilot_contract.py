"""Real installed SDK contracts, without provisioning or contacting an API server."""
import copy
import inspect
import json
import sky
from qsim import controller, settings


def test_real_task_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'DATA', tmp_path)
    monkeypatch.setattr(settings, 'WORKER_IMAGE', 'registry.example.invalid/worker@sha256:' + 'a'*64)
    (tmp_path/'job').mkdir()
    (tmp_path/'job'/'callback-token').write_text('synthetic-test-token')
    config = controller.task_config({'id': 'job'})
    task = sky.Task.from_yaml_config(copy.deepcopy(config))
    exported = task.to_yaml_config()
    assert exported['run'] == config['run']
    assert exported['envs'] == config['envs']
    assert 'docker:' + settings.WORKER_IMAGE in json.dumps(exported)


def test_used_sdk_signatures_and_errors():
    for name, args, kwargs in [
        ('launch', (None,), {'cluster_name': 'test', 'idle_minutes_to_autostop': 10, 'down': True}),
        ('exec', (None,), {'cluster_name': 'test'}),
        ('down', ('test',), {}),
        ('api_cancel', ('request',), {'silent': True}),
        ('api_status', (), {'request_ids': ['request'], 'fields': ['request_id', 'status']}),
        ('api_status', (), {'all_status': True, 'cluster_name': 'test'}),
        ('get', ('request',), {}),
    ]:
        inspect.signature(getattr(sky, name)).bind(*args, **kwargs)
    for name in ('ClusterDoesNotExist', 'ClusterNotUpError', 'ResourcesUnavailableError'):
        assert issubclass(getattr(sky.exceptions, name), Exception)
