"""Disposable fixed-case Runpod transport; not a WQS API or production worker."""
import json
import os
import subprocess

CASES = ('transmon61', 'transmon511', 'rabi2', 'spectrum256')


def handler(job):
    request = job.get('input') if type(job) is dict else None
    if type(request) is not dict or set(request) - {'backend', 'case', 'suite'}:
        return {'error': 'input must contain only backend and case or suite'}
    if request.get('backend') not in ('cpu', 'gpu'):
        return {'error': 'backend must be cpu or gpu'}
    if 'suite' in request:
        if request['suite'] is not True or 'case' in request:
            return {'error': 'suite must be true and excludes case'}
        selector = ['--suite']
    elif request.get('case') in CASES:
        selector = ['--case', request['case']]
    else:
        return {'error': 'case must be one fixed discovery case'}
    env = {key: os.environ[key] for key in
           ('PATH', 'CUDA_VISIBLE_DEVICES', 'NVIDIA_VISIBLE_DEVICES',
            'NVIDIA_DRIVER_CAPABILITIES') if key in os.environ}
    env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
               CUPY_CACHE_DIR='/tmp/cupy-cache')
    completed = subprocess.run(
        ['/opt/discovery/bin/python', '/opt/probe_gpu.py',
         '--backend', request['backend'], *selector],
        env=env, capture_output=True, text=True, check=False)
    receipts = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    return {'returncode': completed.returncode, 'receipts': receipts,
            'stderr': completed.stderr[-6000:]}


if __name__ == '__main__':
    import runpod
    runpod.serverless.start({'handler': handler})
