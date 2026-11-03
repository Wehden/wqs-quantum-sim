import time
from types import SimpleNamespace
import pytest
from qsim import controller, settings, store
from test_service import client, submit, auth, callback

class CapacityError(Exception): pass
class GoneError(Exception): pass

class Sky:
    exceptions = SimpleNamespace(ResourcesUnavailableError=CapacityError, ClusterNotUpError=GoneError, ClusterDoesNotExist=GoneError)
    Task = SimpleNamespace(from_yaml_config=lambda config: config)
    def __init__(self):
        self.calls=[]; self.requests={}; self.errors={}; self.results={}
    def send(self, kind, cluster, task=None):
        rid=str(len(self.requests)+1)
        self.requests[rid]=SimpleNamespace(request_id=rid,name='sky.'+kind,created_at=time.time(),cluster_name=cluster,status=SimpleNamespace(value='RUNNING'))
        self.calls.append((kind,cluster,task,rid))
        return rid
    def launch(self, task, cluster_name, **kw): return self.send('launch',cluster_name,task)
    def exec(self, task, cluster_name): return self.send('exec',cluster_name,task)
    def queue(self, cluster, **kw):
        rid=self.send('queue',cluster);self.results[rid]=[];return rid
    def down(self, cluster): return self.send('down',cluster)
    def api_cancel(self, rid, **kw):
        self.requests[rid].status.value='CANCELLED'
        return self.send('cancel',self.requests[rid].cluster_name)
    def api_status(self,request_ids=None,cluster_name=None,**kw):
        return [r for r in self.requests.values() if (not request_ids or r.request_id in request_ids) and (not cluster_name or r.cluster_name==cluster_name)]
    def get(self,rid):
        if rid in self.errors: raise self.errors[rid]
        assert self.requests[rid].status.value in ('SUCCEEDED','FAILED','CANCELLED')
        return self.results.get(rid)
    def finish(self,rid,error=None):
        self.requests[rid].status.value='FAILED' if error else 'SUCCEEDED'
        if error:self.errors[rid]=error

@pytest.fixture
def sky(monkeypatch):
    fake=Sky();monkeypatch.setattr(controller,'sky_client',lambda:fake);return fake

def status(client,job):return client.get('/api/jobs/'+job,headers=auth()).json()['status']
def dispatch(job):
    with store.connect() as db:return db.execute('SELECT * FROM job_dispatch WHERE job_id=?',(job,)).fetchone()

def test_concurrent_launches_and_restart(client,sky):
    jobs=[submit(client) for _ in range(4)]
    controller.tick()
    assert [c[0] for c in sky.calls]==['launch']*4
    assert all(status(client,j)=='LAUNCHING' for j in jobs)
    controller.tick()
    assert len(sky.calls)==4

def test_capacity_queues_cleans_and_retries_same_identity(client,sky):
    job=submit(client);controller.tick();rid=dispatch(job)['launch_id']
    sky.finish(rid,CapacityError());controller.tick()
    assert status(client,job)=='QUEUED'
    controller.tick();down=dispatch(job)['down_id'];assert down
    controller.tick();assert len(sky.calls)==2
    sky.finish(down);controller.tick()
    assert (settings.DATA/job/'callback-token').exists()
    controller.tick();assert len(sky.calls)==2
    controller.save_dispatch(job,retry_after=0);controller.tick()
    assert sky.calls[-1][0]=='launch'
    assert status(client,job)=='LAUNCHING'

def test_non_capacity_failure_and_cleanup_retry(client,sky):
    job=submit(client);controller.tick();sky.finish(dispatch(job)['launch_id'],ValueError())
    controller.tick();assert status(client,job)=='FAILED'
    controller.tick();sky.finish(dispatch(job)['down_id'],ValueError())
    controller.tick();assert dispatch(job)['down_id'] is None
    controller.save_dispatch(job,retry_after=0);controller.tick()
    sky.finish(dispatch(job)['down_id']);controller.tick()
    assert not (settings.DATA/job/'callback-token').exists()

def test_warm_reuse_and_job_credentials(client,sky):
    first=submit(client);controller.tick();sky.finish(dispatch(first)['launch_id'])
    with store.connect() as db:db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(first,))
    second=submit(client);controller.tick()
    assert sky.calls[-1][0]=='exec'
    assert sky.calls[-1][1]==sky.calls[0][1]
    assert sky.calls[-1][2]['envs']['QSIM_JOB_URL'].endswith(second)
    assert sky.calls[-1][2]['envs']['QSIM_JOB_TOKEN'] != sky.calls[0][2]['envs']['QSIM_JOB_TOKEN']
    third=submit(client);controller.tick()
    assert sky.calls[-1][0]=='launch' # busy worker cannot be assigned twice

def test_lost_ack_reconciles_without_duplicate(client,sky,monkeypatch):
    original=sky.launch
    def lost(*a,**kw):original(*a,**kw);raise TimeoutError()
    monkeypatch.setattr(sky,'launch',lost)
    job=submit(client);controller.tick()
    assert dispatch(job)['intent']=='launch'
    controller.tick()
    assert dispatch(job)['launch_id']=='1'
    controller.tick();assert len(sky.calls)==1

def test_cancel_settles_launch_before_teardown(client,sky):
    job=submit(client);controller.tick()
    client.post('/api/jobs/'+job+'/cancel',headers=auth());controller.tick()
    assert sky.calls[-1][0]=='cancel'
    controller.tick();assert sky.calls[-1][0]=='down'
    sky.finish(dispatch(job)['down_id']);controller.tick()
    assert client.delete('/api/jobs/'+job,headers=auth('bob')).status_code==404
    assert client.delete('/api/jobs/'+job,headers=auth()).status_code==204

def test_long_running_legacy_job_has_no_deadline(client,sky):
    job=submit(client)
    with store.connect() as db:db.execute("UPDATE jobs SET status='RUNNING',started=? WHERE id=?",(time.time(),job))
    controller.tick();assert not sky.calls
    with store.connect() as db:db.execute('UPDATE jobs SET started=? WHERE id=?',(time.time()-1300,job))
    controller.tick();assert status(client,job)=='RUNNING';assert not sky.calls

def test_saturated_job_does_not_block_other_dispatch(client,sky):
    first=submit(client);controller.tick()
    sky.finish(dispatch(first)['launch_id'],CapacityError());controller.tick()
    second=submit(client);controller.tick()
    assert status(client,first)=='QUEUED'
    assert status(client,second)=='LAUNCHING'
    assert sky.calls[-1][0]=='launch'

def test_old_worker_can_be_reused_after_long_job(client,sky):
    first=submit(client);controller.tick();sky.finish(dispatch(first)['launch_id'])
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(first,))
        db.execute('UPDATE worker_pool SET created=?',(time.time()-850,))
    controller.tick();submit(client);controller.tick()
    assert [c[0] for c in sky.calls]==['launch','exec']

def test_cancel_during_capacity_retry_never_requeues(client,sky):
    job=submit(client);controller.tick();sky.finish(dispatch(job)['launch_id'],CapacityError())
    controller.tick();client.post('/api/jobs/'+job+'/cancel',headers=auth())
    controller.tick();sky.finish(dispatch(job)['down_id']);controller.tick()
    assert status(client,job)=='CANCELLED'
    assert client.delete('/api/jobs/'+job,headers=auth()).status_code==204


def test_sdk_string_status(sky):
    rid=sky.send('launch','test')
    sky.requests[rid].status='SUCCEEDED'
    assert controller.request_state(sky,rid)=='SUCCEEDED'

def test_absent_partial_cluster_finishes_capacity_cleanup(client,sky):
    job=submit(client);controller.tick();sky.finish(dispatch(job)['launch_id'],CapacityError())
    controller.tick();controller.tick();sky.finish(dispatch(job)['down_id'],GoneError())
    controller.tick()
    assert dispatch(job)['retry_pending']==0
    assert status(client,job)=='QUEUED'


def test_active_job_survives_two_days_then_completes_and_reuses_worker(client,sky,monkeypatch):
    job=submit(client);controller.tick();rid=dispatch(job)['launch_id']
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='RUNNING' WHERE id=?",(job,))
    future=time.time()+2*86400
    monkeypatch.setattr(controller.time,'time',lambda:future)
    controller.tick()
    assert status(client,job)=='RUNNING'
    assert [c[0] for c in sky.calls]==['launch']
    with store.connect() as db:
        assert db.execute('SELECT job_id FROM worker_pool').fetchone()['job_id']==job
        db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(job,))
    sky.finish(rid);controller.tick()
    with store.connect() as db:
        assert db.execute('SELECT cleaned FROM jobs WHERE id=?',(job,)).fetchone()['cleaned']==1
    submit(client);controller.tick()
    assert [c[0] for c in sky.calls]==['launch','exec']


def test_ambiguous_submission_does_not_expire_or_duplicate(client,sky,monkeypatch):
    def lost(*args,**kwargs):raise TimeoutError()
    monkeypatch.setattr(sky,'launch',lost)
    job=submit(client);controller.tick()
    future=time.time()+2*86400
    monkeypatch.setattr(controller.time,'time',lambda:future)
    controller.tick()
    assert status(client,job)=='LAUNCHING'
    assert dispatch(job)['intent']=='launch'
    assert not sky.calls


def idle_worker(client, sky):
    job=submit(client);controller.tick();sky.finish(dispatch(job)['launch_id'])
    with store.connect() as db: db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(job,))
    controller.tick()
    return job,sky.calls[0][1]


def pool(cluster):
    with store.connect() as db:
        return db.execute('SELECT * FROM worker_pool WHERE cluster=?',(cluster,)).fetchone()


def test_expired_idle_worker_is_retained_until_native_teardown_ack(client,sky):
    job,cluster=idle_worker(client,sky)
    with store.connect() as db: db.execute('UPDATE worker_pool SET released=?',(time.time()-700,))
    controller.tick()
    assert pool(cluster) is not None
    assert pool(cluster)['retired']==1
    assert sky.calls[-1][0]=='queue'
    sky.finish(sky.calls[-1][3]);controller.tick()
    assert sky.calls[-1][0]=='down'
    down=sky.calls[-1][3]
    controller.tick();assert sky.calls[-1][3]==down
    assert pool(cluster) is not None
    sky.finish(down);controller.tick()
    assert pool(cluster) is None
    assert status(client,job)=='SUCCEEDED'


def test_obsolete_image_retires_only_idle_workers(client,sky,monkeypatch):
    first,cluster=idle_worker(client,sky)
    busy=submit(client);controller.tick()
    assert dispatch(busy)['cluster']==cluster
    monkeypatch.setattr(settings,'WORKER_IMAGE','other-tested-image')
    controller.tick()
    assert pool(cluster)['retired']==0
    assert not [c for c in sky.calls if c[0] in ('queue','down')]
    sky.finish(dispatch(busy)['launch_id'])
    with store.connect() as db: db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(busy,))
    controller.tick();controller.tick()
    assert pool(cluster)['retired']==1
    assert sky.calls[-1][0]=='queue'


def expired_worker(client,sky):
    job,cluster=idle_worker(client,sky)
    with store.connect() as db: db.execute('UPDATE worker_pool SET released=?',(time.time()-700,))
    controller.tick()
    return job,cluster,sky.calls[-1][3]


@pytest.mark.parametrize('native_status',['INIT','PENDING','SETTING_UP','RUNNING','UNKNOWN'])
def test_retirement_waits_for_nonterminal_or_unknown_native_work(client,sky,native_status):
    _,cluster,check=expired_worker(client,sky)
    sky.results[check]=[{'status':native_status}];sky.finish(check);controller.tick()
    assert pool(cluster) is not None
    assert not [c for c in sky.calls if c[0]=='down']
    assert pool(cluster)['retire_check_id'] is None
    controller.tick();assert sky.calls[-1][3]==check


def test_retirement_waits_for_pending_native_dispatch_even_with_empty_queue(client,sky):
    _,cluster,check=expired_worker(client,sky)
    sky.finish(check);sky.send('exec',cluster)
    controller.tick()
    assert not [c for c in sky.calls if c[0]=='down']
    assert pool(cluster)['retire_check_id'] is None


def test_retirement_lost_ack_reconciles_once_and_survives_job_deletion(client,sky,monkeypatch):
    job,cluster,check=expired_worker(client,sky);sky.finish(check)
    original=sky.down
    def lost(name):
        original(name);raise TimeoutError('private diagnostic must not escape')
    monkeypatch.setattr(sky,'down',lost)
    controller.tick()
    assert pool(cluster)['retire_intent_at'] is not None
    assert pool(cluster)['retire_down_id'] is None
    assert client.delete('/api/jobs/'+job,headers=auth()).status_code==204
    controller.tick()
    down=pool(cluster)['retire_down_id'];assert down
    assert len([c for c in sky.calls if c[0]=='down'])==1
    sky.finish(down);controller.tick()
    assert pool(cluster) is None


def test_retirement_failed_teardown_rechecks_then_retries(client,sky):
    _,cluster,check=expired_worker(client,sky);sky.finish(check);controller.tick()
    down=pool(cluster)['retire_down_id'];sky.finish(down,ValueError('failed teardown'))
    controller.tick()
    assert pool(cluster) is not None
    assert pool(cluster)['retire_down_id'] is None
    before=len(sky.calls);controller.tick();assert len(sky.calls)==before
    with store.connect() as db: db.execute('UPDATE worker_pool SET retire_retry_after=0')
    controller.tick();check=pool(cluster)['retire_check_id'];sky.finish(check);controller.tick()
    assert len([c for c in sky.calls if c[0]=='down'])==2
    sky.finish(pool(cluster)['retire_down_id']);controller.tick()
    assert pool(cluster) is None


@pytest.mark.parametrize('operation',['queue','down'])
def test_retirement_accepts_confirmed_absent_cluster(client,sky,operation):
    _,cluster,check=expired_worker(client,sky)
    if operation=='down':
        sky.finish(check);controller.tick();check=pool(cluster)['retire_down_id']
    sky.finish(check,GoneError());controller.tick()
    assert pool(cluster) is None


def test_retirement_does_not_block_new_dispatch_or_reuse_retiring_worker(client,sky):
    _,cluster,check=expired_worker(client,sky)
    job=submit(client);controller.tick()
    assert dispatch(job)['cluster']!=cluster
    assert dispatch(job)['method']=='launch'
    assert pool(cluster)['retire_check_id']==check


def test_inconsistent_idle_record_does_not_retire_an_active_job(client,sky):
    job=submit(client);controller.tick();cluster=dispatch(job)['cluster']
    with store.connect() as db:
        db.execute('UPDATE worker_pool SET job_id=NULL,released=?,image=NULL',(time.time()-700,))
    controller.tick()
    assert pool(cluster)['retired']==0
    assert not [c for c in sky.calls if c[0] in ('queue','down')]


def test_legacy_pool_migration_keeps_ownership_and_adds_retirement_state(tmp_path,monkeypatch):
    import sqlite3
    data=tmp_path/'legacy';data.mkdir();monkeypatch.setattr(settings,'DATA',data)
    with sqlite3.connect(data/'jobs.sqlite3') as db:
        db.execute('CREATE TABLE worker_pool (cluster TEXT PRIMARY KEY,job_id TEXT UNIQUE,'
                   'created REAL NOT NULL,released REAL,retired INTEGER NOT NULL DEFAULT 0)')
        db.execute('INSERT INTO worker_pool VALUES (?,?,?,?,?)',('owned-worker',None,1,2,0))
    store.init();row=pool('owned-worker')
    assert row['created']==1 and row['released']==2 and row['retired']==0
    assert row['retire_check_id'] is None and row['retire_down_id'] is None
    assert row['retire_intent_at'] is None and row['retire_retry_after']==0


@pytest.mark.parametrize('operation',['queue','down'])
def test_retirement_handles_synchronously_absent_cluster(client,sky,monkeypatch,operation):
    _,cluster,check=expired_worker(client,sky)
    def gone(*args,**kwargs): raise GoneError()
    monkeypatch.setattr(sky,operation,gone)
    if operation=='queue':
        with store.connect() as db: db.execute('UPDATE worker_pool SET retire_check_id=NULL')
    else: sky.finish(check)
    controller.tick()
    assert pool(cluster) is None


def test_retirement_never_adopts_an_unrelated_teardown_ack(client,sky,monkeypatch):
    _,cluster,check=expired_worker(client,sky);sky.finish(check)
    def lost(*args,**kwargs): raise TimeoutError()
    monkeypatch.setattr(sky,'down',lost)
    controller.tick()
    unrelated=sky.send('down','unrelated-worker');sky.finish(unrelated)
    original=sky.api_status
    monkeypatch.setattr(sky,'api_status',lambda **kw:original(request_ids=kw.get('request_ids')))
    controller.tick()
    assert pool(cluster)['retire_down_id'] is None
    assert pool(cluster)['retire_intent_at'] is not None
