"""Single durable dispatcher; only this process has access to SkyPilot."""
import logging
import os
import shutil
import time
from . import settings, store

log = logging.getLogger("qsim.controller")


def cluster_name(job_id):
    return "qs-" + job_id[:20]


def task_config(row):
    token = (settings.DATA / row["id"] / "callback-token").read_text()
    return {
        "name": "wqsim-simulation",
        "resources": {"infra": os.environ.get("QSIM_SKY_INFRA", "k8s"), "cpus": 2, "memory": 4,
                      "image_id": "docker:" + settings.WORKER_IMAGE,
                      "labels": {"workload": "qsim"}},
        "envs": {"QSIM_JOB_URL": settings.CALLBACK_URL + "/internal/jobs/" + row["id"],
                 "QSIM_JOB_TOKEN": token},
        "run": "/opt/qsim-venv/bin/python /opt/qsim/worker.py",
    }


def sky_client():
    import sky
    return sky


def mark_failed(job_id, message):
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='FAILED', error=?, updated=? WHERE id=? AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED')", (message, time.time(), job_id))


def request_state(sky, request_id):
    rows = sky.api_status(request_ids=[request_id], fields=['request_id', 'status'])
    if not rows:
        raise RuntimeError('SkyPilot request is not available')
    return getattr(rows[0].status, 'value', rows[0].status)


def save_dispatch(job_id, **values):
    with store.connect() as db:
        db.execute('UPDATE job_dispatch SET ' + ','.join(k+'=?' for k in values) + ' WHERE job_id=?', (*values.values(), job_id))


def recover_intent(sky, row, kind):
    # The intent is committed before the remote call. An ambiguous submission is
    # reconciled by cluster name, never blindly repeated after a lost response.
    requests = sky.api_status(all_status=True, cluster_name=(row['cluster'] or cluster_name(row['id'])))
    with store.connect() as db:
        known = {value for r in db.execute('SELECT launch_id,down_id FROM job_dispatch') for value in r if value}
    matches = [r for r in requests if str(r.request_id) not in known if r.cluster_name == (row['cluster'] or cluster_name(row['id'])) if r.name == 'sky.' + (row['method'] if kind == 'launch' else kind) and r.created_at >= row['intent_at'] - 5]
    if matches:
        latest = max(matches, key=lambda r: r.created_at)
        save_dispatch(row['id'], **{kind + '_id': str(latest.request_id), 'intent': None})
    elif time.time() - row['intent_at'] > settings.JOB_DEADLINE:
        mark_failed(row['id'], 'Dispatch acknowledgement unavailable; contact the service operator')
        # Retain intent: cleanup must not race a potentially accepted launch.


def cleanup_job(sky, row):
    if row['launch_id'] and request_state(sky, row['launch_id']) not in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
        if row['status'] == 'SUCCEEDED':
            return
        # Cancellation is asynchronous too. Wait until it is settled before down.
        if not row['cancel_id']:
            save_dispatch(row['id'], cancel_id=str(sky.api_cancel(row['launch_id'], silent=True)))
        return
    if row['cluster'] and row['status'] in ('SUCCEEDED', 'FAILED') and not row['retry_pending']:
        # Reuse only an acknowledged, finished submission. A fresh worker process
        # and temporary directory are used for every simulation.
        healthy = row['launch_id'] and request_state(sky, row['launch_id']) == 'SUCCEEDED'
        if healthy:
            with store.connect() as db:
                db.execute('UPDATE worker_pool SET job_id=NULL,released=? WHERE cluster=?', (time.time(),row['cluster']))
                db.execute('UPDATE jobs SET cleaned=1 WHERE id=?', (row['id'],))
            (settings.DATA / row['id'] / 'callback-token').unlink(missing_ok=True)
            return
    if not row['down_id']:
        save_dispatch(row['id'], intent='down', intent_at=time.time())
        request_id = sky.down((row['cluster'] or cluster_name(row['id'])))
        save_dispatch(row['id'], down_id=str(request_id), intent=None)
        return
    state = request_state(sky, row['down_id'])
    if state not in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
        return
    try:
        sky.get(row['down_id'])
    except sky.exceptions.ClusterDoesNotExist:
        pass  # A failed placement may never have created a cluster.
    except Exception:
        save_dispatch(row['id'], down_id=None, retry_after=time.time()+30)
        return
    with store.connect() as db:
        db.execute('DELETE FROM worker_pool WHERE cluster=?', (row['cluster'],))
        current = db.execute('SELECT status FROM jobs WHERE id=?', (row['id'],)).fetchone()
        if row['retry_pending'] and current['status'] not in settings.TERMINAL:
            db.execute("UPDATE jobs SET status='QUEUED', started=NULL, error=NULL, updated=? WHERE id=?", (time.time(), row['id']))
            db.execute("UPDATE job_dispatch SET launch_id=NULL,down_id=NULL,cancel_id=NULL,cluster=NULL,method='launch',retry_pending=0,retry_after=? WHERE job_id=?", (time.time()+30, row['id']))
        else:
            db.execute('UPDATE jobs SET cleaned=1 WHERE id=?', (row['id'],))
            for name in ('task.yaml', 'callback-token'):
                (settings.DATA / row['id'] / name).unlink(missing_ok=True)


def advance(sky, row):
    if row['intent']:
        recover_intent(sky, row, row['intent'])
        return
    if row['retry_after'] > time.time():
        return
    if row['status'] in settings.TERMINAL or row['retry_pending']:
        cleanup_job(sky, row)
        return
    if row['launch_id']:
        state = request_state(sky, row['launch_id'])
        if state not in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            return
        try:
            sky.get(row['launch_id'])
        except (sky.exceptions.ResourcesUnavailableError, sky.exceptions.ClusterNotUpError, sky.exceptions.ClusterDoesNotExist):
            # Tear down any partial placement before retrying the same job.
            # Preserve its identity, callback token and idempotency record.
            save_dispatch(row['id'], retry_pending=1)
            with store.connect() as db:
                db.execute("UPDATE jobs SET status='QUEUED', started=NULL, updated=? WHERE id=? AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED')", (time.time(), row['id']))
        except Exception:
            mark_failed(row['id'], 'Compute dispatch failed; contact the service operator')
        return
    if row['status'] != 'QUEUED':
        return  # A pre-upgrade launch remains owned by its existing worker.
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute("UPDATE jobs SET status='LAUNCHING', updated=?, started=? WHERE id=? AND status='QUEUED'", (time.time(), time.time(), row['id'])).rowcount
        if not changed:
            return
        worker = db.execute('SELECT cluster FROM worker_pool WHERE job_id IS NULL AND retired=0 AND image=? AND created>? AND released>? ORDER BY released DESC LIMIT 1', (settings.WORKER_IMAGE,time.time()-840,time.time()-540)).fetchone()
        cluster = worker['cluster'] if worker else cluster_name(row['id'])
        method = 'exec' if worker else 'launch'
        if worker:
            db.execute('UPDATE worker_pool SET job_id=? WHERE cluster=?', (row['id'],cluster))
        else:
            db.execute('INSERT INTO worker_pool(cluster,job_id,created,image) VALUES(?,?,?,?)', (cluster,row['id'],time.time(),settings.WORKER_IMAGE))
        db.execute("UPDATE job_dispatch SET intent='launch',intent_at=?,cluster=?,method=? WHERE job_id=?", (time.time(),cluster,method,row['id']))
    task = sky.Task.from_yaml_config(task_config(row))
    if worker:
        request_id = sky.exec(task, cluster_name=cluster)
    else:
        request_id = sky.launch(task, cluster_name=cluster,
                               idle_minutes_to_autostop=10, down=True)
    save_dispatch(row['id'], launch_id=str(request_id), intent=None)



def tick():
    sky = sky_client()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM worker_pool WHERE job_id IS NULL AND (created<? OR released<?)', (time.time()-1200,time.time()-660))
        for old in db.execute('SELECT id FROM jobs WHERE cleaned=1 AND updated<?', (time.time()-30*86400,)).fetchall():
            shutil.rmtree(settings.DATA / old['id'], ignore_errors=True)
            db.execute('DELETE FROM job_dispatch WHERE job_id=?', (old['id'],))
            db.execute('DELETE FROM jobs WHERE id=?', (old['id'],))
        db.execute("INSERT OR IGNORE INTO job_dispatch(job_id) SELECT id FROM jobs WHERE cleaned=0")
        db.execute("UPDATE jobs SET status='FAILED',error='Job exceeded the 20 minute dispatch deadline',updated=? WHERE status IN ('LAUNCHING','RUNNING') AND started<? AND id NOT IN (SELECT job_id FROM job_dispatch WHERE retry_pending=1)", (time.time(), time.time()-settings.JOB_DEADLINE))
        rows = db.execute("SELECT jobs.*,job_dispatch.* FROM jobs JOIN job_dispatch ON jobs.id=job_dispatch.job_id WHERE cleaned=0 ORDER BY (status='QUEUED'),created").fetchall()
    for row in rows:
        try:
            advance(sky, row)
        except Exception as exc:
            # Remote exception messages can contain credentials. Keep logs minimal.
            log.warning('Dispatch reconciliation pending for %s (%s)', row['id'], type(exc).__name__)


def main():
    import fcntl
    import signal
    import sys
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    logging.basicConfig(level=logging.INFO)
    store.init()
    # Prevent competing dispatchers even when a process is accidentally started twice.
    with (settings.DATA / "controller.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                tick()
                (settings.DATA / "controller-heartbeat").write_text(str(time.time()))
            except Exception:
                log.exception("Dispatcher iteration failed")
            time.sleep(3)


if __name__ == "__main__":
    main()
