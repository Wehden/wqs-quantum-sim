import pytest
from qsim import release_control, store
from test_service import client, auth

SHA = 'a' * 40
BODY = {'circuit': 'M 0'}


def test_hold_is_durable_and_replay_survives(client):
    headers = {**auth(), 'Idempotency-Key': 'before-release'}
    first = client.post('/api/jobs/stim', headers=headers, json=BODY)
    assert first.status_code == 202
    assert release_control.control('hold', SHA)['pending'] == 1
    store.init()  # restart must not reopen admission
    replay = client.post('/api/jobs/stim', headers=headers, json=BODY)
    assert replay.json()['id'] == first.json()['id']
    assert replay.headers['Idempotency-Replayed'] == 'true'
    assert client.get('/api/jobs', headers=auth()).status_code == 200
    denied = client.post('/api/jobs', headers=auth(), json=BODY)
    assert denied.status_code == 503 and denied.headers['Retry-After'] == '60'
    with pytest.raises(ValueError):
        release_control.control('open', SHA)
    with pytest.raises(ValueError):
        release_control.control('hold', 'b' * 40)


def test_smoke_identity_and_drain_gate(client):
    release_control.control('hold', SHA)
    release_control.control('smoke', SHA, 'alice')
    release_control.control('hold', SHA)  # idempotent retry preserves smoke phase
    assert client.post('/api/jobs', headers=auth('bob'), json=BODY).status_code == 503
    accepted = client.post('/api/jobs', headers=auth(), json=BODY)
    assert accepted.status_code == 202
    with pytest.raises(ValueError):
        release_control.control('open', SHA)
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED',cleaned=1")
    assert release_control.control('open', SHA)['release'] == ''
    assert client.post('/api/jobs', headers=auth('bob'), json=BODY).status_code == 202


def test_invalid_release_does_not_change_admission(client):
    with pytest.raises(ValueError):
        release_control.control('hold', 'main')
    assert release_control.control('status', SHA)['release'] == ''
