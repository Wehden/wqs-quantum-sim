"""Local, read-only release acceptance probes; never change admission or jobs."""
import argparse
import contextlib
import io
import json
import re
import time

from . import release_control, settings, store


def probe(sha, owner, job_ids, native=False, sky=None):
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or not owner:
        raise ValueError('Release and owner required')
    if len(job_ids) > 8 or len(set(job_ids)) != len(job_ids) or any(
            not re.fullmatch(r'[0-9a-f]{32}', job) for job in job_ids):
        raise ValueError('Invalid smoke job selection')
    with store.connect() as db:
        state = release_control.snapshot(db)
        if state['release'] != sha or state['smoke_owner'] != owner:
            raise ValueError('Release smoke admission required')
        jobs = []
        for job in job_ids:
            row = db.execute('SELECT j.id,j.status,j.cleaned,d.cluster,d.launch_id '
                             'FROM jobs j JOIN job_dispatch d ON d.job_id=j.id '
                             'WHERE j.id=? AND j.owner=?', (job, owner)).fetchone()
            if row is None:
                raise ValueError('Smoke job identity mismatch')
            jobs.append(dict(row))
    observation = json.loads((settings.DATA / 'skypilot-observation.json').read_text())
    heartbeat = float((settings.DATA / 'controller-heartbeat').read_text())
    now = time.time()
    fresh = lambda value: isinstance(value, (int, float)) and 0 <= now - value <= 180
    if (not fresh(heartbeat) or not fresh(observation.get('observed_at'))
            or not fresh(observation.get('queues_observed_at'))
            or observation.get('last_error') or observation.get('queue_errors')
            or observation.get('queues_complete') is not True):
        raise ValueError('Controller or observer not healthy')
    result = {'heartbeat': heartbeat, 'observer_at': observation['observed_at'],
              'cleaned': all(j['cleaned'] and j['status'] == 'SUCCEEDED' for j in jobs)}
    if native:
        if not job_ids or not result['cleaned']:
            raise ValueError('Completed smoke jobs required')
        if sky is None:
            import sky
        queues = {}
        for job in jobs:
            if not job['launch_id'] or not job['cluster']:
                raise ValueError('Missing native job identity')
            # Both sky.launch and sky.exec resolve to (native job id, handle).
            native_id, _ = sky.get(job['launch_id'])
            if type(native_id) is not int:
                raise ValueError('Missing native job id')
            cluster = job['cluster']
            if cluster not in queues:
                queues[cluster] = sky.get(sky.queue(cluster, skip_finished=False, all_users=True))
            matches = [r for r in queues[cluster] if r['job_id'] == native_id]
            if len(matches) != 1:
                raise ValueError('Native smoke job not successful')
            status = getattr(matches[0]['status'], 'value', matches[0]['status'])
            if status in ('INIT', 'PENDING', 'SETTING_UP', 'RUNNING'):
                result['native_succeeded'] = False
                return result
            if status != 'SUCCEEDED':
                raise ValueError('Native smoke job not successful')
        result['native_succeeded'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--jobs', default='[]')
    parser.add_argument('--native', action='store_true')
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = probe(args.release, args.owner, json.loads(args.jobs), args.native)
        print(json.dumps(result))
    except Exception:
        raise SystemExit('Release probe failed; admission unchanged.') from None


if __name__ == '__main__':
    main()
