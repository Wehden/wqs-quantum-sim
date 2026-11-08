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


def test_real_physics_task_uses_persisted_image_and_cpu_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'DATA', tmp_path)
    image = 'registry.example.invalid/worker@sha256:' + 'a'*64
    monkeypatch.setattr(settings,'WORKER_IMAGE',image)
    profile = settings.physics_execution_profile('scqubits','spectrum',
        admission_policy_revision='scqubits-cpu-evr-v1')
    context = dict(version=1,worker_profile=profile,admission_policy_revision='scqubits-cpu-evr-v1',
                   allocation_plan_digest='b'*64,algorithm='scqubits-evr-v1')
    (tmp_path/'physical').mkdir()
    (tmp_path/'physical'/'callback-token').write_text('synthetic-token')
    monkeypatch.setattr(settings,'WORKER_IMAGE','new-default-must-not-be-used')
    config=controller.task_config(dict(id='physical',request=json.dumps(dict(engine='scqubits',operation='spectrum')),
                                       profile_json=json.dumps(context)))
    task=sky.Task.from_yaml_config(copy.deepcopy(config))
    exported=task.to_yaml_config()
    assert exported['envs']==config['envs']
    assert exported['run']==config['run']
    assert next(iter(task.resources)).cpus=='2'
    assert next(iter(task.resources)).memory=='4'
    assert 'docker:'+image in json.dumps(exported)
    assert 'new-default' not in json.dumps(exported)
