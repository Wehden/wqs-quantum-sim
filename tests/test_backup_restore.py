"""Actual encryption/decryption and native disposable destination load tests."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile

from cryptography.fernet import Fernet
import pytest

from qsim import settings, store
from tools.backup.capture import payload
from tools.backup.metadata import canonical, digest
from tools.backup.restore import restore, unpack
from tools.backup.native_load import NativeLoad

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/backup/conformance-v2.json').read_text())


@pytest.fixture(params=[False, True], ids=["empty", "populated"])
def backup(tmp_path, monkeypatch, request):
    source = tmp_path / 'source'; source.mkdir()
    monkeypatch.setattr(settings, 'DATA', source)
    store.init()
    with sqlite3.connect(source / 'jobs.sqlite3') as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    manifest = copy.deepcopy(FIXTURE['manifest'])
    deps = manifest['dependencies']
    with store.connect() as db:
        db.execute('UPDATE release_control SET release=?,smoke_owner=? WHERE singleton=1',
                   (manifest['context']['scope']['release_sha'], ''))
    with sqlite3.connect(source / 'jobs.sqlite3') as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    deps['results'] = []; deps['release_receipts'] = []
    values = {
        'database': (source / 'jobs.sqlite3').read_bytes(),
        'invitation_registry': b'[]',
        'pending_delivery_key': Fernet.generate_key(),
        'runner_ledger': json.dumps({'status': deps['ledger_state'], 'sha': manifest['context']['scope']['release_sha']}).encode(),
        'image_inventory': json.dumps({'images': ['registry.example.invalid/api@sha256:' + 'a' * 64]}).encode(),
        'registry_access': b'{"auths":{}}',
    }
    manifest['inventory'] = []
    for role, value in values.items():
        path = role + '.bin'; deps[role] = path
        (source / path).write_bytes(value)
        manifest['inventory'].append({'path': path, 'kind': 'regular', 'role': role, **digest(value)})
    if request.param:
        key = values['pending_delivery_key']
        raw_key = 'test-invitation-only'
        user = {'id': 'sample', 'sha256': hashlib.sha256(raw_key.encode()).hexdigest(), 'active': True}
        delivery = {'request_id': 'request', 'email': 'sample@example.invalid', 'identity': 'sample', 'key': raw_key}
        with store.connect() as db:
            db.execute("INSERT INTO jobs(id,owner,name,status,created,updated,request,callback_hash,cleaned) VALUES(?,?,?,?,?,?,?,?,?)",
                       ('a'*32, 'sample', 'sample', 'SUCCEEDED', 1, 1, '{}', 'test', 1))
            db.execute("INSERT INTO key_requests(id,name,email,purpose,created,status,identity,token_hash,key_cipher) VALUES(?,?,?,?,?,?,?,?,?)",
                       ('request', 'sample', delivery['email'], 'test', 1, 'APPROVED', 'sample', user['sha256'], Fernet(key).encrypt(json.dumps(delivery).encode()).decode()))
        with sqlite3.connect(source / 'jobs.sqlite3') as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        values['database'] = (source / 'jobs.sqlite3').read_bytes()
        values['invitation_registry'] = json.dumps([user]).encode()
        for role in ('database', 'invitation_registry'):
            (source / deps[role]).write_bytes(values[role])
            next(e for e in manifest['inventory'] if e['role'] == role).update(digest(values[role]))
        result_path = 'results/' + 'a'*32 + '.npz'
        receipt_path = 'runner/' + 'b'*40 + '.json'
        import numpy as np
        result = io.BytesIO(); np.savez(result, measurements=np.asarray([[0]], dtype=np.uint8))
        for role, path, value in [('results', result_path, result.getvalue()),
                                  ('release_receipts', receipt_path, json.dumps({'sha': 'b'*40, 'status': 'verified'}).encode())]:
            (source / path).parent.mkdir(exist_ok=True)
            (source / path).write_bytes(value)
            deps[role].append(path)
            manifest['inventory'].append({'path': path, 'kind': 'regular', 'role': role, **digest(value)})
    archive = tmp_path / 'backup.tar'
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    try:
        payload(fd, manifest, archive)
    finally:
        os.close(fd)
    target = tmp_path / 'actual-destination'
    destinations = {key: str(target / value) for key, value in {
        'data_dir': 'data', 'state_dir': 'runner', 'registry_file': 'auth/users.json',
        'delivery_key_file': 'auth/delivery-key', 'image_inventory_file': 'images.json',
        'registry_access_file': 'registry/config.json'}.items()}
    return source, archive, manifest, destinations


def identities(tmp_path):
    age = shutil.which('age'); keygen = shutil.which('age-keygen')
    assert age and keygen, 'CI must install the pinned age tools'
    paths, recipients = [], []
    for i in range(2):
        path = tmp_path / f'identity-{i}'
        subprocess.run([keygen, '-o', str(path)], capture_output=True, check=True)
        recipients.append(subprocess.run([keygen, '-y', str(path)], capture_output=True, check=True, text=True).stdout.strip())
        paths.append(path)
    return age, paths, recipients


@pytest.mark.parametrize('route', [0, 1])
def test_actual_chosen_identity_decrypts_and_loads_without_external_evidence(backup, tmp_path, route):
    _, archive, manifest, destinations = backup
    age, keys, recipients = identities(tmp_path)
    encrypted = tmp_path / 'actual.age'
    subprocess.run([age, '-r', recipients[0], '-r', recipients[1], '-o', str(encrypted), str(archive)], check=True)
    result = restore(encrypted, keys[route], age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), None, destinations)
    assert result == {'evidence_type': 'final_result', 'verified': True, 'backup_id': manifest['context']['set_id']}
    with sqlite3.connect(Path(destinations['data_dir']) / 'jobs.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == len(manifest['dependencies']['results'])
    assert Path(destinations['registry_file']).read_bytes() == (backup[0] / manifest['dependencies']['invitation_registry']).read_bytes()
    # Old source descriptor is only provenance: the destination is a different path.
    assert Path(destinations['delivery_key_file']).is_file()


def test_wrong_identity_never_loads(backup, tmp_path):
    _, archive, _, destinations = backup
    age, keys, recipients = identities(tmp_path)
    encrypted = tmp_path / 'actual.age'
    subprocess.run([age, '-r', recipients[0], '-o', str(encrypted), str(archive)], check=True)
    with pytest.raises(ValueError, match='decryption failed'):
        restore(encrypted, keys[1], age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), None, destinations)
    assert not Path(destinations['data_dir']).exists()


def test_archive_rejects_wrong_id_and_corrupted_bytes_before_load(backup):
    _, archive, manifest, destinations = backup
    with pytest.raises(ValueError, match='unexpected backup'):
        unpack(archive.read_bytes(), 'wrong')
    raw = bytearray(archive.read_bytes())
    with tarfile.open(archive) as tar:
        member = tar.getmember(manifest['dependencies']['database'])
        raw[member.offset_data] ^= 1
    with pytest.raises(ValueError, match='hash mismatch'):
        unpack(bytes(raw))
    assert not Path(destinations['data_dir']).exists()


@pytest.mark.parametrize('kind', ['extra', 'duplicate', 'symlink', 'traversal'])
def test_hostile_archive_rejected(backup, kind):
    _, archive, manifest, _ = backup
    with tarfile.open(archive, 'a') as tar:
        name = manifest['dependencies']['database'] if kind == 'duplicate' else ('../escape' if kind == 'traversal' else 'unexpected')
        member = tarfile.TarInfo(name)
        if kind == 'symlink': member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
        tar.addfile(member, io.BytesIO(b''))
    with pytest.raises(ValueError): unpack(archive.read_bytes())


def test_native_failure_not_promoted(backup, monkeypatch):
    _, archive, _, destinations = backup
    manifest, files = unpack(archive.read_bytes())
    target = Path(destinations['data_dir']); target.mkdir(parents=True)
    (target / 'jobs.sqlite3-wal').write_bytes(b'active writer')
    with pytest.raises(ValueError, match='sidecars'):
        NativeLoad(destinations).load(manifest, files)
    assert not (target / 'jobs.sqlite3').exists()


def test_partial_load_failure_does_not_return_success(backup, tmp_path, monkeypatch):
    _, archive, _, destinations = backup
    age, keys, recipients = identities(tmp_path)
    encrypted = tmp_path / 'actual.age'
    subprocess.run([age, '-r', recipients[0], '-o', str(encrypted), str(archive)], check=True)
    original = os.fsync
    def fail(fd):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError):
        restore(encrypted, keys[0], age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), None, destinations)


def test_actual_creation_is_not_restore_evidence(backup, tmp_path, monkeypatch):
    from tools.backup.create import create
    source, _, manifest, _ = backup
    age, _, recipients = identities(tmp_path)
    members = sorted([{'key_id': 'test-' + str(i), 'type': 'age-x25519', 'value': value,
                       'fingerprint': hashlib.sha256(value.encode()).hexdigest()} for i, value in enumerate(recipients)],
                     key=lambda item: (item['type'], item['value']))
    manifest['context']['recipients'] = members
    manifest['context']['recipients_digest'] = digest(canonical(members))
    class LocalProviders:
        def validate_recipients(self, supplied): return supplied == members
        def validate_boundary(self, root, record):
            return all(digest((source / entry['path']).read_bytes()) == {k: entry[k] for k in ('sha256', 'size')}
                       for entry in record['inventory'])
        def encrypt(self, raw, encrypted, supplied):
            args = [age]
            for member in supplied: args += ['-r', member['value']]
            subprocess.run(args + ['-o', str(encrypted), str(raw)], check=True)
        def put(self, encrypted):
            with open(tmp_path / 'retained.age', 'xb') as out: out.write(encrypted.read_bytes())
            return 'local-test-object'
        def readback(self, ref, destination):
            assert ref == 'local-test-object'
            destination.write_bytes((tmp_path / 'retained.age').read_bytes())
    work = tmp_path / 'work'; work.mkdir()
    manifest['dependencies'].update(parent_coordinated=False, admission_held=False, cd_enabled=True)
    # Reject even a one-byte overflow before encryption/storage; the actual TAR
    # includes headers and block padding beyond the inventory's payload sizes.
    import importlib
    restore_module = importlib.import_module('tools.backup.restore')
    if manifest['dependencies']['results']:
        import numpy as np
        import zipfile
        native_module = importlib.import_module('tools.backup.native_load')
        path = manifest['dependencies']['results'][0]
        compressed = io.BytesIO()
        np.savez_compressed(compressed, measurements=np.zeros(4096, dtype=np.uint8))
        value = compressed.getvalue()
        (source / path).write_bytes(value)
        next(e for e in manifest['inventory'] if e['path'] == path).update(digest(value))
        with zipfile.ZipFile(io.BytesIO(value)) as zipped:
            expanded = sum(m.file_size for m in zipped.infolist())
        assert expanded > len(value)
        with monkeypatch.context() as patch:
            patch.setattr(native_module, 'MAX_NPZ_BYTES', expanded - 1)
            with pytest.raises(ValueError, match='oversized NPZ'):
                create(source, manifest, LocalProviders(), work, manifest['context']['set_id'])
            assert not (tmp_path / 'retained.age').exists()
    check_archive = tmp_path / 'size-check.tar'
    root = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    try:
        payload(root, manifest, check_archive)
    finally:
        os.close(root)
    size = check_archive.stat().st_size
    assert size > sum(entry['size'] for entry in manifest['inventory'])
    with monkeypatch.context() as patch:
        patch.setattr(restore_module, 'MAX_BYTES', size - 1)
        with pytest.raises(ValueError, match='archive exceeds size limit'):
            create(source, manifest, LocalProviders(), work, manifest['context']['set_id'])
        assert not (tmp_path / 'retained.age').exists()
    with monkeypatch.context() as patch:
        patch.setattr(restore_module, 'MAX_BYTES', size)
        result = create(source, manifest, LocalProviders(), work, manifest['context']['set_id'])
    assert result == {'evidence_type': 'creation_result', 'created': True, 'backup_id': manifest['context']['set_id']}
    assert 'verified' not in result
    with pytest.raises(ValueError, match='unexpected backup'):
        create(source, manifest, LocalProviders(), work, 'wrong-set')
    invalid = LocalProviders()
    invalid.validate_boundary = lambda *_: False
    with pytest.raises(ValueError, match='source boundary'):
        create(source, manifest, invalid, work, manifest['context']['set_id'])


@pytest.mark.parametrize('cd_enabled', [False, True])
def test_v2_creation_allows_current_cd_switch_as_provenance(backup, cd_enabled):
    from tools.backup.v2 import validate_manifest
    _, _, manifest, _ = backup
    manifest['dependencies']['cd_enabled'] = cd_enabled
    validate_manifest(manifest, None)


@pytest.mark.parametrize('role', ['database', 'invitation_registry', 'pending_delivery_key', 'runner_ledger', 'image_inventory', 'registry_access'])
def test_matching_hash_does_not_make_malformed_native_data_loadable(backup, tmp_path, role):
    source, _, manifest, destinations = backup
    path = manifest['dependencies'][role]
    value = b'not-valid-native-data'
    (source / path).write_bytes(value)
    next(item for item in manifest['inventory'] if item['path'] == path).update(digest(value))
    archive = tmp_path / 'malformed.tar'
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    try: payload(fd, manifest, archive)
    finally: os.close(fd)
    with pytest.raises(Exception): unpack(archive.read_bytes())
    assert not Path(destinations['data_dir']).exists()


def test_restore_cli_missing_input_returns_only_failure_json(backup, tmp_path):
    import sys
    _, _, _, destinations = backup
    config = tmp_path / 'destinations.json'; config.write_text(json.dumps(destinations))
    result = subprocess.run([sys.executable, '-m', 'tools.backup.restore', '--backup', str(tmp_path/'missing.age'),
                             '--identity', str(tmp_path/'missing.identity'), '--age', '/missing/age',
                             '--age-sha256', '0'*64, '--destinations', str(config)], capture_output=True, text=True)
    assert result.returncode == 1 and result.stderr == ''
    assert json.loads(result.stdout) == {'evidence_type': 'final_result', 'verified': False,
                                         'backup_id': None, 'failure': 'unavailable'}


def test_noncanonical_manifest_bytes_rejected(backup, tmp_path):
    source, _, manifest, _ = backup
    malformed = tmp_path / 'noncanonical.tar'
    with tarfile.open(malformed, 'w') as tar:
        value = json.dumps(manifest, indent=2).encode()
        header = tarfile.TarInfo('manifest.json'); header.size = len(value)
        tar.addfile(header, io.BytesIO(value))
        for entry in manifest['inventory']:
            value = (source / entry['path']).read_bytes()
            header = tarfile.TarInfo(entry['path']); header.size = len(value)
            tar.addfile(header, io.BytesIO(value))
    with pytest.raises(ValueError, match='noncanonical manifest'):
        unpack(malformed.read_bytes())


@pytest.mark.parametrize('change', ['duplicate', 'order', 'fingerprint'])
def test_recomputed_digest_does_not_hide_invalid_recipient_metadata(backup, change):
    from tools.backup.v2 import validate_manifest
    _, _, manifest, _ = backup
    members = manifest['context']['recipients']
    if change == 'duplicate': members[1] = copy.deepcopy(members[0])
    elif change == 'order': members.reverse()
    else: members[0]['fingerprint'] = '0'*64
    manifest['context']['recipients_digest'] = digest(canonical(members))
    with pytest.raises(ValueError): validate_manifest(manifest, None)


def test_actual_decrypt_rejects_hash_consistent_invalid_npz(backup, tmp_path):
    source, _, manifest, destinations = backup
    # Add a completed result also for the empty-install fixture so both exercise
    # the same negative case without a skipped/vacuous branch.
    if not manifest['dependencies']['results']:
        with store.connect() as db:
            db.execute("INSERT INTO jobs(id,owner,name,status,created,updated,request,callback_hash,cleaned) VALUES(?,?,?,?,?,?,?,?,?)",
                       ('a'*32, 'sample', 'sample', 'SUCCEEDED', 1, 1, '{}', 'test', 1))
        with sqlite3.connect(source / 'jobs.sqlite3') as db: db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        value = (source / 'jobs.sqlite3').read_bytes()
        path = manifest['dependencies']['database']; (source/path).write_bytes(value)
        next(e for e in manifest['inventory'] if e['role']=='database').update(digest(value))
        path = 'results/' + 'a'*32 + '.npz'; (source/path).parent.mkdir(exist_ok=True)
        manifest['dependencies']['results'] = [path]
        manifest['inventory'].append({'path': path, 'kind':'regular', 'role':'results'})
    path = manifest['dependencies']['results'][0]; value = b'not a numpy archive'
    (source/path).write_bytes(value)
    next(e for e in manifest['inventory'] if e['path']==path).update(digest(value))
    archive = tmp_path / 'invalid-native.tar'
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    try: payload(fd, manifest, archive)
    finally: os.close(fd)
    age, keys, recipients = identities(tmp_path)
    encrypted = tmp_path / 'invalid-native.age'
    subprocess.run([age, '-r', recipients[0], '-o', str(encrypted), str(archive)], check=True)
    with pytest.raises(Exception):
        restore(encrypted, keys[0], age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), None, destinations)
    assert not Path(destinations['data_dir']).exists()



def test_false_provenance_flags_and_different_source_do_not_block_actual_load(backup, tmp_path):
    source, _, manifest, destinations = backup
    manifest['dependencies'].update(parent_coordinated=False, admission_held=False, cd_enabled=True)
    manifest['context']['source']['workload'] = 'historical-source-no-longer-present'
    with store.connect() as db:
        db.execute("UPDATE release_control SET release='',smoke_owner='' WHERE singleton=1")
    with sqlite3.connect(source / 'jobs.sqlite3') as db: db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    value = (source/'jobs.sqlite3').read_bytes(); path = manifest['dependencies']['database']
    (source/path).write_bytes(value)
    next(e for e in manifest['inventory'] if e['path']==path).update(digest(value))
    archive = tmp_path/'provenance.tar'
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    try: payload(fd, manifest, archive)
    finally: os.close(fd)
    age, keys, recipients = identities(tmp_path)
    encrypted = tmp_path/'provenance.age'
    subprocess.run([age, '-r', recipients[0], '-o', str(encrypted), str(archive)], check=True)
    result = restore(encrypted, keys[0], age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), None, destinations)
    assert result['verified'] is True
    with sqlite3.connect(Path(destinations['data_dir'])/'jobs.sqlite3') as db:
        assert db.execute('SELECT release FROM release_control WHERE singleton=1').fetchone()[0] == ''
