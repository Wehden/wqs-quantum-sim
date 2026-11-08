"""Single durable dispatcher; only this process has access to SkyPilot."""
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import time
from . import settings, store

log = logging.getLogger("qsim.controller")


def cluster_name(job_id):
    return "qs-" + job_id[:20]


def _profile_fingerprint(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _execution_profile(row):
    """Validate persisted context without replacing it with current defaults."""
    raw = row['profile_json'] if 'profile_json' in row.keys() else None
    request = json.loads(row['request']) if 'request' in row.keys() else {}
    physics = request.get('engine') in ('lindblad', 'scqubits', 'josim')
    if not physics and raw is None:
        return None
    try:
        context = json.loads(raw)
        if (not physics or set(context) != {'version', 'worker_profile', 'admission_policy_revision',
                                           'allocation_plan_digest', 'algorithm'} or type(context['version']) is not int or context['version'] != 1):
            raise ValueError()
        revision = settings._certified_physics_profiles['operations'].get(request['engine'], {}).get(request.get('operation'))
        if not revision or context['admission_policy_revision'] != revision:
            raise ValueError()
        if (not isinstance(context['algorithm'], str) or not re.fullmatch(r'[a-zA-Z0-9._:-]{1,128}', context['algorithm'])
                or not re.fullmatch(r'[0-9a-f]{64}', context['allocation_plan_digest'])):
            raise ValueError()
        profile = context['worker_profile']
        expected = copy.deepcopy(settings._certified_physics_profiles['worker'])
        image = profile['image']
        infra = profile['resources']['infra']
        if (not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', image) or image.startswith('docker:')
                or not isinstance(infra, str) or not re.fullmatch(r'[a-zA-Z0-9_./:-]{1,128}', infra)):
            raise ValueError()
        expected['image'] = image
        expected['resources']['infra'] = infra
        # Canonical bytes reject booleans masquerading as integer resource values.
        if json.dumps(profile,sort_keys=True,allow_nan=False) != json.dumps(expected,sort_keys=True):
            raise ValueError()
        return profile
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('execution_policy_mismatch: invalid persisted physics context') from None


def task_config(row):
    token = (settings.DATA / row["id"] / "callback-token").read_text()
    profile = _execution_profile(row)
    config = {
        "name": "wqsim-simulation",
        "resources": {"infra": os.environ.get("QSIM_SKY_INFRA", "k8s"), "cpus": 2, "memory": 4,
                      "image_id": "docker:" + settings.WORKER_IMAGE,
                      "labels": {"workload": "qsim"}},
        "envs": {"QSIM_JOB_URL": settings.CALLBACK_URL + "/internal/jobs/" + row["id"],
                 "QSIM_JOB_TOKEN": token},
        "run": "cd /opt/qsim && /opt/qsim-venv/bin/python -m qsim.worker",
    }
    if profile is not None:
        config['resources'] = {**profile['resources'], 'image_id': 'docker:' + profile['image'],
                               'labels': {'workload': 'qsim'}}
        config['envs']['QSIM_EXECUTION_CONTEXT'] = row['profile_json']
        config['run'] = ('cd /opt/qsim && sudo -n -H -u qsim '
                         '--preserve-env=QSIM_JOB_URL,QSIM_JOB_TOKEN,QSIM_EXECUTION_CONTEXT,'
                         'OPENBLAS_NUM_THREADS,OMP_NUM_THREADS '
                         '/opt/qsim-venv/bin/python -m qsim.worker')
    return config


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
    try:
        profile = _execution_profile(row)
    except ValueError:
        mark_failed(row['id'], 'Physics execution profile is unavailable; contact the service operator')
        # No external intent exists, so there is no remote allocation to tear down.
        with store.connect() as db:
            db.execute('UPDATE jobs SET cleaned=1 WHERE id=?', (row['id'],))
        (settings.DATA / row['id'] / 'callback-token').unlink(missing_ok=True)
        return
    image = profile['image'] if profile is not None else settings.WORKER_IMAGE
    task = sky.Task.from_yaml_config(task_config(row))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute("UPDATE jobs SET status='LAUNCHING', updated=?, started=? WHERE id=? AND status='QUEUED'", (time.time(), time.time(), row['id'])).rowcount
        if not changed:
            return
        worker = None
        for candidate in db.execute('SELECT * FROM worker_pool WHERE job_id IS NULL AND retired=0 '
                                    'AND image=? AND released>? ORDER BY released DESC', (image,time.time()-540)):
            try:
                pooled = json.loads(candidate['profile_json']) if candidate['profile_json'] is not None else None
                compatible = ((profile is None and candidate['profile_json'] is None) or
                              (profile is not None and candidate['profile_json'] is not None and
                               _profile_fingerprint(profile) == _profile_fingerprint(pooled)))
            except (ValueError, TypeError):
                compatible = False
            if compatible:
                worker = candidate
                break
        cluster = worker['cluster'] if worker else cluster_name(row['id'])
        method = 'exec' if worker else 'launch'
        if worker:
            db.execute('UPDATE worker_pool SET job_id=? WHERE cluster=?', (row['id'],cluster))
        else:
            db.execute('INSERT INTO worker_pool(cluster,job_id,created,image,profile_json) VALUES(?,?,?,?,?)',
                       (cluster,row['id'],time.time(),image,
                        json.dumps(profile,sort_keys=True,separators=(',',':')) if profile is not None else None))
        db.execute("UPDATE job_dispatch SET intent='launch',intent_at=?,cluster=?,method=? WHERE job_id=?", (time.time(),cluster,method,row['id']))
    if worker:
        request_id = sky.exec(task, cluster_name=cluster)
    else:
        request_id = sky.launch(task, cluster_name=cluster,
                               idle_minutes_to_autostop=10, down=True)
    save_dispatch(row['id'], launch_id=str(request_id), intent=None)




def retire_worker(sky, row):
    """Keep idle-worker ownership until a checked native teardown completes."""
    cluster = row['cluster']
    if row['retire_retry_after'] > time.time():
        return
    if row['retire_down_id']:
        state = request_state(sky, row['retire_down_id'])
        if state not in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            return
        try:
            sky.get(row['retire_down_id'])
            if state != 'SUCCEEDED':
                raise RuntimeError('Native teardown was not acknowledged')
        except sky.exceptions.ClusterDoesNotExist:
            pass
        except Exception:
            with store.connect() as db:
                db.execute('UPDATE worker_pool SET retire_down_id=NULL,retire_check_id=NULL,'
                           'retire_intent_at=NULL,retire_retry_after=? WHERE cluster=?',
                           (time.time()+30,cluster))
            return
        with store.connect() as db:
            db.execute('DELETE FROM worker_pool WHERE cluster=? AND retired=1 AND job_id IS NULL '
                       'AND retire_down_id=?',(cluster,row['retire_down_id']))
        return
    if row['retire_intent_at'] is not None:
        # Recover a lost submission acknowledgement without allocating another
        # teardown request or forgetting the cluster during controller restart.
        requests = sky.api_status(all_status=True,cluster_name=cluster)
        with store.connect() as db:
            known = {r[0] for r in db.execute('SELECT retire_down_id FROM worker_pool UNION '
                                             'SELECT down_id FROM job_dispatch') if r[0]}
        matches = [r for r in requests if r.cluster_name == cluster and r.name == 'sky.down' and
                   str(r.request_id) not in known and r.created_at >= row['retire_intent_at']-5]
        if matches:
            latest = max(matches,key=lambda r:r.created_at)
            with store.connect() as db:
                db.execute('UPDATE worker_pool SET retire_down_id=? WHERE cluster=?',
                           (str(latest.request_id),cluster))
        return
    if not row['retire_check_id']:
        request = sky.queue(cluster,skip_finished=False,all_users=True)
        with store.connect() as db:
            db.execute('UPDATE worker_pool SET retire_check_id=? WHERE cluster=?',(str(request),cluster))
        return
    state = request_state(sky,row['retire_check_id'])
    if state not in ('SUCCEEDED','FAILED','CANCELLED'):
        return
    try:
        queue = sky.get(row['retire_check_id'])
        terminal = {'SUCCEEDED','FAILED','FAILED_SETUP','FAILED_DRIVER','CANCELLED'}
        if state != 'SUCCEEDED' or not isinstance(queue,list) or any(
                getattr(job['status'],'value',job['status']) not in terminal for job in queue):
            raise RuntimeError('Native worker is not proven idle')
        requests = sky.api_status(all_status=True,cluster_name=cluster)
        if any(request.cluster_name == cluster and request.name in ('sky.launch','sky.exec') and
               getattr(request.status,'value',request.status) not in ('SUCCEEDED','FAILED','CANCELLED')
               for request in requests):
            raise RuntimeError('Native dispatch is still active')
    except sky.exceptions.ClusterDoesNotExist:
        with store.connect() as db:
            db.execute('DELETE FROM worker_pool WHERE cluster=? AND retired=1 AND job_id IS NULL',
                       (cluster,))
        return
    except Exception:
        with store.connect() as db:
            db.execute('UPDATE worker_pool SET retire_check_id=NULL,retire_retry_after=? WHERE cluster=?',
                       (time.time()+30,cluster))
        return
    with store.connect() as db:
        db.execute('UPDATE worker_pool SET retire_intent_at=? WHERE cluster=?',(time.time(),cluster))
    request = sky.down(cluster)
    with store.connect() as db:
        db.execute('UPDATE worker_pool SET retire_down_id=? WHERE cluster=?',(str(request),cluster))


def tick():
    sky = sky_client()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE worker_pool SET retired=1 WHERE job_id IS NULL AND "
                   "(released<? OR image IS NULL OR image!=?) AND NOT EXISTS "
                   "(SELECT 1 FROM job_dispatch d JOIN jobs j ON j.id=d.job_id "
                   "WHERE d.cluster=worker_pool.cluster AND j.cleaned=0)",
                   (time.time()-660,settings.WORKER_IMAGE))
        retiring = db.execute("SELECT * FROM worker_pool WHERE retired=1 AND job_id IS NULL "
                              "AND NOT EXISTS (SELECT 1 FROM job_dispatch d JOIN jobs j "
                              "ON j.id=d.job_id WHERE d.cluster=worker_pool.cluster AND j.cleaned=0)").fetchall()
        for old in db.execute('SELECT id FROM jobs WHERE cleaned=1 AND updated<?', (time.time()-30*86400,)).fetchall():
            shutil.rmtree(settings.DATA / old['id'], ignore_errors=True)
            db.execute('DELETE FROM job_dispatch WHERE job_id=?', (old['id'],))
            db.execute('DELETE FROM jobs WHERE id=?', (old['id'],))
        db.execute("INSERT OR IGNORE INTO job_dispatch(job_id) SELECT id FROM jobs WHERE cleaned=0")
        rows = db.execute("SELECT jobs.*,job_dispatch.* FROM jobs JOIN job_dispatch ON jobs.id=job_dispatch.job_id WHERE cleaned=0 ORDER BY (status='QUEUED'),created").fetchall()
    for worker in retiring:
        try:
            retire_worker(sky,worker)
        except sky.exceptions.ClusterDoesNotExist:
            with store.connect() as db:
                db.execute('DELETE FROM worker_pool WHERE cluster=? AND retired=1 AND job_id IS NULL',
                           (worker['cluster'],))
        except Exception as exc:
            log.warning('Worker retirement pending for %s (%s)',worker['cluster'],type(exc).__name__)
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
