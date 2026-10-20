import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from qsim.welcome_evidence import (ProviderConfig, EvidenceReader, EvidenceError,
                                   InvalidCursor, SnapshotChanged)


@pytest.fixture
def rig(tmp_path):
    digest = hashlib.sha256(b'synthetic-user-key').hexdigest()
    member = {'subject_id': 'subject-a', 'membership_id': 'member-a', 'identity': 'alice',
              'request_id': 'a' * 32, 'recipient_email': 'alice@example.test',
              'display_name': 'Alice', 'enabled': True, 'reviewed_by': 'operator-a', 'reviewed_at': '2026-10-16'}
    binding = {'schema_version': 1, 'revision': 'mapping-v1', 'app_instance_id': 'app-a',
               'org_id': 'org-a', 'namespace': 'test', 'secret_name': 'test-users', 'members': [member]}
    grant = {'credential_id': 'central-a', 'sha256': hashlib.sha256(b'synthetic-service-key').hexdigest(),
             'audience': 'wqs-welcome:app-a', 'org_id': 'org-a',
             'scopes': ['welcome:read', 'welcome:capabilities'], 'active': True}
    config = ProviderConfig(*(tmp_path / f for f in ('binding.json', 'auth.json', 'integrity', 'db.sqlite3', 'cert', 'key')))
    config.binding_file.write_text(json.dumps(binding))
    config.auth_file.write_text(json.dumps([grant]))
    config.integrity_key_file.write_bytes(b'x' * 32)
    with sqlite3.connect(config.database) as db:
        db.execute('CREATE TABLE key_requests(id TEXT PRIMARY KEY,identity TEXT,email TEXT,status TEXT,reviewed REAL,token_hash TEXT,delivery_state TEXT,delivered REAL,key_cipher TEXT)')
        db.execute('INSERT INTO key_requests VALUES(?,?,?,?,?,?,?,?,?)', ('a'*32, 'alice', 'alice@example.test', 'APPROVED', 5, digest, 'SENT', 6, 'NEVER_EXPORT_CIPHER'))
    state = {'revision': '1', 'users': [{'id': 'alice', 'sha256': digest, 'active': True}], 'time': 1000}
    calls = []
    def get(binding, timeout):
        calls.append((binding.namespace, binding.secret_name, timeout))
        return state['revision'], copy.deepcopy(state['users'])
    reader = EvidenceReader(config, registry_get=get, clock=lambda: state['time'])
    return SimpleNamespace(config=config, binding=binding, member=member, grant=grant, state=state,
                           reader=reader, calls=calls, digest=digest)


def update(rig, sql, args=()):
    with sqlite3.connect(rig.config.database) as db:
        db.execute(sql, args)


def test_proof_is_stable_and_private(rig):
    before = rig.config.database.read_bytes()
    _, contacts, snapshot = rig.reader.observe()
    contact = contacts[0]
    assert contact['eligible'] and contact['recipient_email'] == 'alice@example.test'
    assert contact['expires_at'] - contact['observed_at'] == 30
    rig.state['time'] += 3
    _, again, snapshot2 = rig.reader.observe()
    assert again[0]['revision'] == contact['revision'] and snapshot == snapshot2
    serialized = json.dumps(contacts)
    for forbidden in (rig.digest, 'NEVER_EXPORT_CIPHER', 'synthetic-user-key', 'token_hash', 'key_cipher'):
        assert forbidden not in serialized
    assert rig.config.database.read_bytes() == before
    assert all(c[:2] == ('test', 'test-users') for c in rig.calls)


def test_revocation_reactivation_and_rotation(rig):
    original = rig.reader.observe()[1][0]['revision']
    rig.state['users'][0]['active'] = False
    rig.state['revision'] = '2'
    revoked = rig.reader.observe()[1][0]
    assert revoked['reason'] == 'inactive_identity' and revoked['recipient_email'] is None
    assert revoked['revision'] != original
    rig.state['users'][0]['active'] = True
    rig.state['revision'] = '3'
    assert rig.reader.observe()[1][0]['revision'] not in (original, revoked['revision'])
    rig.state['users'][0]['sha256'] = '0' * 64
    rig.state['revision'] = '4'
    assert rig.reader.observe()[1][0]['reason'] == 'binding_mismatch'


@pytest.mark.parametrize('field,value,reason', [('email', 'other@example.test', 'binding_mismatch'),
    ('identity', 'other', 'binding_mismatch'), ('status', 'PENDING', 'unapproved_request'),
    ('delivery_state', 'FAILED', 'delivery_not_sent'), ('delivery_state', 'PENDING', 'delivery_not_sent')])
def test_binding_and_delivery_fail_closed(rig, field, value, reason):
    update(rig, f'UPDATE key_requests SET {field}=?', (value,))
    item = rig.reader.observe()[1][0]
    assert item['reason'] == reason and not item['eligible'] and item['recipient_email'] is None


def test_missing_request_and_mapping_disabled(rig):
    update(rig, 'DELETE FROM key_requests')
    assert rig.reader.observe()[1][0]['reason'] == 'unapproved_request'
    rig.binding['members'][0]['enabled'] = False
    rig.config.binding_file.write_text(json.dumps(rig.binding))
    assert rig.reader.observe()[1][0]['reason'] == 'mapping_disabled'


def test_duplicate_or_changed_scope_rejected(rig):
    rig.binding['members'].append(dict(rig.member))
    rig.config.binding_file.write_text(json.dumps(rig.binding))
    with pytest.raises(EvidenceError): rig.reader.observe()
    rig.binding['members'].pop()
    rig.binding['org_id'] = 'org-b'
    rig.config.binding_file.write_text(json.dumps(rig.binding))
    with pytest.raises(EvidenceError): rig.reader.observe()


def test_registry_duplicates_and_malformed_data_rejected(rig):
    rig.state['users'].append(dict(rig.state['users'][0]))
    with pytest.raises(EvidenceError): rig.reader.observe()
    rig.state['users'] = [{'id': 'alice', 'active': True, 'sha256': 'bad'}]
    with pytest.raises(EvidenceError): rig.reader.observe()


def test_registry_change_retries_once_then_fails(rig):
    calls = []
    def get(binding, timeout):
        calls.append(1)
        return str(len(calls)), rig.state['users']
    rig.reader.registry_get = get
    with pytest.raises(EvidenceError): rig.reader.observe()
    assert len(calls) == 4


def test_registry_one_change_recovers(rig):
    versions = iter(['1', '2', '2', '2'])
    rig.reader.registry_get = lambda b, t: (next(versions), rig.state['users'])
    assert rig.reader.observe()[1][0]['eligible']


def test_db_snapshot_is_consistent(rig):
    with sqlite3.connect(rig.config.database) as db: db.execute('PRAGMA journal_mode=WAL')
    calls = []
    def get(binding, timeout):
        calls.append(1)
        if len(calls) == 1: update(rig, "UPDATE key_requests SET delivery_state='FAILED'")
        return '1', rig.state['users']
    rig.reader.registry_get = get
    assert rig.reader.observe()[1][0]['eligible']
    assert not rig.reader.observe()[1][0]['eligible']


def test_cursor_snapshot_expiry_and_scope(rig):
    binding, items, snapshot = rig.reader.observe()
    items += [dict(items[0], subject_id='subject-b')]
    page = rig.reader.page(binding, items, snapshot, 1, None)
    cursor = page['next_cursor']
    assert rig.reader.page(binding, items, snapshot, 1, cursor)['contacts'][0]['subject_id'] == 'subject-b'
    with pytest.raises(SnapshotChanged): rig.reader.page(binding, items, 'new', 1, cursor)
    with pytest.raises(InvalidCursor): rig.reader.page(binding, items, snapshot, 2, cursor)
    with pytest.raises(InvalidCursor): rig.reader.page(binding, items, snapshot, 1, cursor + 'x')
    rig.state['time'] += 301
    with pytest.raises(InvalidCursor): rig.reader.page(binding, items, snapshot, 1, cursor)


def test_more_than_500_contacts_no_truncation(rig):
    rig.binding['members'] = []
    rig.state['users'] = []
    for n in range(501):
        subject, identity, request_id = f'subject-{n:04}', f'user-{n}', f'{n:032x}'
        email, digest = f'user{n}@example.test', hashlib.sha256(identity.encode()).hexdigest()
        rig.binding['members'].append(dict(rig.member, subject_id=subject, identity=identity,
            membership_id=f'member-{n}', request_id=request_id, recipient_email=email))
        rig.state['users'].append({'id': identity, 'sha256': digest, 'active': True})
        update(rig, 'INSERT OR REPLACE INTO key_requests VALUES(?,?,?,?,?,?,?,?,?)',
               (request_id, identity, email, 'APPROVED', 5, digest, 'SENT', 6, None))
    rig.config.binding_file.write_text(json.dumps(rig.binding))
    seen, cursor = [], None
    while True:
        binding, contacts, snapshot = rig.reader.observe()
        page = rig.reader.page(binding, contacts, snapshot, 100, cursor)
        seen.extend(c['subject_id'] for c in page['contacts'])
        cursor = page['next_cursor']
        if not cursor: break
    assert len(seen) == len(set(seen)) == 501


def test_timeout_budget_and_absent_database(rig):
    clock = iter([0, 0, 0, 16])
    rig.reader.monotonic = lambda: next(clock)
    with pytest.raises(EvidenceError): rig.reader.observe()
    rig.config.database.unlink()
    rig.reader.monotonic = lambda: 0
    with pytest.raises(sqlite3.OperationalError): rig.reader.observe()
    assert not rig.config.database.exists()


def test_real_kubernetes_transport_is_get_only(rig, monkeypatch):
    import base64
    import httpx
    from dataclasses import replace
    token = rig.config.database.parent / 'service-token'
    token.write_text('synthetic-kubernetes-token')
    config = replace(rig.config, kube_token=token, kube_ca=Path('/not-read-by-mock'))
    requests = []
    def handle(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/api/v1/namespaces/test/secrets/test-users'
        assert request.headers['authorization'] == 'Bearer synthetic-kubernetes-token'
        return httpx.Response(200, json={'metadata': {'resourceVersion': '1'}, 'data': {
            'users.json': base64.b64encode(json.dumps(rig.state['users']).encode()).decode()}})
    original = httpx.Client
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['verify'] == str(config.kube_ca)
        return original(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, 'Client', client)
    reader = EvidenceReader(config)
    assert reader.observe()[1][0]['eligible']
    assert len(requests) == 2


def test_sqlite_connections_are_read_only(rig, monkeypatch):
    original = sqlite3.connect
    def connect(path, **kwargs):
        assert path.endswith('?mode=ro') and kwargs['uri'] is True
        db = original(path, **kwargs)
        with pytest.raises(sqlite3.OperationalError): db.execute('DELETE FROM key_requests')
        db.rollback()
        return db
    monkeypatch.setattr(sqlite3, 'connect', connect)
    assert rig.reader.observe()[1][0]['eligible']


def test_mapping_change_during_observation_retried(rig):
    calls = []
    def get(binding, timeout):
        calls.append(1)
        if len(calls) == 2:
            rig.binding['members'][0]['enabled'] = False
            rig.binding['revision'] = 'mapping-v2'
            rig.config.binding_file.write_text(json.dumps(rig.binding))
        return '1', rig.state['users']
    rig.reader.registry_get = get
    assert rig.reader.observe()[1][0]['reason'] == 'mapping_disabled'
    assert len(calls) == 4
