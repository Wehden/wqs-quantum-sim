import hashlib
import io
import tarfile
import json
from pathlib import Path
import pytest
from tools.cd import release

SHA = 'a' * 40


def event(**changes):
    job = {'head_repository': {'full_name': release.REPOSITORY}, 'event': 'push', 'head_branch': 'main',
           'name': 'Merge pull request: tested change', 'path': '.github/workflows/ci.yml', 'conclusion': 'success', 'head_sha': SHA}
    job.update(changes)
    return {'repository': {'full_name': release.REPOSITORY}, 'workflow_run': job}


def config(tmp_path):
    (tmp_path / 'smoke-token').write_text('synthetic-token')
    return {'bootstrap_verified': True, 'namespace': 'test', 'registry': 'registry.example.invalid/wqs',
            'state_dir': str(tmp_path/'state'), 'smoke_owner': 'verifier',
            'roles': {role: {'deployment': role, 'container': role} for role in release.ROLES},
            'backup_command': ['backup'], 'verify_command': ['verify'],
            'acceptance': {'api_url': 'https://api.example.invalid',
                           'admin_health_url': 'http://admin.example.invalid/healthz',
                           'token_file': str(tmp_path / 'smoke-token')},
            'required_secrets': [{'name': 'example-credentials', 'keys': ['token']}]}


def evidence():
    return {'sha': SHA, 'roles_healthy': sorted(release.ROLES), 'controller_progress': True,
            'cases': {name: True for name in release.SMOKE_CASES}}


def bundle(tmp_path):
    for target in release.TARGETS:
        image = 'wqs-release-ci:' + target + '-' + SHA
        cfg = {'os': 'linux', 'architecture': 'amd64', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:'+'c'*64]},
               'config': {'Labels': {'org.opencontainers.image.revision': SHA}, 'Cmd': ['test']}}
        payload = json.dumps(cfg).encode()
        with tarfile.open(tmp_path/(target+'.tar'), 'w') as archive:
            for name, data in [('config.json', payload), ('manifest.json', json.dumps([
                    {'Config': 'config.json', 'RepoTags': [image], 'Layers': []}]).encode())]:
                info = tarfile.TarInfo(name); info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        digest = hashlib.sha256((tmp_path/(target+'.tar')).read_bytes()).hexdigest()
        (tmp_path/(target+'.json')).write_text(json.dumps({'schema': 1, 'source_sha': SHA, 'target': target,
            'platform': 'linux/amd64', 'image_id': 'sha256:'+'b'*64, 'archive': target+'.tar',
            'image_ref': image, 'config_digest': 'sha256:'+hashlib.sha256(payload).hexdigest(),
            'archive_sha256': digest}))
    return tmp_path


def test_main_gate_rejects_untrusted_and_failed_events(monkeypatch):
    monkeypatch.setattr(release, 'current', lambda sha: True)
    assert release.gate(event()) == SHA
    for change in ({'event': 'pull_request'}, {'head_branch': 'feature'}, {'conclusion': 'failure'},
                   {'head_repository': {'full_name': 'fork/repo'}}, {'head_sha': 'main'}, {'name': 'CI', 'path': '.github/workflows/other.yml'},
                   {'name': 'CI', 'path': None}):
        assert release.gate(event(**change)) == ''
    monkeypatch.setattr(release, 'current', lambda sha: False)
    assert release.gate(event()) == ''


@pytest.mark.parametrize('title', ['CI', 'Merge pull request #33\n\nBackup hook', 'ci-local-attempt'])
def test_main_gate_uses_workflow_path_instead_of_display_title(monkeypatch, title):
    monkeypatch.setattr(release, 'current', lambda sha: True)
    assert release.gate(event(name=title)) == SHA
    # Display-title changes never relax event, repository or branch checks.
    assert release.gate(event(name=title, event='workflow_dispatch')) == ''
    assert release.gate(event(name=title, path='.github/workflows/cd.yml')) == ''


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

    def preflight_secrets(self):
        self.calls.append('secrets')

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
        {'evidence_type': 'creation_result', 'created': True, 'backup_id': kw['env']['WQS_BACKUP_SET_ID']} if args == ['backup'] else evidence()))


def test_success_order_and_rerun_does_not_redeploy(tmp_path, services):
    deploy = Fake(config(tmp_path))
    record = deploy.deploy(bundle(tmp_path), SHA, '123')
    assert record['status'] == 'verified'
    assert deploy.calls == ['secrets', 'status', 'publish', 'hold', 'status', 'secrets', 'controller', 'observer', 'admin', 'api', 'smoke', 'status', 'open']
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


@pytest.mark.parametrize('value', [False, None, 'false', 'true', 0, 1, {}, []])
def test_bootstrap_requires_explicit_boolean_acknowledgement(tmp_path, value):
    cfg = config(tmp_path)
    cfg['bootstrap_verified'] = value
    with pytest.raises(release.BootstrapUnverified):
        release.validate_config(cfg)


@pytest.mark.parametrize('action', ['preflight', 'deploy'])
def test_cli_bootstrap_failure_is_actionable_and_sanitized(tmp_path, monkeypatch, action):
    cfg = config(tmp_path)
    cfg['bootstrap_verified'] = False
    cfg['registry'] = 'private-inventory-do-not-publish'
    source = tmp_path / 'private.json'
    source.write_text(json.dumps(cfg))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(tmp_path / 'summary'))
    monkeypatch.setattr('sys.argv', ['release.py', action, '--sha', SHA, '--run-id', '123', '--config', str(source)])
    monkeypatch.setattr(release, 'run', lambda *a, **kw: pytest.fail('preflight must not run external commands'))
    monkeypatch.setattr(release, 'github', lambda *a: pytest.fail('bootstrap must fail before GitHub calls'))
    with pytest.raises(SystemExit, match='CD blocked: bootstrap_verified') as error:
        release.main()
    assert error.value.code != 0
    receipt = json.loads((tmp_path / 'release-receipt.json').read_text())
    assert receipt == {'schema': 1, 'sha': SHA, 'ci_run': '123', 'status': 'blocked',
                       'stage': action, 'reason': 'bootstrap_unverified'}
    assert 'WQS_CD_ENABLED' in (tmp_path / 'summary').read_text()
    assert not (tmp_path / 'state').exists()
    assert 'private-inventory' not in str(error.value) + (tmp_path / 'summary').read_text() + json.dumps(receipt)


def test_cli_preflight_validates_without_deployment(tmp_path, monkeypatch):
    source = tmp_path / 'private.json'
    source.write_text(json.dumps(config(tmp_path)))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['release.py', 'preflight', '--config', str(source)])
    monkeypatch.setattr(release.Deployer, 'deploy', lambda *a: pytest.fail('preflight must not deploy'))
    release.main()
    assert not (tmp_path / 'state').exists()


def test_cli_unexpected_preflight_error_does_not_expose_details(tmp_path, monkeypatch):
    source = tmp_path / 'private.json'
    source.write_text('secret malformed configuration')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['release.py', 'preflight', '--config', str(source)])
    with pytest.raises(SystemExit, match='Release failed: JSONDecodeError') as error:
        release.main()
    receipt = json.loads((tmp_path / 'release-receipt.json').read_text())
    assert receipt['status'] == 'failed' and receipt['reason'] == 'release_failed'
    assert 'secret' not in str(error.value) + json.dumps(receipt)


def test_workflow_preflight_precedes_artifact_download():
    import yaml
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / '.github/workflows/cd.yml').read_text())
    steps = workflow['jobs']['deploy']['steps']
    preflight = next(i for i, step in enumerate(steps) if 'release.py preflight' in step.get('run', ''))
    download = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('actions/download-artifact@'))
    assert preflight < download
    assert 'rm -f release-receipt.json' in steps[preflight]['run']
    assert next(step for step in steps if step.get('name') == 'Retain sanitized receipt')['if'] == 'always()'


def test_manual_hosted_validation_preserves_default():
    from tools.ci.route import choose
    assert choose({'inputs': {'hosted_tests': 'true'}}, 'workflow_dispatch', 'test/repo', 'refs/heads/main', '')[0] == 'hosted'
    assert choose({}, 'workflow_dispatch', 'test/repo', 'refs/heads/main', 'true',
                  json.dumps(dict(schema=1, observed_at=1000, capacity=2, online=2, busy=0, idle=2)), now=1000)[0] == 'local'


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
            return json.dumps([{'Id': 'wrong', 'Os': 'windows', 'Architecture': 'amd64'}])
        return ''
    monkeypatch.setattr(release, 'run', command)
    with pytest.raises(ValueError, match='content'):
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


def test_loaded_content_uses_portable_config_and_layer_identity(tmp_path):
    root = bundle(tmp_path)
    record = release.validate_bundle(root, SHA)['api']
    _, expected = release.archive_config(root/'api.tar', record['image_ref'])
    actual = {'Id': 'different-backend-id', 'Os': 'linux', 'Architecture': 'amd64',
              'RootFS': {'Type': 'layers', 'Layers': expected['rootfs']['diff_ids']},
              'Config': {**expected['config'], 'LegacyEmpty': ''}}
    release.verify_loaded_content(actual, expected, SHA)
    actual['RootFS']['Layers'] = ['sha256:'+'d'*64]
    with pytest.raises(ValueError, match='content'):
        release.verify_loaded_content(actual, expected, SHA)


@pytest.mark.parametrize('strategy', [{}, {'type': 'RollingUpdate'}, {'type': 'Recreate'}])
def test_controller_strategy_is_checked_in_preflight(tmp_path, monkeypatch, strategy):
    deploy = release.Deployer(config(tmp_path))
    def kube(*args, **kwargs):
        role = args[2]
        return json.dumps({'spec': {'replicas': 1, 'strategy': strategy,
            'template': {'spec': {'containers': [{'name': role, 'image': 'old', 'env': []}]}}}})
    monkeypatch.setattr(deploy, 'kube', kube)
    if strategy.get('type') == 'Recreate':
        assert set(deploy.capture()) == set(release.ROLES)
    else:
        with pytest.raises(ValueError, match='Recreate'):
            deploy.capture()


def test_record_creates_fixed_tag_and_safe_notes_with_installed_gh_flags(tmp_path, monkeypatch):
    cfg = config(tmp_path); Path(cfg['state_dir']).mkdir()
    receipt = {'sha': SHA, 'status': 'verified', 'acceptance': evidence(), 'ci_run': '123',
               'images': {'api': 'private-inventory-must-not-leak'}}
    (Path(cfg['state_dir'])/(SHA+'.json')).write_text(json.dumps(receipt))
    calls = []
    def command(args, *a, **kw):
        calls.append(args)
        return '[]' if 'matching-refs' in args[-1] else ''
    monkeypatch.setattr(release, 'run', command)
    release.publish_version(cfg, SHA, 'v0.1.0')
    assert any('sha='+SHA in args and 'ref=refs/tags/v0.1.0' in args for args in calls)
    assert not any('--slurp' in args for args in calls)
    assert 'private-inventory' not in str(calls)


@pytest.mark.parametrize('failure', [None, 'load', 'content', 'tag', 'push', 'digest'])
def test_publication_cleans_only_attempted_release_tags(tmp_path, monkeypatch, failure):
    records = release.validate_bundle(bundle(tmp_path), SHA)
    source = records['api']['image_ref']
    destination = config(tmp_path)['registry'] + '/api:' + SHA
    _, expected = release.archive_config(tmp_path/'api.tar', source)
    actual = {'Os': expected['os'], 'Architecture': expected['architecture'],
              'RootFS': {'Type': 'layers', 'Layers': expected['rootfs']['diff_ids']},
              'Config': expected['config']}
    calls = []
    def command(args, *a, **kw):
        calls.append(args)
        stage = args[1]
        if stage in ('load', 'tag', 'push') and failure == stage:
            raise RuntimeError('original ' + stage + ' failure')
        if args[:3] == ['docker', 'image', 'inspect']:
            if args[-1] == source:
                return json.dumps([{**actual, 'Os': 'windows'} if failure == 'content' else actual])
            return json.dumps([{'RepoDigests': [] if failure == 'digest' else [
                config(tmp_path)['registry']+'/api@sha256:'+'b'*64]}])
        return ''
    monkeypatch.setattr(release, 'run', command)
    deploy = release.Deployer(config(tmp_path))
    if failure:
        with pytest.raises((RuntimeError, ValueError)):
            deploy.publish(tmp_path, {'api': records['api']}, SHA)
    else:
        assert deploy.publish(tmp_path, {'api': records['api']}, SHA)['api'].endswith('@sha256:'+'b'*64)
    removed = [args[-1] for args in calls if args[:3] == ['docker', 'image', 'rm']]
    assert removed == ([source] if failure in ('load', 'content') else [destination, source])
    assert not any('prune' in args or '--force' in args for args in calls)
    if failure is None:
        assert calls.index(['docker', 'image', 'inspect', destination]) < calls.index(['docker', 'image', 'rm', destination])


@pytest.mark.parametrize('publish_fails', [True, False])
def test_cleanup_attempts_both_tags_and_preserves_original_error(tmp_path, monkeypatch, capsys, publish_fails):
    import subprocess
    records = release.validate_bundle(bundle(tmp_path), SHA)
    source = records['api']['image_ref']
    destination = config(tmp_path)['registry'] + '/api:' + SHA
    _, expected = release.archive_config(tmp_path/'api.tar', source)
    removed = []
    def command(args, *a, **kw):
        if args[:3] == ['docker', 'image', 'rm']:
            removed.append(args[-1])
            if args[-1] == destination:
                raise subprocess.CalledProcessError(1, args)
        if args[:2] == ['docker', 'push'] and publish_fails:
            raise ValueError('original push error')
        if args[:3] == ['docker', 'image', 'inspect']:
            if args[-1] == source:
                return json.dumps([{'Os': 'linux', 'Architecture': 'amd64', 'Config': expected['config'],
                    'RootFS': {'Type': 'layers', 'Layers': expected['rootfs']['diff_ids']}}])
            return json.dumps([{'RepoDigests': [config(tmp_path)['registry']+'/api@sha256:'+'b'*64]}])
        return ''
    monkeypatch.setattr(release, 'run', command)
    with pytest.raises(ValueError if publish_fails else RuntimeError,
                       match='original push error' if publish_fails else 'cleanup incomplete'):
        release.Deployer(config(tmp_path)).publish(tmp_path, {'api': records['api']}, SHA)
    assert removed == [destination, source]
    assert 'cleanup failed' in capsys.readouterr().err


@pytest.mark.parametrize('references', [None, [], [{}], [{'name': '-bad', 'keys': ['token']}],
    [{'name': 'good', 'keys': []}], [{'name': 'good', 'keys': ['token', 'token']}],
    [{'name': 'good', 'keys': ['bad/key']}], [{'name': 'good', 'keys': [None]}],
    [{'name': 'a..b', 'keys': ['token']}], [{'name': 'a'*64, 'keys': ['token']}],
    [{'name': 'good', 'keys': ['token'], 'namespace': 'other'}],
    [{'name': 'good', 'keys': ['token']}] * 2,
    [{'name': f'secret-{i}', 'keys': ['token']} for i in range(17)]])
def test_secret_inventory_is_required_bounded_and_validated(tmp_path, references):
    cfg = config(tmp_path); cfg['required_secrets'] = references
    with pytest.raises(ValueError): release.Deployer(cfg)


def test_secret_check_only_gets_named_resource_and_nonempty_key_names(tmp_path, monkeypatch):
    calls = []
    def command(args, timeout=120, **kwargs):
        calls.append((args, timeout))
        return 'token\nextra-key'
    monkeypatch.setattr(release, 'run', command)
    release.Deployer(config(tmp_path)).preflight_secrets()
    args, timeout = calls[0]
    assert args[:6] == ['kubectl', '-n', 'test', 'get', 'secret', 'example-credentials']
    assert '--request-timeout=8s' in args and timeout == 10
    assert '{{if $value}}{{$key}}' in args[-1] and '{{$value}}' not in args[-1]
    assert len(calls) == 1


@pytest.mark.parametrize('keys', ['', 'unrelated\n'])
def test_secret_check_rejects_missing_or_empty_keys(tmp_path, monkeypatch, keys):
    deploy = release.Deployer(config(tmp_path))
    monkeypatch.setattr(deploy, 'kube', lambda *a, **kw: keys)
    with pytest.raises(ValueError, match='missing or empty'): deploy.preflight_secrets()


@pytest.mark.parametrize('kind', ['denied', 'timeout', 'missing-binary'])
def test_secret_read_errors_are_bounded_and_sanitized(tmp_path, monkeypatch, capsys, kind):
    import subprocess
    deploy = release.Deployer(config(tmp_path)); calls = []
    canary = 'synthetic-credential-DO-NOT-LOG'
    def kube(*args, **kwargs):
        calls.append(args)
        if kind == 'denied': raise subprocess.CalledProcessError(1, args, output=canary, stderr=canary)
        if kind == 'timeout': raise subprocess.TimeoutExpired(args, 10, output=canary, stderr=canary)
        raise FileNotFoundError(canary)
    monkeypatch.setattr(deploy, 'kube', kube)
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError) as error: deploy.preflight_secrets()
    import traceback
    assert canary not in ''.join(traceback.format_exception(error.type, error.value, error.tb))
    assert capsys.readouterr() == ('', '')
    assert len(calls) == 2


def test_secret_check_recovers_from_one_transient_failure(tmp_path, monkeypatch):
    import subprocess
    deploy = release.Deployer(config(tmp_path)); calls = []
    def kube(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1: raise subprocess.TimeoutExpired(args, 10)
        return 'token'
    monkeypatch.setattr(deploy, 'kube', kube)
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    deploy.preflight_secrets()
    assert len(calls) == 2


@pytest.mark.parametrize('failure_at', [1, 2])
def test_missing_secrets_block_before_side_effects_or_rollout(tmp_path, services, monkeypatch, failure_at):
    deploy = Fake(config(tmp_path)); checks = []
    def preflight():
        checks.append(True)
        if len(checks) == failure_at: raise ValueError('Required secret missing')
    monkeypatch.setattr(deploy, 'preflight_secrets', preflight)
    with pytest.raises(ValueError, match='Required secret'): deploy.deploy(bundle(tmp_path), SHA, '123')
    assert not any(role in deploy.calls for role in release.ROLES)
    if failure_at == 1:
        assert deploy.calls == []
        assert not (tmp_path/'state/deployment.json').exists()
    else:
        assert deploy.held and 'open' not in deploy.calls
        assert json.loads((tmp_path/'state/deployment.json').read_text())['status'] == 'failed'


@pytest.mark.parametrize('case', ['missing', 'url', 'port', 'path', 'unreadable', 'empty', 'newline'])
def test_acceptance_preflight_before_side_effects(tmp_path, monkeypatch, case):
    cfg = config(tmp_path)
    if case == 'missing':
        del cfg['acceptance']
    elif case == 'url':
        cfg['acceptance']['api_url'] = 'file:///tmp/api'
    elif case == 'port':
        cfg['acceptance']['admin_health_url'] = 'https://admin.example.invalid:bad'
    elif case == 'path':
        cfg['acceptance']['token_file'] = 'relative'
    elif case == 'unreadable':
        (tmp_path / 'smoke-token').unlink()
    else:
        (tmp_path / 'smoke-token').write_text('' if case == 'empty' else 'one\ntwo')
    calls = []
    monkeypatch.setattr(release, 'run', lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError):
        release.Deployer(cfg)
    assert not calls
    assert not (tmp_path / 'state').exists()


@pytest.mark.parametrize('raw', [
    '{}', '[]', '{"evidence_type":"final_result","verified":true,"backup_id":"expected"}',
    '{"evidence_type":"creation_result","created":1,"backup_id":"expected"}',
    '{"evidence_type":"creation_result","created":true,"backup_id":"other"}',
    '{"evidence_type":"creation_result","created":false,"backup_id":null,"failure":"load_failed"}',
    '{"evidence_type":"creation_result","created":true,"backup_id":"expected","extra":true}',
    '{"evidence_type":"creation_result","created":false,"created":true,"backup_id":"expected"}',
])
def test_creation_result_rejects_ambiguous_or_wrong_receipts(raw):
    with pytest.raises(ValueError):
        release.verify_creation(raw, 'expected')


def test_creation_result_matches_exact_invocation():
    assert release.verify_creation('{"evidence_type":"creation_result","created":true,"backup_id":"expected"}', 'expected')['created'] is True


@pytest.mark.parametrize('failure', ['nonzero', 'wrong-set', 'restore'])
def test_failed_creation_never_applies_images(tmp_path, services, monkeypatch, failure):
    import subprocess
    def backup(*args, **kwargs):
        if failure == 'nonzero':
            raise subprocess.CalledProcessError(1, ['backup'])
        return json.dumps({'evidence_type': 'final_result', 'verified': True, 'backup_id': 'wrong'} if failure == 'restore' else
                          {'evidence_type': 'creation_result', 'created': True, 'backup_id': 'wrong'})
    monkeypatch.setattr(release, 'run', backup)
    deploy = Fake(config(tmp_path))
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        deploy.deploy(bundle(tmp_path), SHA, '123')
    assert deploy.held
    assert not set(release.ROLES).intersection(deploy.calls)
    assert 'open' not in deploy.calls
