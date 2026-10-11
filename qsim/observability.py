"""Read-only SkyPilot reporting, isolated from the dispatcher and web requests."""
import json
import math
import re
import time
from . import settings

CLUSTER = re.compile(r'^qs-[0-9a-f]{20}$')


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def resource_values(resources, nodes):
    count = number(nodes)
    cpu = number(getattr(resources, 'cpus', None))
    memory = number(getattr(resources, 'memory', None))
    return (cpu * count if cpu is not None and count is not None else None,
            memory * count if memory is not None and count is not None else None)


def state(value):
    return getattr(value, 'value', str(value))


def summarize(clusters, history):
    workers = []
    for item in clusters:
        row = dict(item)
        if not CLUSTER.fullmatch(row['name']):
            continue
        handle = row.get('handle')
        cpu, memory = resource_values(getattr(handle, 'launched_resources', None),
                                      getattr(handle, 'launched_nodes', None))
        workers.append({'name': row['name'], 'status': state(row['status']),
                        'cpus': cpu, 'memory_gib': memory})
    totals = {'cpu_hours': 0., 'memory_gib_hours': 0., 'worker_hours': 0.,
              'history_records': 0, 'unknown_resource_records': 0}
    seen = set()
    for item in history:
        row = dict(item)
        if not CLUSTER.fullmatch(row['name']):
            continue
        key = row.get('cluster_hash') or (row['name'], row.get('launched_at'))
        if key in seen:
            continue
        seen.add(key)
        duration = number(row.get('duration'))
        cpu, memory = resource_values(row.get('resources'), row.get('num_nodes'))
        totals['history_records'] += 1
        if duration is None or cpu is None or memory is None:
            totals['unknown_resource_records'] += 1
            continue
        hours = duration / 3600
        totals['cpu_hours'] += cpu * hours
        totals['memory_gib_hours'] += memory * hours
        totals['worker_hours'] += hours * float(row['num_nodes'])
    up = [w for w in workers if w['status'] == 'UP']
    return {'workers': workers, 'history': totals,
            'current': {'up_workers': len(up),
                        'cpus': sum(w['cpus'] or 0 for w in up),
                        'memory_gib': sum(w['memory_gib'] or 0 for w in up),
                        'unknown_resource_workers': sum(w['cpus'] is None or w['memory_gib'] is None for w in up)},
            'history_days': 30}


def write_snapshot(value):
    path = settings.DATA / 'skypilot-observation.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def collect(sky):
    from sky.utils.common import StatusRefreshMode
    # Native refresh handles autodown; no launch, exec, stop or scheduling calls.
    clusters = sky.get(sky.status(cluster_names=['qs-*'], refresh=StatusRefreshMode.AUTO, all_users=True))
    history = sky.get(sky.cost_report(days=30))
    snapshot = summarize(clusters, history)
    snapshot.update(observed_at=time.time(), jobs=[], queue_errors=[], queues_complete=False)
    write_snapshot(snapshot)
    # Native queue only needs reachable UP workers; preserve separate Qsim states.
    requests = [(w['name'], sky.queue(w['name'], skip_finished=True, all_users=True))
                for w in snapshot['workers'] if w['status'] == 'UP']
    for cluster, request_id in requests:
        try:
            for item in sky.get(request_id):
                row = dict(item)
                snapshot['jobs'].append({'cluster': cluster, 'job_id': row['job_id'],
                                         'status': state(row['status']),
                                         'submitted_at': row.get('submitted_at'),
                                         'start_at': row.get('start_at')})
        except Exception as error:
            # Exceptions can contain job credentials; expose the class only.
            snapshot['queue_errors'].append({'cluster': cluster, 'error': type(error).__name__})
    snapshot['queues_complete'] = True
    snapshot['queues_observed_at'] = time.time()
    write_snapshot(snapshot)


def main():
    import sky
    while True:
        try:
            collect(sky)
        except Exception as error:
            path = settings.DATA / 'skypilot-observation.json'
            try:
                snapshot = json.loads(path.read_text())
            except (OSError, ValueError):
                snapshot = {}
            snapshot.update(last_error=type(error).__name__, failed_at=time.time())
            write_snapshot(snapshot)
        time.sleep(60)


if __name__ == '__main__':
    main()
