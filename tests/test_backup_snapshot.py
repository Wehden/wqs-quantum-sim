import copy
import fcntl
import hashlib
import json
from pathlib import Path

from cryptography.fernet import Fernet
import pytest

from qsim import settings, store
from tools.backup.snapshot import Snapshot
from tools.backup.providers import Budget

SHA = '1'*40


class Volume:
    def verify(self, context, data):
        return True


class Reader:
    def __init__(self, value):
        self.value = value

    def read(self):
        return (('synthetic-uid', '1'), self.value)


@pytest.fixture
def source(tmp_path, monkeypatch):
    data, state, output = [tmp_path / name for name in ('data', 'state', 'output')]
    for path in (data, state, output):
        path.mkdir()
    monkeypatch.setattr(settings, 'DATA', data)
    store.init()
    key = Fernet.generate_key()
    raw_key = 'synthetic invitation only'
    registry = [{'id': 'sample', 'sha256': hashlib.sha256(raw_key.encode()).hexdigest(), 'active': True}]
    payload = {'request_id': 'request', 'identity': 'sample', 'email': 'sample@example.invalid', 'key': raw_key}
    with store.connect() as db:
        db.execute('UPDATE release_control SET release=?,smoke_owner=?', (SHA, ''))
        db.execute("INSERT INTO key_requests(id,name,email,purpose,created,status,identity,token_hash,key_cipher) VALUES(?,?,?,?,?,?,?,?,?)",
                   ('request', 'sample', payload['email'], 'test', 1, 'APPROVED', 'sample', registry[0]['sha256'], Fernet(key).encrypt(json.dumps(payload).encode()).decode()))
    with store.connect() as db:
        db.execute("INSERT INTO jobs(id,owner,name,status,created,updated,request,callback_hash,cleaned) VALUES(?,?,?,?,?,?,?,?,?)", ('a'*32,'test','test','SUCCEEDED',1,1,'{}','test',1))
    (data / ('a'*32)).mkdir()
    import numpy as np
    np.savez_compressed(data / ('a'*32) / 'result.npz', measurements=np.zeros((1, 1), dtype=np.uint8))
    (state / ('b'*40 + '.json')).write_text(json.dumps({'sha':'b'*40, 'status':'verified'}))
    ledger = {'sha': SHA, 'status': 'holding', 'images': {'api': 'registry.example.invalid/api@sha256:'+'a'*64},
              'previous': {'api': {'image': 'registry.example.invalid/api@sha256:'+'b'*64}}}
    (state / 'deployment.json').write_text(json.dumps(ledger))
    access = tmp_path / 'access.json'
    access.write_text('{"auths":{}}')
    fixture = json.loads((Path(__file__).parent / 'fixtures/backup/conformance.json').read_text())
    context = copy.deepcopy(fixture['base']['graph']['records']['manifest']['context'])
    lock = (state / 'lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    snapshot = Snapshot(data, state, Reader(json.dumps(registry).encode()), Reader(key), access,
                        context, 'synthetic-retention-policy', Budget(), lock.fileno(), Volume(), cd_enabled=True)
    yield snapshot, output
    lock.close()


def test_coherent_snapshot_and_matching_delivery_material(source):
    snapshot, output = source
    manifest = snapshot.create(output)
    assert manifest['contract_version'] == 2
    assert manifest['dependencies']['cd_enabled'] is True
    assert manifest['context']['boundary']['method'] == 'wqs-sqlite-write-lock-v1'
    assert len(manifest['context']['boundary']['cursor']) == 64
    assert (output / 'delivery-key').read_bytes() == snapshot.delivery_key.value
    import os
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert snapshot.validate(fd, manifest)
        (output / 'registry.json').write_text('[]')
        with pytest.raises(ValueError, match='snapshot changed'):
            snapshot.validate(fd, manifest)
    finally:
        os.close(fd)
    with store.connect() as db:
        assert db.execute('SELECT release FROM release_control').fetchone()[0] == SHA
        assert db.execute('SELECT key_cipher FROM key_requests').fetchone()[0]


@pytest.mark.parametrize('change', ['open', 'smoke', 'approving', 'sending', 'lease', 'wrong-key', 'changed-secret', 'ledger'])
def test_snapshot_refuses_unsafe_boundary(source, change):
    snapshot, output = source
    with store.connect() as db:
        if change == 'open':
            db.execute("UPDATE release_control SET release=''")
        elif change == 'smoke':
            db.execute("UPDATE release_control SET smoke_owner='test'")
        elif change == 'approving':
            db.execute("UPDATE key_requests SET status='APPROVING'")
        elif change == 'sending':
            db.execute("UPDATE key_requests SET notification_state='SENDING'")
        elif change == 'lease':
            db.execute('UPDATE key_requests SET delivery_lease=1')
    if change == 'wrong-key':
        snapshot.delivery_key.value = Fernet.generate_key()
    elif change == 'changed-secret':
        count = iter([1, 2])
        snapshot.registry.read = lambda: (('synthetic-uid', str(next(count))), snapshot.registry.value)
    elif change == 'ledger':
        (snapshot.state / 'deployment.json').write_text('{"sha":"'+SHA+'","status":"verified"}')
    with pytest.raises(Exception):
        snapshot.create(output)
    # The source stays held; no recovery/rotation/write is performed by capture.
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM key_requests').fetchone()[0] == 1


def test_requires_existing_parent_lock(source):
    snapshot, output = source
    fcntl.flock(snapshot.lock_fd, fcntl.LOCK_UN)
    with pytest.raises(ValueError, match='lock is not held'):
        snapshot.create(output)


def test_initial_release_without_receipts_is_supported(source):
    snapshot, output = source
    for path in snapshot.state.glob('b*.json'):
        path.unlink()
    assert snapshot.create(output)['dependencies']['release_receipts'] == []


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('cd_enabled', [False, True])
def test_capture_orchestration_encrypts_reads_back_and_cleans(source, tmp_path, empty, cd_enabled):
    import shutil
    from tools.backup.coordinator import capture_new_set
    from tools.backup.providers import AgeEncryption, S3Storage
    from tools.backup.metadata import canonical, digest
    from test_backup_providers import FakeStore
    from test_backup_restore import identities
    from tools.backup.restore import restore
    snapshot, _ = source
    snapshot.cd_enabled = cd_enabled
    if empty:
        with store.connect() as db:
            db.execute('DELETE FROM jobs')
        for path in snapshot.state.glob('b*.json'):
            path.unlink()
    age, keys, recipients = identities(tmp_path)
    approved = sorted([{'key_id': str(i), 'type': 'age-x25519', 'value': value,
                        'fingerprint': hashlib.sha256(value.encode()).hexdigest()}
                       for i, value in enumerate(recipients)], key=lambda item: (item['type'], item['value']))
    snapshot.context['recipients'] = approved
    snapshot.context['recipients_digest'] = digest(canonical(approved))
    age = shutil.which('age')
    encryption = AgeEncryption(approved, age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), snapshot.budget)
    retained = FakeStore()
    storage = S3Storage(retained, 'test-bucket', 'wqs', snapshot.budget)
    work = tmp_path / 'private'
    work.mkdir(mode=0o700)
    result = capture_new_set(snapshot, encryption, storage, work, 'requested-set')
    assert result == {'evidence_type': 'creation_result', 'created': True, 'backup_id': 'requested-set'}
    encrypted = tmp_path / 'retained.age'
    encrypted.write_bytes(retained.value)
    destinations = {name: str(tmp_path / 'restored' / path) for name, path in {
        'data_dir': 'data', 'state_dir': 'state', 'registry_file': 'auth/registry.json',
        'delivery_key_file': 'auth/delivery-key', 'image_inventory_file': 'images.json',
        'registry_access_file': 'registry-access.json'}.items()}
    restored = restore(encrypted, keys[0], age, encryption.sha, 'requested-set', destinations)
    assert restored == {'evidence_type': 'final_result', 'verified': True, 'backup_id': 'requested-set'}
    assert Path(destinations['data_dir'], 'jobs.sqlite3').is_file()
    archived = json.loads(snapshot.expected)
    assert archived['context']['scope']['release_sha'] == SHA
    assert archived['dependencies']['cd_enabled'] is cd_enabled
    assert bool(archived['dependencies']['results']) is not empty
    assert not list(work.iterdir())


def test_registry_reads_happen_under_database_write_lock(source):
    import sqlite3
    snapshot, output = source
    old_read = snapshot.registry.read
    def probe():
        with sqlite3.connect(snapshot.data / 'jobs.sqlite3', timeout=0) as writer:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                writer.execute('BEGIN IMMEDIATE')
        return old_read()
    snapshot.registry.read = probe
    snapshot.create(output)


def test_wrong_current_volume_rejected(source):
    snapshot, output = source
    snapshot.volume.verify = lambda *_: False
    with pytest.raises(ValueError, match='source volume'):
        snapshot.create(output)


def test_mount_verifier_binds_pod_pvc_image_and_revision(monkeypatch, source):
    from tools.backup.snapshot import MountedVolume, source_identity
    monkeypatch.setattr('tools.backup.snapshot.socket.gethostname', lambda: 'capture-pod')
    volume = MountedVolume('test', 'capture-pod', 'capture', Budget())
    pod = {'metadata': {'uid': 'pod-uid', 'annotations': {'wqsim-release-sha': SHA}},
           'spec': {'containers': [{'name': 'capture', 'image': 'registry.example.invalid/capture@sha256:'+'a'*64,
                                   'volumeMounts': [{'name': 'data', 'mountPath': '/data'}]}],
                    'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'application-data'}}]}}
    pvc = {'metadata': {'uid': 'pvc-uid'}, 'status': {'phase': 'Bound'}}
    volume.get = lambda kind, name: pod if kind == 'pod' else pvc
    context = {'source': {'storage': source_identity('pvc', 'test', 'application-data', 'pvc-uid'),
                          'workload': source_identity('pod', 'test', 'capture-pod', 'pod-uid'),
                          'revision': SHA, 'image_or_executable_digest': 'a'*64}}
    assert volume.verify(context, Path('/data'))
    snapshot, output = source
    snapshot.context['source'] = context['source']
    snapshot.volume = volume
    pod['spec']['containers'][0]['volumeMounts'][0]['mountPath'] = str(snapshot.data)
    manifest = snapshot.create(output)  # Includes pinned schema and dependency validation.
    assert manifest['dependencies']['pvc_identity'] == context['source']['storage']
    import os
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert snapshot.validate(fd, manifest)
    finally:
        os.close(fd)
    pod['spec']['containers'][0]['volumeMounts'][0]['mountPath'] = '/data'
    pvc['metadata']['uid'] = 'replacement'
    with pytest.raises(ValueError, match='identity mismatch'):
        volume.verify(context, Path('/data'))


@pytest.mark.parametrize('change', ['registry', 'key', 'version', 'delivered', 'hold', 'in-flight'])
def test_post_storage_validation_rejects_live_mutation(source, change):
    import os
    snapshot, output = source
    manifest = snapshot.create(output)
    if change == 'registry':
        snapshot.registry.value = b'[]'
    elif change == 'key':
        snapshot.delivery_key.value = Fernet.generate_key()
    elif change == 'version':
        snapshot.registry.read = lambda: (('synthetic-uid', '2'), snapshot.registry.value)
    else:
        with store.connect() as db:
            if change == 'delivered':
                db.execute("UPDATE key_requests SET key_cipher=NULL")
            elif change == 'hold':
                db.execute("UPDATE release_control SET release=''")
            else:
                db.execute("UPDATE key_requests SET delivery_state='SENDING'")
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):
            snapshot.validate(fd, manifest)
    finally:
        os.close(fd)


def test_source_identity_fits_contract_for_long_names():
    from tools.backup.snapshot import source_identity
    import re
    identity = source_identity('pvc', 'n' * 63, 'v' * 253, 'u' * 36)
    assert len(identity) <= 128
    assert re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]*', identity)
    assert identity != source_identity('pvc', 'n' * 63, 'v' * 253, 'different-uid')
    assert identity != source_identity('pod', 'n' * 63, 'v' * 253, 'u' * 36)
