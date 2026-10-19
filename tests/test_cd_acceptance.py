import base64
import copy
import io
import json
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np
import pytest

from qsim import release_control, release_probe, settings, store
from qsim.models import Submission
from test_aer_worker import run_worker
from test_cd_release import SHA, config
from test_service import client, auth
from tools.cd import acceptance, release


@pytest.mark.parametrize('name,body', acceptance.cases().items())
def test_all_smoke_cases_pass_real_workers(tmp_path, name, body):
    request = Submission(**body).model_dump()
    result = run_worker(tmp_path, request)
    assert result['status'] == 'SUCCEEDED', result
    artifact = base64.b64decode(result['artifact'])
    acceptance.verify_result(body, result['summary'], artifact)
    broken = copy.deepcopy(result['summary'])
    broken['engine'] = 'unexpected 1.0'
    with pytest.raises(ValueError):
        acceptance.verify_result(body, broken, artifact)
    with pytest.raises(Exception):
        acceptance.verify_result(body, result['summary'], b'not an NPZ')
    if body['operation'] == 'basis_probabilities':
        broken = {**result['summary'], 'probabilities': [1, 0]}
    else:
        broken = {**result['summary'], 'counts': {'0': 16}}
    with pytest.raises(ValueError):
        acceptance.verify_result(body, broken, artifact)


def test_cases_cover_release_contract_without_probability_shots():
    assert set(acceptance.cases()) == release.SMOKE_CASES
    for body in acceptance.cases().values():
        if body['operation'] == 'basis_probabilities':
            assert 'shots' not in body and 'seed' not in body


def healthy_files():
    now = time.time()
    (settings.DATA / 'controller-heartbeat').write_text(str(now))
    (settings.DATA / 'skypilot-observation.json').write_text(json.dumps({
        'observed_at': now, 'queues_observed_at': now, 'queues_complete': True, 'queue_errors': []}))


def test_probe_requires_smoke_owner_fresh_observer_and_does_not_change_hold(client):
    healthy_files()
    release_control.control('hold', SHA)
    with pytest.raises(ValueError):
        release_probe.probe(SHA, 'alice', [])
    release_control.control('smoke', SHA, 'alice')
    assert release_probe.probe(SHA, 'alice', [])['cleaned']
    with pytest.raises(ValueError):
        release_probe.probe(SHA, 'bob', [])
    (settings.DATA / 'controller-heartbeat').write_text(str(time.time()-181))
    with pytest.raises(ValueError):
        release_probe.probe(SHA, 'alice', [])
    assert release_control.control('status', SHA)['release'] == SHA


def test_probe_native_identity_matches_exact_job_not_other_success(client):
    healthy_files()
    release_control.control('hold', SHA)
    release_control.control('smoke', SHA, 'alice')
    job = client.post('/api/jobs', headers=auth(), json={'circuit': 'M 0'}).json()['id']
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED',cleaned=1 WHERE id=?", (job,))
        db.execute("INSERT INTO job_dispatch(job_id,cluster,launch_id) VALUES(?,'qs-test','request')", (job,))
    rows = [{'job_id': 2, 'status': 'SUCCEEDED'}]
    sky = SimpleNamespace(get=lambda value: (1, None) if value == 'request' else rows,
                          queue=lambda cluster, **kwargs: 'queue')
    with pytest.raises(ValueError):
        release_probe.probe(SHA, 'alice', [job], native=True, sky=sky)
    rows.append({'job_id': 1, 'status': 'RUNNING'})
    assert release_probe.probe(SHA, 'alice', [job], native=True, sky=sky)['native_succeeded'] is False
    rows[-1]['status'] = 'SUCCEEDED'
    assert release_probe.probe(SHA, 'alice', [job], native=True, sky=sky)['native_succeeded'] is True


def hook(tmp_path):
    conf = config(tmp_path)
    Path(conf['state_dir']).mkdir()
    conf['acceptance'] = {'api_url': 'https://api.example.invalid',
                          'admin_health_url': 'http://admin.example.invalid/healthz',
                          'token_file': str(tmp_path / 'token')}
    (tmp_path / 'token').write_text('synthetic')
    return acceptance.Acceptance(conf, SHA)


def test_hook_rejects_wrong_ledger_before_any_network(tmp_path, monkeypatch):
    verifier = hook(tmp_path)
    (Path(verifier.config['state_dir']) / 'deployment.json').write_text(json.dumps({'sha': SHA, 'status': 'failed'}))
    monkeypatch.setattr(verifier, 'request', lambda *args, **kwargs: pytest.fail('network called'))
    with pytest.raises(ValueError, match='ledger'):
        verifier.execute()


def test_redirects_do_not_forward_credentials():
    assert acceptance.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid') is None


def test_deadline_and_artifact_mismatch_fail_closed(tmp_path):
    verifier = hook(tmp_path)
    verifier.deadline = time.monotonic()-1
    with pytest.raises(TimeoutError):
        verifier.remaining()
    artifact = io.BytesIO()
    np.savez_compressed(artifact, measurements=np.zeros((16, 1), dtype=np.uint8))
    with pytest.raises(ValueError):
        acceptance.verify_result(acceptance.cases()['clifft_stim'],
                                 {'engine': 'clifft test', 'operation': 'sample', 'shots': 16, 'counts': {'1': 16}},
                                 artifact.getvalue())


def test_deployment_generation_and_pod_readiness_are_checked(tmp_path, monkeypatch):
    verifier = hook(tmp_path)
    image = 'registry.example.invalid/image@sha256:' + 'b'*64
    deployment = {'metadata': {'generation': 2}, 'spec': {'replicas': 1,
        'selector': {'matchLabels': {'app': 'example'}},
        'template': {'spec': {'containers': [{'name': role, 'image': image,
            'env': [{'name': 'QSIM_WORKER_IMAGE', 'value': image}]} for role in release.ROLES]}}},
        'status': {'observedGeneration': 2, 'replicas': 1, 'readyReplicas': 1, 'updatedReplicas': 1, 'availableReplicas': 1}}
    pod = {'metadata': {}, 'spec': {'containers': [{'name': role, 'image': image} for role in release.ROLES]},
           'status': {'containerStatuses': [{'name': role, 'ready': True, 'imageID': image} for role in release.ROLES]}}
    monkeypatch.setattr(verifier, 'kube', lambda *args: json.dumps(deployment if args[1] == 'deployment' else {'items': [pod]}))
    verifier.roles({target: image for target in release.TARGETS})
    deployment['status']['observedGeneration'] = 1
    with pytest.raises(ValueError):
        verifier.roles({target: image for target in release.TARGETS})
    deployment['status']['observedGeneration'] = 2
    pod['status']['containerStatuses'][0]['ready'] = False
    with pytest.raises(ValueError):
        verifier.roles({target: image for target in release.TARGETS})
