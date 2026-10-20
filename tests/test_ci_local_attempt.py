import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('local_attempt', Path(__file__).parents[1] / 'tools/ci/local_attempt.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA = 'a' * 40

class Fake:
    def __init__(self, accepted=False, conclusion=None, raced=False, cancel_stuck=False, child_sha=SHA, marker=False):
        self.now = 0
        self.accepted, self.conclusion, self.raced = accepted, conclusion, raced
        self.cancel_stuck, self.child_sha, self.marker = cancel_stuck, child_sha, marker
        self.cancelled = False
        self.calls = []
        self.parent = dict(run_attempt=1, path='.github/workflows/ci.yml', status='in_progress',
            repository={'id': 1}, head_repository={'id': 1}, event='push', head_branch='main', head_sha=SHA)
    def sleep(self, n): self.now += n
    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if path == '/actions/runs/1': return self.parent
        if path.endswith('/dispatches'): return {}
        if '/runs?event=' in path:
            return {'workflow_runs': [{'id': 2, 'head_sha': self.child_sha, 'display_title': 'ci-local-1-1-' + SHA}]}
        if '/jobs?' in path:
            jobs = [{'name': 'Local execution', 'runner_id': 7 if self.accepted or (self.cancelled and self.raced) else 0}]
            if self.marker: jobs.append({'name': 'Local capacity unavailable', 'conclusion': 'success'})
            return {'jobs': jobs}
        if path.endswith('/cancel'):
            self.cancelled = True
            return {}
        if path == '/actions/runs/2':
            done = self.conclusion is not None or (self.cancelled and not self.cancel_stuck)
            return {'status': 'completed' if done else 'in_progress', 'conclusion': self.conclusion or 'cancelled'}
        raise AssertionError(path)
    def run(self):
        return m.Controller(self, lambda: self.now, self.sleep).run('1','1',SHA,'main',queue_timeout=4,execution_timeout=4)

@pytest.mark.parametrize('conclusion,outcome', [('success','success'), ('failure','failure'), ('cancelled','failure')])
def test_accepted_never_retries(conclusion, outcome):
    f = Fake(accepted=True, conclusion=conclusion)
    assert f.run()['outcome'] == outcome
    assert f.run()['lane'] == 'local'

def test_queue_expiry_confirms_cancellation_before_hosted():
    f = Fake()
    assert f.run()['lane'] == 'hosted'
    assert f.cancelled

def test_assignment_race_fails_without_retry():
    assert Fake(raced=True).run()['outcome'] == 'failure'

def test_cancellation_not_confirmed_fails_closed():
    with pytest.raises(ValueError, match='not confirmed'):
        Fake(cancel_stuck=True).run()

def test_changed_child_head_cancelled():
    f = Fake(child_sha='b'*40)
    with pytest.raises(ValueError, match='advanced'): f.run()
    assert f.cancelled

def test_execution_timeout_is_failure():
    f = Fake(accepted=True)
    assert f.run()['outcome'] == 'failure'
    assert f.cancelled

@pytest.mark.parametrize('accepted,expected', [(True,'hosted'), (False,'local')])
def test_explicit_capacity_requires_accepted_success(accepted, expected):
    assert Fake(accepted=accepted, conclusion='success', marker=True).run()['lane'] == expected

def test_fork_parent_rejected():
    f = Fake(); f.parent['head_repository'] = {'id': 2}
    with pytest.raises(ValueError, match='Untrusted'): f.run()
    assert not any(method == 'POST' for method, _ in f.calls)

def test_pr_requires_exact_recorded_merge_parents():
    f=Fake(); f.parent.update(event='pull_request', head_branch='topic', pull_requests=[{'number':7, 'head': {'repo': {'id':1}, 'sha':SHA}, 'base': {'sha':'b'*40}}])
    def api(method,path,body=None):
        if path.startswith('/git/commits/'): return {'parents':[{'sha':'b'*40},{'sha':SHA}]}
        if path == '/git/ref/pull/7/merge': return {'object': {'sha':'c'*40}}
        return f(method,path,body)
    assert m.validate_parent(api,'1','1','c'*40)['event'] == 'pull_request'
    with pytest.raises(ValueError):
        m.validate_parent(lambda *args: f.parent if args[1]=='/actions/runs/1' else {'parents':[]},'1','1','c'*40)

def test_monitor_transport_failure_cancels_known_child():
    f=Fake()
    def api(method,path,body=None):
        if '/jobs?' in path and not f.cancelled: raise m.APIError('transport')
        return f(method,path,body)
    with pytest.raises(m.APIError):
        m.Controller(api, lambda:f.now, f.sleep).run('1','1',SHA,'main')
    assert f.cancelled

@pytest.mark.parametrize('status,hosted', [(404,True),(422,True),('transport',False),(403,False)])
def test_dispatch_error_never_reposts(status,hosted):
    f=Fake(); posts=[]
    def api(method,path,body=None):
        if path.endswith('/dispatches'):
            posts.append(path); raise m.APIError(status)
        return f(method,path,body)
    controller=m.Controller(api,lambda:f.now,f.sleep)
    if hosted: assert controller.run('1','1',SHA,'main')['lane']=='hosted'
    else:
        with pytest.raises(m.APIError): controller.run('1','1',SHA,'main')
    assert len(posts)==1

def test_unknown_child_never_falls_back():
    f=Fake()
    def api(method,path,body=None):
        if '/runs?event=' in path: return {'workflow_runs':[]}
        return f(method,path,body)
    with pytest.raises(ValueError,match='unresolved'):
        m.Controller(api,lambda:f.now,f.sleep).run('1','1',SHA,'main',queue_timeout=4)

@pytest.mark.parametrize('ref_sha,merge_head,allowed', [('c'*40,SHA,True), ('d'*40,SHA,False), ('c'*40,'e'*40,False)])
def test_stale_api_base_requires_exact_merge_ref_and_trusted_head(ref_sha,merge_head,allowed):
    f=Fake()
    f.parent.update(event='pull_request',head_branch='topic',pull_requests=[{
        'number':7,'head':{'repo':{'id':1},'sha':SHA},'base':{'sha':'b'*40}}])
    def api(method,path,body=None):
        if path.startswith('/git/commits/'):
            return {'parents':[{'sha':'f'*40},{'sha':merge_head}]}
        if path == '/git/ref/pull/7/merge': return {'object':{'sha':ref_sha}}
        return f(method,path,body)
    if allowed: assert m.validate_parent(api,'1','1','c'*40)['event']=='pull_request'
    else:
        with pytest.raises(ValueError,match='trusted PR merge'):
            m.validate_parent(api,'1','1','c'*40)


def test_accepted_polling_reduces_api_budget():
    f=Fake(accepted=True)
    sleeps=[]
    def sleep(n):
        sleeps.append(n); f.sleep(n)
    result=m.Controller(f,lambda:f.now,sleep).run('1','1',SHA,'main',execution_timeout=16)
    assert result['outcome']=='failure'
    assert sleeps==[15,15]

@pytest.mark.parametrize('path,allowed', [('.github/workflows/ci.yml',True),('.github/workflows/ci.yml@main',True),('.github/workflows/ci.yml@refs/heads/main',True),('.github/workflows/other.yml@main',False),('.github/workflows/ci.yml@',False),('.github/workflows/ci.yml@main\nforged',False)])
def test_workflow_identity_optional_ref(path,allowed):
    f=Fake(); f.parent['path']=path
    if allowed: m.validate_parent(f,'1','1',SHA)
    else:
        with pytest.raises(ValueError): m.validate_parent(f,'1','1',SHA)

@pytest.mark.parametrize('known', [True,False])
def test_cleanup_survives_controller_loss(tmp_path,known):
    path=tmp_path/'receipt.json'
    m.write_child_receipt(path, {'child_run_id':2 if known else None,'correlation':'ci-local-1-1-'+SHA})
    f=Fake()
    def api(method,url,body=None):
        result=f(method,url,body)
        if url=='/actions/runs/2':
            result.update(display_title='ci-local-1-1-'+SHA,event='workflow_dispatch',path='.github/workflows/ci.yml@main',repository={'id':1},head_repository={'id':1})
        return result
    m.cleanup(api,path,'1','1',SHA)
    assert f.cancelled
    assert path.stat().st_mode & 0o777 == 0o600

def test_cleanup_refuses_wrong_child(tmp_path):
    path=tmp_path/'receipt.json'
    m.write_child_receipt(path, {'child_run_id':2,'correlation':'ci-local-1-1-'+SHA})
    f=Fake()
    with pytest.raises(ValueError,match='identity mismatch'): m.cleanup(f,path,'1','1',SHA)
    assert not f.cancelled

def test_dispatch_intent_written_before_post():
    f=Fake(accepted=True,conclusion='success'); records=[]
    def api(method,path,body=None):
        if path.endswith('/dispatches'): assert records==[{'child_run_id':None,'correlation':'ci-local-1-1-'+SHA}]
        return f(method,path,body)
    m.Controller(api,lambda:f.now,f.sleep,records.append).run('1','1',SHA,'main')
    assert records[-1]['child_run_id']==2


def test_workflow_signal_and_cleanup_contract():
    import yaml
    workflow=yaml.safe_load((Path(__file__).parents[1]/'.github/workflows/ci.yml').read_text())
    steps=workflow['jobs']['attempt']['steps']
    assert next(s for s in steps if s.get('id')=='attempt')['run'].startswith('exec python3 ')
    cleanup_step=next(s for s in steps if '--cleanup' in s.get('run',''))
    assert cleanup_step['if']=='always()'
    assert cleanup_step['timeout-minutes']==3
    assert '!cancelled()' in workflow['jobs']['local']['if']


def test_terminal_job_refresh_finds_late_capacity_marker():
    f=Fake(accepted=True,conclusion='success',marker=True)
    inventories=0
    def api(method,path,body=None):
        nonlocal inventories
        result=f(method,path,body)
        if '/jobs?' in path:
            inventories+=1
            if inventories==1: result['jobs']=result['jobs'][:1]
        return result
    result=m.Controller(api,lambda:f.now,f.sleep).run('1','1',SHA,'main')
    assert inventories==2
    assert result['lane']=='hosted'
    assert result['outcome']=='capacity_unavailable'
