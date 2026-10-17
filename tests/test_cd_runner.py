import copy
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from tools.cd import check_runner_access as access
from tools.cd import release

ROOT = Path(__file__).resolve().parents[1]


def group():
    return dict(name=access.GROUP, visibility='selected', default=False, inherited=False,
                allows_public_repositories=True, restricted_to_workflows=True,
                selected_workflows=[access.WORKFLOW])


def test_only_exact_workflow_main_and_repository_are_accepted():
    access.check(group(), [access.REPOSITORY])
    for change in [dict(visibility='all'), dict(default=True), dict(inherited=True),
                   dict(restricted_to_workflows=False), dict(selected_workflows=[]),
                   dict(selected_workflows=[access.WORKFLOW.replace('main', '*')]),
                   dict(selected_workflows=[access.WORKFLOW, 'other']),
                   dict(allows_public_repositories=False)]:
        candidate = group() | change
        with pytest.raises(ValueError):
            access.check(candidate, [access.REPOSITORY])
    for repos in [[], [access.REPOSITORY, 'other/repo']]:
        with pytest.raises(ValueError):
            access.check(group(), repos)


def test_missing_group_fields_fail_closed():
    for key in group():
        candidate = copy.deepcopy(group())
        candidate.pop(key)
        with pytest.raises(ValueError):
            access.check(candidate, [access.REPOSITORY])


def test_github_uses_paginated_compatible_cli(monkeypatch):
    def run(argv, **kwargs):
        assert '--paginate' in argv and '--slurp' not in argv
        assert kwargs['capture_output'] and kwargs['check'] and kwargs['timeout'] == 60
        return subprocess.CompletedProcess(argv, 0, '{"id":1}\n{"id":2}\n')
    monkeypatch.setattr(access.subprocess, 'run', run)
    assert access.github('example', 'repositories') == [{'id': 1}, {'id': 2}]


def test_example_configuration_cannot_enable_cd():
    config = json.loads((ROOT / 'tools/cd/release-config.example.json').read_text())
    with pytest.raises(ValueError):
        release.validate_config(config)
    config['bootstrap_verified'] = True
    release.validate_config(config)


def test_runner_is_stopped_separate_and_has_no_host_mounts():
    objects = list(yaml.safe_load_all((ROOT / 'tools/cd/runner.example.yaml').read_text()))
    deployment = next(obj for obj in objects if obj['kind'] == 'Deployment')
    assert deployment['spec']['replicas'] == 0
    assert deployment['spec']['strategy']['type'] == 'Recreate'
    pod = deployment['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert all('hostPath' not in volume for volume in pod['volumes'])
    runner, daemon = pod['containers']
    assert runner['securityContext']['runAsNonRoot'] is True
    assert all(mount['name'] not in {'api-token', 'private', 'registration'}
               for mount in daemon['volumeMounts'])
    workflow = yaml.safe_load((ROOT / '.github/workflows/cd.yml').read_text())
    assert workflow['jobs']['deploy']['runs-on']['group'] == access.GROUP


def test_named_rbac_matches_example_configuration():
    config = json.loads((ROOT / 'tools/cd/release-config.example.json').read_text())
    role, binding = yaml.safe_load_all((ROOT / 'tools/cd/runner-rbac.example.yaml').read_text())
    assert role['kind'] == 'Role' and role['metadata']['namespace'] == config['namespace']
    rules = {rule['resources'][0]: rule for rule in role['rules']}
    assert rules['secrets']['verbs'] == ['get']
    assert set(rules['secrets']['resourceNames']) == {s['name'] for s in config['required_secrets']}
    assert set(rules['deployments']['resourceNames']) == {r['deployment'] for r in config['roles'].values()}
    assert binding['subjects'][0]['name'] == 'wqs-deployer'
