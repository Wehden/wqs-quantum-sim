"""Bounded live acceptance hook. Invoke only inside an approved release smoke phase."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

import numpy as np

from tools.cd import release


def cases():
    result = {'clifft_stim': {'engine': 'clifft', 'input_format': 'stim',
                             'operation': 'sample', 'circuit': 'X 0\nM 0', 'shots': 16, 'seed': 42}}
    for engine, formats, operations in (
            ('clifft', ('qasm2',), ('basis_probabilities',)),
            ('qsim', ('qasm2', 'qasm3'), ('sample', 'basis_probabilities')),
            ('aer', ('qasm2', 'qasm3'), ('sample',))):
        for fmt in formats:
            for operation in operations:
                if fmt == 'qasm2':
                    circuit = 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; '
                    circuit += 'x q[0];' if operation == 'basis_probabilities' else 'creg c[1]; x q[0]; measure q[0] -> c[0];'
                else:
                    circuit = 'OPENQASM 3.0; include "stdgates.inc"; qubit[1] q; '
                    circuit += 'x q[0];' if operation == 'basis_probabilities' else 'bit[1] c; x q[0]; c[0] = measure q[0];'
                # Exercise classical feed-forward and reset on the Aer path.
                if engine == 'aer':
                    circuit += (' reset q[0]; if(c==1) x q[0]; measure q[0] -> c[0];'
                                if fmt == 'qasm2' else ' reset q[0]; if(c == 1) { x q[0]; } c[0] = measure q[0];')
                key = f'{engine}_{fmt}_' + ('probability' if operation == 'basis_probabilities' else 'sample')
                result[key] = {'engine': engine, 'input_format': fmt, 'operation': operation, 'circuit': circuit}
                result[key].update({'bitstrings': ['0', '1']} if operation == 'basis_probabilities'
                                   else {'shots': 16, 'seed': 42})
    return result


def verify_result(body, summary, artifact):
    if not summary.get('engine', '').startswith(body['engine'] + ' ') or summary.get('operation') != body['operation']:
        raise ValueError('Engine or operation mismatch')
    with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
        members = archive.infolist()
        if (not members or len(members) > 8 or sum(m.file_size for m in members) > 1024 * 1024
                or len({m.filename for m in members}) != len(members)):
            raise ValueError('Invalid smoke artifact')
    with np.load(io.BytesIO(artifact), allow_pickle=False) as arrays:
        if body['operation'] == 'basis_probabilities':
            if (summary.get('bitstrings') != ['0', '1'] or arrays['bitstrings'].tolist() != ['0', '1']
                    or arrays['probabilities'].shape != (2,)
                    or np.asarray(summary.get('probabilities')).shape != (2,)
                    or not np.allclose(arrays['probabilities'], [0, 1], atol=1e-6, rtol=0)
                    or not np.allclose(summary['probabilities'], [0, 1], atol=1e-6, rtol=0)):
                raise ValueError('Probability mismatch')
        else:
            key = 'measurements' if body['input_format'] == 'stim' else 'classical_memory'
            values = arrays[key]
            if (summary.get('shots') != 16 or summary.get('counts') != {'1': 16}
                    or values.shape != (16, 1) or values.dtype.kind not in 'bu'
                    or not np.all(values == 1)):
                raise ValueError('Sample mismatch')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Acceptance:
    def __init__(self, config, sha):
        release.validate_config(config)
        if not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise ValueError('Full release SHA required')
        self.config, self.sha = config, sha
        self.options = config['acceptance']
        self.token = release.acceptance_token(config)
        self.deadline = time.monotonic() + 540
        self.http = urllib.request.build_opener(NoRedirect)
        self.jobs = {}
        self.receipt_path = Path(config['state_dir']) / ('acceptance-' + sha + '.json')

    def remaining(self, limit=15):
        seconds = min(limit, self.deadline - time.monotonic())
        if seconds <= 0:
            raise TimeoutError('Acceptance deadline exceeded')
        return seconds

    def kube(self, *args, timeout=30):
        return release.run(['kubectl', '-n', self.config['namespace'], *args], timeout=self.remaining(timeout))

    def request(self, path, body=None, key=None, external=False):
        url = path if external else self.options['api_url'].rstrip('/') + path
        headers = {} if external else {'Authorization': 'Bearer ' + self.token}
        if key:
            headers['Idempotency-Key'] = key
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
        with self.http.open(req, timeout=self.remaining()) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('Oversized acceptance response')
        return data

    def probe(self, native=False):
        role = self.config['roles']['controller' if native else 'api']
        args = ['exec', 'deployment/' + role['deployment'], '-c', role['container'], '--',
                'python', '-m', 'qsim.release_probe', '--release', self.sha,
                '--owner', self.config['smoke_owner'], '--jobs', json.dumps(list(self.jobs.values()))]
        if native:
            args.append('--native')
        return json.loads(self.kube(*args, timeout=120 if native else 30))

    def roles(self, expected):
        for role, mapping in self.config['roles'].items():
            deployment = json.loads(self.kube('get', 'deployment', mapping['deployment'], '-o', 'json'))
            spec, status = deployment['spec'], deployment.get('status', {})
            image = expected[release.ROLES[role]]
            if not re.fullmatch(r'.+@sha256:[a-f0-9]{64}', image):
                raise ValueError('Immutable expected image required')
            containers = {c['name']: c for c in spec['template']['spec']['containers']}
            if role == 'controller':
                environment = {e['name']: e.get('value') for e in containers[mapping['container']].get('env', [])}
                if environment.get('QSIM_WORKER_IMAGE') != expected['worker']:
                    raise ValueError('Controller worker image mismatch')
            if (spec.get('replicas') != 1 or status.get('observedGeneration', 0) < deployment['metadata']['generation']
                    or any(status.get(k) != 1 for k in ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas'))
                    or containers[mapping['container']]['image'] != image):
                raise ValueError('Deployment not at expected image/readiness')
            # Pinning the pod image to the immutable reference also works when
            # the runtime reports its platform manifest rather than an OCI index.
            selector = spec['selector']
            if selector.get('matchExpressions') or not selector.get('matchLabels'):
                raise ValueError('Unsupported deployment selector')
            labels = ','.join(k + '=' + v for k, v in selector['matchLabels'].items())
            pods = json.loads(self.kube('get', 'pods', '-l', labels, '-o', 'json'))['items']
            pods = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
            if len(pods) != 1:
                raise ValueError('Ambiguous deployment pod')
            pod = pods[0]
            actual = next(c for c in pod['spec']['containers'] if c['name'] == mapping['container'])
            running = next(c for c in pod['status'].get('containerStatuses', []) if c['name'] == mapping['container'])
            if actual['image'] != image or not running.get('ready') or not running.get('imageID'):
                raise ValueError('Pod not running expected image')

    def execute(self):
        ledger = json.loads((Path(self.config['state_dir']) / 'deployment.json').read_text())
        if ledger['sha'] != self.sha or ledger['status'] != 'deploying':
            raise ValueError('Matching active deployment ledger required')
        self.roles(ledger['images'])
        if json.loads(self.request('/api/me')).get('id') != self.config['smoke_owner']:
            raise ValueError('Smoke credential owner mismatch')
        if json.loads(self.request('/readyz')).get('status') != 'ready':
            raise ValueError('API readiness failed')
        if json.loads(self.request(self.options['admin_health_url'], external=True)).get('status') != 'ok':
            raise ValueError('Admin health failed')
        before = self.probe()
        nonce = uuid.uuid4().hex
        pending = cases()
        for name, body in pending.items():
            release.write_record(self.receipt_path, {'sha': self.sha, 'status': 'submitting',
                                 'nonce': nonce, 'next_case': name, 'jobs': self.jobs})
            job = json.loads(self.request('/api/jobs', body, f'release:{self.sha}:{nonce}:{name}'))['id']
            if not re.fullmatch(r'[0-9a-f]{32}', job) or job in self.jobs.values():
                raise ValueError('Invalid smoke job identity')
            self.jobs[name] = job
            release.write_record(self.receipt_path, {'sha': self.sha, 'status': 'checking', 'nonce': nonce, 'jobs': self.jobs})
        while pending:
            for name in list(pending):
                job = self.jobs[name]
                state = json.loads(self.request('/api/jobs/' + job))
                if state.get('id') != job:
                    raise ValueError('Job identity mismatch')
                if state['status'] in ('FAILED', 'CANCELLED'):
                    raise ValueError('Smoke job failed')
                if state['status'] == 'SUCCEEDED':
                    verify_result(pending.pop(name), state['summary'], self.request('/api/jobs/' + job + '/result'))
            if pending:
                time.sleep(self.remaining(2))
        while not self.probe()['cleaned']:
            time.sleep(self.remaining(2))
        after = self.probe(native=True)
        while after.get('native_succeeded') is False:
            time.sleep(self.remaining(2))
            after = self.probe(native=True)
        if after['heartbeat'] <= before['heartbeat'] or after.get('native_succeeded') is not True:
            raise ValueError('No controller/native progress')
        self.roles(ledger['images'])
        result = {'sha': self.sha, 'roles_healthy': sorted(release.ROLES),
                  'controller_progress': True, 'cases': {name: True for name in cases()}}
        release.verify_acceptance(result, self.sha)
        release.write_record(self.receipt_path, {**result, 'jobs': self.jobs, 'status': 'verified'})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        hook = Acceptance(json.loads(Path(args.config).read_text()), os.environ['WQS_RELEASE_SHA'])
        print(json.dumps(hook.execute()))
    except Exception:
        # Neither API errors, circuit payloads, token paths nor kubectl stderr
        # belong in CI output. Keep existing jobs and admission for the operator.
        raise SystemExit('Release acceptance failed; inspect private evidence. Admission unchanged.') from None


if __name__ == '__main__':
    main()
