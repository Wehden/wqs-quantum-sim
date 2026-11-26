"""Run inside the exact API/controller image as its configured non-root user."""
import inspect
import os
from pathlib import Path
import sys

assert os.getuid() != 0
from qsim import api, adminweb, settings, store
store.init()
assert api.health() == {'status': 'ok'}
assert not {'clifft', 'qsimcirq', 'qiskit_aer'} & sys.modules.keys()
if sys.argv[1] == 'controller':
    import sky
    from sky.utils.common import StatusRefreshMode
    from qsim import controller, observability
    path = settings.DATA / 'smoke'
    path.mkdir()
    (path / 'callback-token').write_text('synthetic')
    task = sky.Task.from_yaml_config(controller.task_config({'id': 'smoke'}))
    assert task.to_yaml_config()['run'].endswith('-m qsim.worker')
    inspect.signature(sky.launch).bind(task, cluster_name='smoke', idle_minutes_to_autostop=10, down=True)
    inspect.signature(sky.exec).bind(task, cluster_name='smoke')
    inspect.signature(sky.queue).bind('smoke', skip_finished=False, all_users=True)
    inspect.signature(sky.status).bind(cluster_names=['smoke'], refresh=StatusRefreshMode.FORCE, all_users=True)
    inspect.signature(sky.down).bind('smoke')
print('Image imports, database initialization, health and SDK smoke passed')
