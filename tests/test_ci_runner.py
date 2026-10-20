import json
import os
from pathlib import Path
import subprocess

import yaml


def test_stateful_slots_keep_credentials_and_privileges_separate():
    resources = list(yaml.safe_load_all(Path('tools/ci/runner.example.yaml').read_text()))
    sts = next(r for r in resources if r['kind'] == 'StatefulSet')
    assert sts['metadata']['namespace'] == 'wqs-ci'
    assert sts['metadata']['name'] == 'wqs-ci'
    assert sts['spec']['replicas'] == 2
    assert sts['spec']['volumeClaimTemplates'][0]['metadata']['name'] == 'data'
    pod = sts['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert 'volumes' not in pod  # no shared runner PVC, host paths, or production secrets
    runner = pod['containers'][0]
    assert runner['securityContext']['runAsNonRoot'] is True
    assert runner['resources'] == {'requests': {'cpu': '1', 'memory': '1Gi'},
                                   'limits': {'cpu': '6', 'memory': '5Gi'}}
    env = {e['name']: e for e in runner['env']}
    assert env['RUNNER_NAME']['valueFrom']['fieldRef']['fieldPath'] == 'metadata.name'
    assert set(env) == {'RUNNER_NAME', 'RUNNER_REPOSITORY', 'RUNNER_REGISTRATION_TOKEN'}
    policy = next(r for r in resources if r['kind'] == 'NetworkPolicy')
    assert policy['spec']['ingress'] == []
    assert policy['spec']['podSelector'] == {}


def run_entrypoint(tmp_path, name, cached_name=None, cached_encoding="utf-8"):
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir(); target.mkdir()
    (source / 'config.sh').write_text('#!/bin/bash\nprintf "%s\\n" "$@" > configured-args\n')
    (source / 'run.sh').write_text('#!/bin/bash\ntest -z "${RUNNER_REGISTRATION_TOKEN:-}"\n')
    for file in source.iterdir():
        file.chmod(0o755)
    if cached_name:
        (target / '.runner').write_text(json.dumps({'agentName': cached_name}), encoding=cached_encoding)
    script = Path('tools/ci/runner-entrypoint.sh').read_text().replace('/home/runner/', str(source) + '/').replace('/runner', str(target))
    file = tmp_path / 'entrypoint.sh'; file.write_text(script)
    result = subprocess.run(['bash', str(file)], env={**os.environ, 'RUNNER_NAME': name,
            'RUNNER_REPOSITORY': 'https://github.com/example/project', 'RUNNER_REGISTRATION_TOKEN': 'synthetic-test-token'}, capture_output=True, text=True)
    return result, target


def test_distinct_slot_registration_and_label(tmp_path):
    result, target = run_entrypoint(tmp_path, 'wqs-ci-1')
    assert result.returncode == 0, result.stderr
    args = (target / 'configured-args').read_text().splitlines()
    assert args[args.index('--name') + 1] == 'wqs-ci-1'
    assert args[args.index('--labels') + 1] == 'wqs-ci-local'


def test_cached_slot_restarts_without_reregistration(tmp_path):
    result, target = run_entrypoint(tmp_path, 'wqs-ci-0', 'wqs-ci-0')
    assert result.returncode == 0, result.stderr
    assert not (target / 'configured-args').exists()


def test_shared_or_wrong_slot_state_is_rejected(tmp_path):
    result, target = run_entrypoint(tmp_path, 'wqs-ci-1', 'wqs-ci-0')
    assert result.returncode != 0
    assert not (target / 'configured-args').exists()


def test_bom_cached_slot_restarts_without_reregistration(tmp_path):
    result, target = run_entrypoint(tmp_path, 'wqs-ci-0', 'wqs-ci-0', 'utf-8-sig')
    assert (target / '.runner').read_bytes().startswith(b'\xef\xbb\xbf')
    assert result.returncode == 0, result.stderr
    assert not (target / 'configured-args').exists()


def test_bom_wrong_slot_state_still_rejected(tmp_path):
    result, target = run_entrypoint(tmp_path, 'wqs-ci-1', 'wqs-ci-0', 'utf-8-sig')
    assert (target / '.runner').read_bytes().startswith(b'\xef\xbb\xbf')
    assert result.returncode != 0
    assert 'Runner state belongs to another slot' in result.stderr
    assert not (target / 'configured-args').exists()
