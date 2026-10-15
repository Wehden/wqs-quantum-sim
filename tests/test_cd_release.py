import hashlib
import json
from pathlib import Path
import pytest
from tools.cd import release

SHA = 'a' * 40


def event(**changes):
    job = {'head_repository': {'full_name': release.REPOSITORY}, 'event': 'push', 'head_branch': 'main',
           'name': 'CI', 'conclusion': 'success', 'head_sha': SHA}
    job.update(changes)
    return {'repository': {'full_name': release.REPOSITORY}, 'workflow_run': job}


def config(tmp_path):
    return {'bootstrap_verified': True, 'namespace': 'test', 'registry': 'registry.example.invalid/wqs',
            'state_dir': str(tmp_path/'state'), 'smoke_owner': 'verifier',
            'roles': {role: {'deployment': role, 'container': role} for role in release.ROLES},
            'backup_command': ['backup'], 'verify_command': ['verify']}


def evidence():
    return {'sha': SHA, 'roles_healthy': sorted(release.ROLES), 'controller_progress': True,
            'cases': {name: True for name in release.SMOKE_CASES}}


def bundle(tmp_path):
    for target in release.TARGETS:
        data = (target + ' image').encode()
        (tmp_path/(target+'.tar')).write_bytes(data)
        (tmp_path/(target+'.json')).write_text(json.dumps({'schema': 1, 'source_sha': SHA, 'target': target,
            'platform': 'linux/amd64', 'image_id': 'sha256:'+'b'*64, 'archive': target+'.tar',
            'archive_sha256': hashlib.sha256(data).hexdigest()}))
    return tmp_path


def test_main_gate_rejects_untrusted_and_failed_events(monkeypatch):
    monkeypatch.setattr(release, 'current', lambda sha: True)
    assert release.gate(event()) == SHA
    for change in ({'event': 'pull_request'}, {'head_branch': 'feature'}, {'conclusion': 'failure'},
                   {'head_repository': {'full_name': 'fork/repo'}}, {'head_sha': 'main'}, {'name': 'Other'}):
        assert release.gate(event(**change)) == ''
    monkeypatch.setattr(release, 'current', lambda sha: False)
    assert release.gate(event()) == ''


def test_bundle_rejects_changed_archive_and_source(tmp_path):
    root = bundle(tmp_path)
    assert set(release.validate_bundle(root, SHA)) == set(release.TARGETS)
    with pytest.raises(ValueError):
        release.validate_bundle(root, 'b'*40)
    (root/'api.tar').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='checksum'):
        release.validate_bundle(root, SHA)


def test_acceptance_requires_all_engines_and_exact_sha():
    release.verify_acceptance(evidence(), SHA)
    for modify in (lambda x: x['cases'].pop('aer_qasm3_sample'),
                   lambda x: x.update(sha='b'*40), lambda x: x.update(controller_progress=False),
                   lambda x: x['cases'].update(qsim_qasm2_sample=False)):
        value = evidence(); modify(value)
        with pytest.raises(ValueError):
            release.verify_acceptance(value, SHA)


class Fake(release.Deployer):
    def __init__(self, cfg, failure=None):
        super().__init__(cfg)
        self.calls = []
        self.held = False
        self.failure = failure

    def control(self, action, sha):
        self.calls.append(action)
        if action == 'hold': self.held = True
        if action == 'open': self.held = False
        return {'protocol': 1, 'release': sha if self.held else '', 'pending': 0}

    def publish(self, root, records, sha):
        self.calls.append('publish')
        return {t: 'registry.example.invalid/'+t+'@sha256:'+'b'*64 for t in release.TARGETS}

    def capture(self):
        return {role: {'image': 'old'} for role in release.ROLES}

    def apply(self, role, images, sha):
        self.calls.append(role)
        if role == self.failure: raise RuntimeError('rollout failed')


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setattr(release, 'current', lambda sha: True)
    monkeypatch.setattr(release, 'github', lambda path: event()['workflow_run'])
    monkeypatch.setattr(release, 'run', lambda args, *a, **kw: json.dumps(
        {'verified': True, 'backup_id': 'local-test-backup'} if args == ['backup'] else evidence()))


def test_success_order_and_rerun_does_not_redeploy(tmp_path, services):
    deploy = Fake(config(tmp_path))
    record = deploy.deploy(bundle(tmp_path), SHA, '123')
    assert record['status'] == 'verified'
    assert deploy.calls == ['status', 'publish', 'hold', 'status', 'controller', 'observer', 'admin', 'api', 'smoke', 'status', 'open']
    assert not deploy.held
    count = len(deploy.calls)
    assert deploy.deploy(tmp_path, SHA, '123')['status'] == 'verified'
    assert len(deploy.calls) == count


@pytest.mark.parametrize('failure', ['controller', 'observer', 'admin', 'api'])
def test_partial_rollout_keeps_hold_and_blocks_blind_retry(tmp_path, services, failure):
    deploy = Fake(config(tmp_path), failure)
    with pytest.raises(RuntimeError):
        deploy.deploy(bundle(tmp_path), SHA, '123')
    assert deploy.held and 'open' not in deploy.calls
    assert json.loads((tmp_path/'state/deployment.json').read_text())['status'] == 'failed'
    with pytest.raises(ValueError, match='operator recovery'):
        deploy.deploy(tmp_path, SHA, '123')


def test_failed_smoke_keeps_admission_closed(tmp_path, services, monkeypatch):
    monkeypatch.setattr(release, 'verify_acceptance', lambda *args: (_ for _ in ()).throw(ValueError('bad result')))
    deploy = Fake(config(tmp_path))
    with pytest.raises(ValueError):
        deploy.deploy(bundle(tmp_path), SHA, '123')
    assert deploy.held and 'open' not in deploy.calls


def test_bootstrap_and_ci_fail_before_mutation(tmp_path, services, monkeypatch):
    cfg = config(tmp_path); cfg['bootstrap_verified'] = False
    with pytest.raises(ValueError): Fake(cfg)
    monkeypatch.setattr(release, 'github', lambda path: event(conclusion='failure')['workflow_run'])
    deploy = Fake(config(tmp_path))
    with pytest.raises(ValueError, match='exact-main'):
        deploy.deploy(bundle(tmp_path), SHA, '123')
    assert deploy.calls == []


def test_manual_hosted_validation_preserves_default():
    from tools.ci.route import choose
    assert choose({'inputs': {'hosted_tests': 'true'}}, 'workflow_dispatch', 'test/repo', 'refs/heads/main', '')[0] == 'hosted'
    assert choose({}, 'workflow_dispatch', 'test/repo', 'refs/heads/main', '')[0] == 'local'


def test_version_requires_verified_record_and_never_retags(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    Path(cfg['state_dir']).mkdir()
    receipt = {'sha': SHA, 'status': 'verified', 'acceptance': evidence(), 'ci_run': '123'}
    (Path(cfg['state_dir'])/(SHA+'.json')).write_text(json.dumps(receipt))
    calls = []
    def command(args, *a, **kw):
        calls.append(args)
        if 'matching-refs' in args[-1]:
            return json.dumps([{'ref': 'refs/tags/v0.1.0', 'object': {'type': 'commit', 'sha': 'b'*40}}])
        return '[]'
    monkeypatch.setattr(release, 'run', command)
    with pytest.raises(ValueError, match='elsewhere'):
        release.publish_version(cfg, SHA, 'v0.1.0')
    assert not any('POST' in c for c in calls)
    with pytest.raises(ValueError):
        release.publish_version(cfg, SHA, '../bad')


def test_image_identity_mismatch_never_pushes(tmp_path, monkeypatch):
    records = release.validate_bundle(bundle(tmp_path), SHA)
    calls = []
    def command(args, *a, **kw):
        calls.append(args)
        if args[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([{'Id': 'wrong', 'Os': 'linux', 'Architecture': 'amd64'}])
        return ''
    monkeypatch.setattr(release, 'run', command)
    with pytest.raises(ValueError, match='identity'):
        release.Deployer(config(tmp_path)).publish(tmp_path, records, SHA)
    assert not any('push' in c for c in calls)


def test_controller_patch_updates_worker_without_replacing_other_config(tmp_path, monkeypatch):
    deploy = release.Deployer(config(tmp_path))
    calls = []
    monkeypatch.setattr(deploy, 'kube', lambda *a, **kw: calls.append(a))
    deploy.apply('controller', {'controller': 'controller-digest', 'worker': 'worker-digest'}, SHA)
    patch = json.loads(calls[0][-1])
    container = patch['spec']['template']['spec']['containers'][0]
    assert container == {'name': 'controller', 'image': 'controller-digest',
                         'env': [{'name': 'QSIM_WORKER_IMAGE', 'value': 'worker-digest', 'valueFrom': None}]}
    assert 'replicas' not in patch['spec']
    assert calls[1][:2] == ('rollout', 'status')
