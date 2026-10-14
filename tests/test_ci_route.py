from tools.ci.route import choose


def test_standpunkt_default_and_operator_override():
    assert choose({},'push','example/project','refs/heads/main','')[0]=='local'
    assert choose({},'push','example/project','refs/heads/main','false')[0]=='hosted'


def test_forks_never_select_private_runner():
    event={'pull_request':{'head':{'repo':{'full_name':'fork/project'}}}}
    assert choose(event,'pull_request','example/project','refs/pull/1/merge','true')[0]=='hosted'
    event['pull_request']['head']['repo']['full_name']='example/project'
    assert choose(event,'pull_request','example/project','refs/pull/1/merge','true')[0]=='local'


def test_manual_and_unknown_events():
    assert choose({},'workflow_dispatch','example/project','refs/heads/main','true')[0]=='local'
    assert choose({},'workflow_dispatch','example/project','refs/heads/feature','true')[0]=='hosted'
    assert choose({},'other','example/project','refs/heads/main','true')[0]=='hosted'


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
