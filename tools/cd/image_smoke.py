"""Run inside the exact API/controller image as its configured non-root user."""
from importlib.metadata import version
import inspect
import os
import socket
import subprocess
from pathlib import Path
import sys
import time

import httpx

assert os.getuid() != 0
from qsim import api, adminweb, settings, store
store.init()
assert api.health() == {'status': 'ok'}
assert not {'clifft', 'qsimcirq', 'qiskit_aer'} & sys.modules.keys()
if sys.argv[1] == 'api':
    # Exercise the installed ASGI server in the published image. Its only
    # network connection is the container's loopback health request.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        address = f'http://127.0.0.1:{listener.getsockname()[1]}/healthz'
        process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'qsim.api:app', '--fd',
             str(listener.fileno()), '--no-access-log'],
            pass_fds=(listener.fileno(),),
        )
    try:
        deadline = time.monotonic() + 10
        with httpx.Client(timeout=1, trust_env=False) as client:
            while time.monotonic() < deadline:
                assert process.poll() is None, 'Uvicorn exited before the health check'
                try:
                    response = client.get(address)
                except httpx.TransportError:
                    time.sleep(0.05)
                    continue
                assert response.status_code == 200 and response.json() == {'status': 'ok'}
                break
            else:
                raise AssertionError('Uvicorn health check timed out')
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
else:
    assert sys.argv[1] == 'controller'
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
print(f'{sys.argv[1]} image smoke passed with Uvicorn {version("uvicorn")}')
