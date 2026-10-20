import copy
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from test_backup_snapshot import source, SHA
from test_backup_restore import identities
from test_backup_providers import FakeStore
from tools.backup import remote_data
from tools.backup import live
from tools.backup.coordinator import capture_new_set
from tools.backup.live import RecordedStorage, persist_locator
from tools.backup.metadata import canonical, digest
from tools.backup.providers import AgeEncryption, Budget
from tools.backup.remote_snapshot import KubernetesSource, RemoteSnapshot
from tools.backup.restore import restore


class LocalExec:
    """Real exporter and source data, with only Kubernetes transport replaced."""
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.data = str(snapshot.data)

    def identity(self):
        return self.snapshot.context['source']

    def execute(self, action, sha, destination, max_bytes):
        if action == 'export':
            remote_data.export(self.data, sha, destination, max_bytes)
        else:
            destination.write(json.dumps(remote_data.export(self.data, sha, None, max_bytes)).encode())


def remote(snapshot):
    return RemoteSnapshot(LocalExec(snapshot), snapshot.state, snapshot.registry, snapshot.delivery_key,
                          snapshot.registry_access, copy.deepcopy(snapshot.context), snapshot.retention_ref,
                          snapshot.budget, snapshot.lock_fd, cd_enabled=True)


def test_remote_snapshot_real_creation_and_native_restore(source, tmp_path):
    snapshot, _ = source
    age, keys, recipients = identities(tmp_path)
    members = sorted([{'key_id': str(i), 'type': 'age-x25519', 'value': value,
                       'fingerprint': hashlib.sha256(value.encode()).hexdigest()}
                      for i, value in enumerate(recipients)], key=lambda x: (x['type'], x['value']))
    snapshot.context.update(recipients=members, recipients_digest=digest(canonical(members)))
    capture = remote(snapshot)
    cipher = AgeEncryption(members, age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), snapshot.budget)
    retained = FakeStore()
    storage = RecordedStorage(retained, 'test-bucket', 'wqs', snapshot.budget)
    work = tmp_path / 'private'; work.mkdir(mode=0o700)
    result = capture_new_set(capture, cipher, storage, work, 'remote-set')
    assert result == {'evidence_type': 'creation_result', 'created': True, 'backup_id': 'remote-set'}
    persist_locator(snapshot.state, 'remote-set', storage.receipt)
    locator = json.loads((snapshot.state / 'backup-locators/remote-set.json').read_text())
    assert locator['sha256'] == hashlib.sha256(retained.value).hexdigest()
    assert locator['key'] == retained.key and locator['version'] == 'version-one'
    encrypted = tmp_path / 'backup.age'; encrypted.write_bytes(retained.value)
    destinations = {key: str(tmp_path / 'restored' / path) for key, path in {
        'data_dir': 'data', 'state_dir': 'state', 'registry_file': 'auth/users.json',
        'delivery_key_file': 'auth/key', 'image_inventory_file': 'images.json',
        'registry_access_file': 'registry.json'}.items()}
    assert restore(encrypted, keys[0], age, cipher.sha, None, destinations)['verified'] is True
    assert not list(work.iterdir())


@pytest.mark.parametrize('change', ['database', 'result', 'secret', 'ledger', 'identity'])
def test_remote_boundary_rejects_changes(source, change):
    snapshot, output = source
    capture = remote(snapshot)
    manifest = capture.create(output)
    if change == 'database':
        with sqlite3.connect(snapshot.data / 'jobs.sqlite3') as db:
            db.execute("UPDATE jobs SET name='changed'")
    elif change == 'result':
        (snapshot.data / ('a' * 32) / 'result.npz').write_bytes(b'changed')
    elif change == 'secret':
        snapshot.registry.value = b'[]'
    elif change == 'ledger':
        (snapshot.state / 'deployment.json').write_text('{}')
    else:
        capture.source.identity = lambda: {**capture.identity, 'workload': 'replaced'}
    root = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises((ValueError, KeyError)):
            capture.validate(root, manifest)
    finally:
        os.close(root)


def test_locator_refuses_overwrite_and_propagates_sync_failure(tmp_path, monkeypatch):
    persist_locator(tmp_path, 'set', {'key': 'first'})
    with pytest.raises(FileExistsError):
        persist_locator(tmp_path, 'set', {'key': 'second'})
    assert json.loads((tmp_path / 'backup-locators/set.json').read_text())['key'] == 'first'
    monkeypatch.setattr(os, 'fsync', lambda *_: (_ for _ in ()).throw(OSError('simulated disk failure')))
    with pytest.raises(OSError):
        persist_locator(tmp_path, 'other', {'key': 'new'})
    assert not (tmp_path / 'backup-locators/other.json').exists()


@pytest.mark.parametrize('failure', ['deadline', 'disconnect', 'terminate'])
def test_exporter_failure_releases_sqlite_and_removes_plaintext(source, tmp_path, failure):
    snapshot, _ = source
    # Large enough to block stdout without reading, but no large allocation.
    (snapshot.data / ('a' * 32) / 'result.npz').write_bytes(b'x' * (256 * 1024))
    fake = tmp_path / 'package'; (fake / 'qsim').mkdir(parents=True)
    (fake / 'qsim/__init__.py').write_text('')
    (fake / 'qsim/settings.py').write_text('from pathlib import Path\nDATA=Path(' + repr(str(snapshot.data)) + ')\n')
    scratch = tmp_path / 'scratch'; scratch.mkdir()
    env = {**os.environ, 'PYTHONPATH': str(fake), 'TMPDIR': str(scratch)}
    process = subprocess.Popen([sys.executable, str(Path(remote_data.__file__))], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=fake, env=env)
    try:
        ready = json.loads(process.stdout.readline())['ready']
        process.stdin.write(json.dumps({'action': 'export', 'sha': SHA, 'deadline': ready + 2000, 'max_bytes': 1024 * 1024}).encode())
        process.stdin.close()
        # Receiving TAR bytes proves the exporter reached its write transaction.
        assert process.stdout.read(512)
        if failure == 'disconnect':
            process.stdout.close()
        elif failure == 'terminate':
            process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) != 0
        assert not list(scratch.iterdir())
        with sqlite3.connect(snapshot.data / 'jobs.sqlite3', timeout=0.2) as db:
            db.execute('BEGIN IMMEDIATE')
            db.rollback()
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        process.stdout.close(); process.stderr.close()


def test_oversized_database_rejected_before_fingerprinting(source, monkeypatch):
    snapshot, _ = source
    monkeypatch.setattr(remote_data, 'source_state', lambda *_: pytest.fail('fingerprinted oversized DB'))
    with pytest.raises(ValueError, match='database exceeds limit'):
        remote_data.export(snapshot.data, SHA, io.BytesIO(), 1)


def test_transport_recomputes_deadline_after_delayed_exec_start(tmp_path, monkeypatch):
    binary = tmp_path / 'kubectl'
    binary.write_text('#!' + sys.executable + '\n' + '''
import json,sys,time
time.sleep(2)
ready=int(time.monotonic()*1000)
print(json.dumps({'ready':ready}),flush=True)
request=json.load(sys.stdin)
print(json.dumps({'window':request['deadline']-ready}))
''')
    binary.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + os.environ['PATH'])
    source = KubernetesSource('test', 'api', 'api', '/data', SHA, Budget(16))
    source.pod = 'api-pod'
    out = io.BytesIO()
    source.execute('validate', SHA, out, 512 * 1024 * 1024)
    assert 1000 <= json.loads(out.getvalue())['window'] <= 4000


@pytest.fixture
def kube_source(monkeypatch):
    image = 'registry.example.invalid/api@sha256:' + 'a' * 64
    container = {'name': 'api', 'image': image, 'volumeMounts': [{'name': 'data', 'mountPath': '/data'}]}
    deployment = {'metadata': {'uid': 'deployment'}, 'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'api'}},
                  'template': {'metadata': {'annotations': {'wqsim-release-sha': SHA}}, 'spec': {'containers': [container]}}}}
    rs = {'metadata': {'uid': 'replicaset', 'ownerReferences': [{'controller': True, 'kind': 'Deployment', 'uid': 'deployment'}]}}
    pod = {'metadata': {'name': 'api-pod', 'uid': 'pod', 'annotations': {'wqsim-release-sha': SHA},
           'ownerReferences': [{'controller': True, 'kind': 'ReplicaSet', 'name': 'api-rs', 'uid': 'replicaset'}]},
           'spec': {'containers': [copy.deepcopy(container)], 'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'data'}}]},
           'status': {'containerStatuses': [{'name': 'api', 'ready': True, 'state': {'running': {}}, 'imageID': image}]}}
    pvc = {'metadata': {'uid': 'pvc'}, 'status': {'phase': 'Bound'}}
    records = {'deployment': deployment, 'replicaset': rs, 'pods': {'items': [pod]}, 'pvc': pvc}
    source = KubernetesSource('test', 'api', 'api', '/data', SHA, Budget())
    monkeypatch.setattr(source, 'get', lambda kind, *args, **kwargs: records[kind])
    return source, records


def test_kubernetes_source_binds_runtime_uid_and_pvc(kube_source):
    source, records = kube_source
    # A running state is a mapping; its timestamp is not an authorization input.
    records['pods']['items'][0]['status']['containerStatuses'][0]['state']['running']['startedAt'] = 'now'
    identity = source.identity()
    assert identity['image_or_executable_digest'] == 'a' * 64
    old = identity['storage']
    records['pvc']['metadata']['uid'] = 'replacement'
    assert source.identity()['storage'] != old


@pytest.mark.parametrize('change', ['runtime', 'owner', 'annotation', 'subpath', 'replicas'])
def test_kubernetes_source_refuses_mismatched_execution(kube_source, change):
    source, records = kube_source
    pod = records['pods']['items'][0]
    pod['status']['containerStatuses'][0]['state']['running']['startedAt'] = 'now'
    if change == 'runtime':
        pod['status']['containerStatuses'][0]['imageID'] = 'different@sha256:' + 'b' * 64
    elif change == 'owner':
        records['replicaset']['metadata']['uid'] = 'replacement'
    elif change == 'annotation':
        pod['metadata']['annotations']['wqsim-release-sha'] = 'b' * 40
    elif change == 'subpath':
        pod['spec']['containers'][0]['volumeMounts'][0]['subPath'] = 'other'
    else:
        records['deployment']['spec']['replicas'] = 2
    with pytest.raises(ValueError):
        source.identity()


def test_configured_hook_uses_real_pipeline_and_commits_locator(source, tmp_path, monkeypatch):
    snapshot, _ = source
    age, _, recipients = identities(tmp_path)
    members = sorted([{'key_id': str(i), 'type': 'age-x25519', 'value': value,
                       'fingerprint': hashlib.sha256(value.encode()).hexdigest()}
                      for i, value in enumerate(recipients)], key=lambda x: (x['type'], x['value']))
    public = tmp_path / 'recipients.json'; public.write_bytes(canonical(members))
    work = tmp_path / 'private'; work.mkdir(mode=0o700)
    retained = FakeStore()
    monkeypatch.setattr(live, 'KubernetesSource', lambda *args: LocalExec(snapshot))
    monkeypatch.setattr(live, 'SecretReader', lambda namespace, name, key, budget:
                        snapshot.registry if name == 'users' else snapshot.delivery_key)
    monkeypatch.setattr(live, 's3_client', lambda *args: retained)
    config = {'namespace': 'test', 'deployment': 'api', 'container': 'api', 'data': str(snapshot.data),
              'registry_secret': {'name': 'users', 'key': 'users.json'}, 'delivery_secret': {'name': 'admin', 'key': 'key'},
              'state_dir': str(snapshot.state), 'registry_access': str(snapshot.registry_access),
              'work_dir': str(work), 'cd_enabled': True, 'recipients_file': str(public),
              'age': {'path': age, 'sha256': hashlib.sha256(Path(age).read_bytes()).hexdigest()}, 'plugin': None,
              'storage': {'endpoint': 'https://store.example.invalid', 'region': 'test', 'credential_file': '/private/store.json',
                          'bucket': 'test-bucket', 'prefix': 'backups'}, 'retention_ref': 'test-retention',
              'app_id': 'wqs', 'org_id': 'test'}
    result = live.configured_capture(config, 'configured-set')
    assert result == {'evidence_type': 'creation_result', 'created': True, 'backup_id': 'configured-set'}
    locator = json.loads((snapshot.state / 'backup-locators/configured-set.json').read_text())
    assert locator['sha256'] == hashlib.sha256(retained.value).hexdigest()
    assert locator['version'] == 'version-one'
    monkeypatch.setattr(live, 'persist_locator', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        live.configured_capture(config, 'locator-fails')


def test_live_cli_failure_does_not_print_private_diagnostics(tmp_path, monkeypatch, capsys):
    config = tmp_path / 'config.json'; config.write_text('{}')
    monkeypatch.setattr(sys, 'argv', ['live', '--config', str(config)])
    monkeypatch.setattr(live, 'configured_capture', lambda *args: (_ for _ in ()).throw(ValueError('private sentinel')))
    with pytest.raises(SystemExit) as error:
        live.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.err == '' and 'sentinel' not in output.out
    assert json.loads(output.out) == {'evidence_type': 'creation_result', 'created': False,
                                    'backup_id': None, 'failure': 'unavailable'}


@pytest.mark.parametrize('version', [None, '', ' ', 'null', False])
def test_live_storage_refuses_unversioned_generation(tmp_path, version):
    class Unversioned(FakeStore):
        def put_object(self, **kwargs):
            super().put_object(**kwargs)
            return {} if version is None else {'VersionId': version}

        def get_object(self, **kwargs):
            pytest.fail('unversioned upload reached readback')
    ciphertext = tmp_path / 'ciphertext'; ciphertext.write_bytes(b'synthetic storage test')
    storage = RecordedStorage(Unversioned(), 'test-bucket', 'wqs', Budget())
    with pytest.raises(ValueError, match='versioned object required'):
        storage.put(ciphertext)
    assert not hasattr(storage, 'receipt')
