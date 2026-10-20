import json
import pytest
from tools.ci.route import choose

NOW = 1000
STATUS = dict(schema=1, observed_at=NOW, capacity=2, online=2, busy=1, idle=1)


def route(event=None, kind='push', ref='refs/heads/main', enabled='true', status=None):
    return choose(event or {}, kind, 'example/project', ref, enabled,
                  json.dumps(STATUS if status is None else status), now=NOW)[0]


def test_idle_slot_and_operator_override():
    assert route() == 'local'
    assert route(enabled='false') == 'hosted'
    assert route(enabled='') == 'hosted'
    assert route(enabled='unexpected') == 'hosted'


@pytest.mark.parametrize('change', [
    {'busy': 2, 'idle': 0}, {'online': 0, 'busy': 0, 'idle': 0},
    {'observed_at': NOW - 121}, {'observed_at': NOW + 16},
    {'observed_at': float('nan')}, {'observed_at': True},
    {'observed_at': 10**400}, {'observed_at': -(10**400)},
    {'capacity': 3}, {'capacity': True}, {'online': 3, 'busy': 2},
    {'busy': -1}, {'idle': True}, {'online': 1}, {'schema': 2}, {'schema': True},
])
def test_unavailable_or_inconsistent_evidence_is_hosted(change):
    assert route(status=dict(STATUS, **change)) == 'hosted'


@pytest.mark.parametrize('raw', ['', '{', 'null', '[]', '{}', '"text"'])
def test_missing_malformed_evidence_is_hosted(raw):
    assert choose({}, 'push', 'example/project', 'refs/heads/main', 'true', raw, now=NOW)[0] == 'hosted'


def test_freshness_boundaries_and_one_online_slot():
    assert route(status=dict(STATUS, observed_at=NOW-120)) == 'local'
    assert route(status=dict(STATUS, observed_at=NOW+15)) == 'local'
    assert route(status=dict(STATUS, online=1, busy=0, idle=1)) == 'local'


def test_forks_never_select_private_runner():
    event = {'pull_request': {'head': {'repo': {'full_name': 'fork/project'}}}}
    assert route(event, kind='pull_request', ref='refs/pull/1/merge') == 'hosted'
    event['pull_request']['head']['repo']['full_name'] = 'example/project'
    assert route(event, kind='pull_request', ref='refs/pull/1/merge') == 'local'


def test_manual_and_unknown_events():
    assert route(kind='workflow_dispatch') == 'local'
    assert route({'inputs': {'hosted_tests': 'true'}}, kind='workflow_dispatch') == 'hosted'
    assert route(kind='workflow_dispatch', ref='refs/heads/feature') == 'hosted'
    assert route(kind='other') == 'hosted'


def test_workflow_wires_status_and_new_pool_without_changing_image_lane():
    from pathlib import Path
    import yaml
    jobs = yaml.safe_load(Path('.github/workflows/ci.yml').read_text())['jobs']
    assert jobs['route']['steps'][1]['env']['CI_LOCAL_RUNNER_STATUS'] == '${{ vars.CI_LOCAL_RUNNER_STATUS }}'
    assert jobs['local']['runs-on'] == ['self-hosted', 'Linux', 'X64', 'wqs-ci-local']
    assert jobs['worker-image']['runs-on'] == 'ubuntu-24.04'


def test_final_gate_reports_paths_and_never_masks_hosted_failure(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    import yaml
    workflow=yaml.safe_load(Path('.github/workflows/ci.yml').read_text())
    script=workflow['jobs']['result']['steps'][0]['run'].split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    for route,local,hosted,route_result,image,passed in [
        ('local','success','skipped','success','success',True),
        ('local','failure','success','success','success',True),
        ('local','','failure','success','success',False),
        ('hosted','','success','success','success',True),
        ('hosted','','cancelled','success','success',False),
        ('hosted','','success','failure','success',False),
        ('local','success','skipped','success','failure',False),
        ('local','success','skipped','success','cancelled',False),
        ('local','success','skipped','success','skipped',False),
    ]:
        report=tmp_path/'summary.md';report.write_text('')
        env={**os.environ,'IMAGE_RESULT':image,'ROUTE':route,'LOCAL_TESTS':local,'LOCAL_ARTIFACT':local,'HOSTED_RESULT':hosted,'ROUTE_RESULT':route_result,'GITHUB_STEP_SUMMARY':str(report)}
        result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True)
        assert (result.returncode==0)==passed,result.stderr
        assert 'Standpunkt:' in report.read_text() and 'GitHub-hosted' in report.read_text()
