"""Bounded live acceptance hook. Invoke only inside an approved release smoke phase."""
import argparse
import io
import hashlib
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


def cases(exported=None):
    from qsim.release_probe import release_cases
    return release_cases(exported)


def verify_physics_result(body, summary, artifact, *, name, related):
    """Independent small-fixture math; no native simulator imports on the runner."""
    if len(artifact)>1024**2:raise ValueError('Oversized physics smoke artifact')
    with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
        members=archive.infolist()
        if (not 1<=len(members)<=8 or len({m.filename for m in members})!=len(members)
                or sum(m.file_size for m in members)>1024**2):raise ValueError('Invalid physics smoke archive')
        for item in members:
            if (not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.npy',item.filename)
                    or item.compress_type!=zipfile.ZIP_STORED or item.flag_bits&1):
                raise ValueError('Invalid physics smoke member')
            with archive.open(item) as stream:
                version=np.lib.format.read_magic(stream)
                if version not in ((1,0),(2,0)):raise ValueError('Unsupported smoke NPY version')
                reader=(np.lib.format.read_array_header_1_0 if version==(1,0)
                        else np.lib.format.read_array_header_2_0)
                shape,_,dtype=reader(stream,max_header_size=4096)
                count=1
                for dimension in shape:
                    if type(dimension) is not int or dimension<0 or dimension>65536:raise ValueError('Invalid smoke shape')
                    count*=dimension
                if (len(shape)>3 or count*dtype.itemsize>1024**2 or dtype.hasobject
                        or dtype.fields is not None or not dtype.isnative
                        or (item.filename=='metadata_json.npy' and (shape!=() or dtype.kind!='U' or dtype.itemsize>4*65536))
                        or (item.filename!='metadata_json.npy' and dtype not in (np.dtype('float64'),np.dtype('complex128')))):
                    raise ValueError('Invalid physics smoke dtype/shape')
    with np.load(io.BytesIO(artifact),allow_pickle=False) as archive:
        arrays={key:archive[key] for key in archive.files}
    metadata=json.loads(str(arrays.pop('metadata_json')))
    engine=body['engine'];operation=body['operation'];solver=body.get('solver')
    precision={'lindblad':'complex128','scqubits':'float64/complex128','josim':'float64'}[engine]
    version={'lindblad':'5.3.1','scqubits':'4.3.1','josim':'2.7.02a34ee'}[engine]
    if (any(metadata.get(k)!=v for k,v in {'engine':engine,'operation':operation,'schema_version':1,
            'backend':'cpu','precision':precision,'engine_version':version}.items())
            or any(summary.get(k)!=metadata.get(k) for k in
                   ('engine','operation','schema_version','backend','precision','engine_version','solver'))
            or (solver and metadata.get('solver')!=solver)
            or set(summary.get('arrays',{}))!=set(arrays)):
        raise ValueError('Physics smoke metadata mismatch')
    for key,value in arrays.items():
        if (not np.isfinite(value).all() or summary['arrays'][key]['shape']!=list(value.shape)
                or summary['arrays'][key]['dtype']!=value.dtype.name):raise ValueError('Physics smoke array mismatch')
    if engine=='lindblad':
        if operation in ('evolve','propagator'):
            expected_times=np.asarray(body['times']);np.testing.assert_array_equal(arrays['times'],expected_times)
            if summary['arrays']['times']['units']!='ns':raise ValueError('Lindblad time units mismatch')
        if operation=='evolve':
            expected_keys={'times','expectations','state_times','states'}
            np.testing.assert_array_equal(arrays['state_times'],expected_times)
            if summary['arrays']['state_times']['units']!='ns':raise ValueError('State time units mismatch')
            np.testing.assert_equal(arrays['expectations'].shape,(3,1))
            if solver=='se':
                phase=np.exp(-2j*np.pi*.017*expected_times)
                np.testing.assert_allclose(arrays['states'],np.column_stack((np.zeros(3),phase)),atol=1e-7,rtol=0)
                np.testing.assert_allclose(arrays['expectations'][:,0],1.,atol=1e-7,rtol=0)
            else:
                rho=arrays['states'];np.testing.assert_equal(rho.shape,(3,2,2))
                np.testing.assert_allclose(rho,rho.conj().transpose(0,2,1),atol=1e-9,rtol=0)
                np.testing.assert_allclose(np.trace(rho,axis1=1,axis2=2),1.,atol=1e-7,rtol=0)
                if np.linalg.eigvalsh(rho).min() < -1e-8:raise ValueError('Nonphysical smoke density')
                np.testing.assert_allclose(arrays['expectations'][:,0],rho[:,1,1],atol=1e-7,rtol=0)
                if solver=='me':np.testing.assert_allclose(rho[:,1,1],np.exp(-expected_times/3),atol=1e-7,rtol=0)
                else:
                    if metadata.get('seed')!='42' or metadata['work'].get('trajectories_completed')!='8':
                        raise ValueError('Seeded smoke identity mismatch')
                    np.testing.assert_allclose(rho[0],np.diag([0,1]),atol=1e-10)
                    # Pinned PCG64 jump thresholds; |0> is absorbing and the
                    # no-jump norm squared is exp(-t/T1) for this fixture.
                    seeds=np.random.SeedSequence(42)
                    thresholds=np.asarray([np.random.Generator(np.random.PCG64(seeds.spawn(1)[0])).random()
                                           for _ in range(8)])
                    population=(thresholds[:,None]<np.exp(-expected_times[None,:]/3)).mean(axis=0)
                    expected_rho=np.zeros((3,2,2),dtype=complex)
                    expected_rho[:,0,0]=1-population;expected_rho[:,1,1]=population
                    np.testing.assert_allclose(rho,expected_rho,atol=1e-7,rtol=0)
                    if name=='lindblad_mc_repeat':
                        previous=related['lindblad_evolve_mc']['arrays']
                        for key in arrays:np.testing.assert_array_equal(arrays[key],previous[key])
        elif operation=='propagator':
            expected_keys={'times','propagators'}
            times=arrays['times'];values=arrays['propagators']
            if solver=='se':
                expected=np.asarray([np.diag([1,np.exp(-2j*np.pi*.017*t)]) for t in times])
                representation='hilbert-unitary'
            else:
                expected=[]
                for t in times:
                    decay=np.exp(-t/3);coherence=np.exp(-t/6)*np.exp(2j*np.pi*.017*t)
                    expected.append([[1,0,0,1-decay],[0,coherence.conjugate(),0,0],
                                     [0,0,coherence,0],[0,0,0,decay]])
                representation='liouville-superoperator'
                if metadata.get('vectorization')!='column-stacking':raise ValueError('Vectorization mismatch')
            if metadata.get('representation')!=representation or metadata.get('reference_time')!=0.:
                raise ValueError('Propagator representation mismatch')
            np.testing.assert_allclose(values,expected,atol=1e-7,rtol=0)
        elif operation=='spectrum':
            expected_keys={'eigenvalues','eigenvectors'} if body.get('return_vectors') else {'eigenvalues'}
            expected=related['scqubits_export_for_lindblad']['arrays']['eigenvalues'] if name=='scqubits_export_lindblad' else [0.,.017]
            np.testing.assert_allclose(arrays['eigenvalues'],expected,atol=1e-9,rtol=0)
            if summary['arrays']['eigenvalues']['units']!='GHz':raise ValueError('Spectrum units mismatch')
            if 'eigenvectors' in arrays:
                vectors=arrays['eigenvectors'];np.testing.assert_allclose(vectors.conj().T@vectors,np.eye(2),atol=1e-9)
                np.testing.assert_allclose(np.diag([0.,.017])@vectors,vectors*np.asarray(expected),atol=1e-9)
        else:
            expected_keys={'expectations','steady_state'}
            np.testing.assert_allclose(arrays['steady_state'],[[1,0],[0,0]],atol=1e-9,rtol=0)
            np.testing.assert_allclose(arrays['expectations'],[0],atol=1e-9,rtol=0)
    elif engine=='scqubits':
        charge=np.arange(-30,31)
        h=np.diag(4*.3*charge**2)+np.diag(np.full(60,-15/2),1)+np.diag(np.full(60,-15/2),-1)
        energy,vectors=np.linalg.eigh(h);energy=energy[:4];vectors=vectors[:,:4]
        exported=operation=='export_for_lindblad'
        np.testing.assert_allclose(arrays['eigenvalues'],energy-energy[0] if exported else energy,atol=1e-9,rtol=0)
        if summary['arrays']['eigenvalues']['units']!='GHz':raise ValueError('Scqubits energy units mismatch')
        if operation=='spectrum':
            expected_keys={'eigenvalues','eigenvectors'}
            actual=arrays['eigenvectors'];np.testing.assert_equal(actual.shape,(61,4))
            np.testing.assert_allclose(actual.conj().T@actual,np.eye(4),atol=1e-9)
            np.testing.assert_allclose(h@actual,actual*energy,atol=1e-9)
        else:
            key='n_operator' if exported else 'matrix_elements'
            expected_keys={'eigenvalues','hamiltonian',key} if exported else {'eigenvalues',key}
            actual=arrays[key];np.testing.assert_equal(actual.shape,(4,4))
            np.testing.assert_allclose(actual,actual.conj().T,atol=1e-10)
            np.testing.assert_allclose(np.abs(actual),np.abs(vectors.conj().T@(charge[:,None]*vectors)),atol=1e-9)
            if (summary['arrays'][key]['units'] is not None
                    or metadata['units'][key]!='dimensionless'):
                raise ValueError('Operator units mismatch')
            if exported:
                np.testing.assert_allclose(arrays['hamiltonian'],np.diag(energy-energy[0]),atol=1e-9)
                if metadata.get('energy_reference')!='ground' or metadata['export_basis']['representation']!='selected energy eigenbasis':
                    raise ValueError('Export basis mismatch')
                np.testing.assert_allclose(metadata['removed_ground_energy'],energy[0],atol=1e-9)
                handoff=metadata['lindblad_request'];mode=handoff['model']['modes'][0]
                decode=lambda matrix:np.asarray([[complex(v['re'],v['im']) for v in row] for row in matrix])
                np.testing.assert_array_equal(decode(mode['hamiltonian']),arrays['hamiltonian'])
                np.testing.assert_array_equal(decode(handoff['model']['matrices']['n_operator']),actual)
    else:
        expected_keys={'times','traces'}
        times=arrays['times'];values=arrays['traces']
        np.testing.assert_equal(times.shape,(999,));np.testing.assert_equal(values.shape,(999,5))
        if times[0]<0 or not np.all(np.diff(times)>0):raise ValueError('JoSIM native time order mismatch')
        # Original/candidate exact frozen SFQ oracle, including native row selection.
        packed=np.column_stack((times,values)).astype('>f8').tobytes()
        if hashlib.sha256(packed).hexdigest()!='698addcdc6ff152b94d4d908fd4023738560622fc31ab306a2b0e407fe5beb04':
            raise ValueError('JoSIM frozen native output mismatch')
        if (metadata.get('trace_names')!=['V(B01)','P(B01)','V(B02)','P(B02)','I(ROUT)']
                or metadata.get('trace_units')!=['V','rad','V','rad','A'] or metadata.get('time_units')!='s'
                or summary['arrays']['times']['units']!='s'):
            raise ValueError('JoSIM units/trace identity mismatch')
        window=(times>=50e-12)&(times<=200e-12)
        for column in (0,2):
            if abs(np.trapezoid(values[window,column],times[window])/2.0678338484619295e-15-1)>=.02:
                raise ValueError('SFQ integral mismatch')
    if set(arrays)!=expected_keys:raise ValueError('Unexpected physics smoke arrays')
    return {'arrays':arrays,'metadata':metadata}

def verify_result(body, summary, artifact, *, name=None, related=None):
    if body['engine'] in ('lindblad','scqubits','josim'):
        return verify_physics_result(body,summary,artifact,name=name,related=related or {})
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
        self.worker_image = None
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
                '--owner', self.config['smoke_owner'], '--jobs', json.dumps(self.jobs),
                '--worker-image', self.worker_image]
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
            if role == 'api':
                environment = {e['name']:e.get('value') for e in containers[mapping['container']].get('env',[])}
                if environment.get('QSIM_PHYSICS_ENABLED')!='1':raise ValueError('Physics matrix not staged')
            if role in ('api', 'controller'):
                environment = {e['name']: e.get('value') for e in containers[mapping['container']].get('env', [])}
                if environment.get('QSIM_WORKER_IMAGE') != expected['worker']:
                    raise ValueError(('API' if role == 'api' else 'Controller')+' worker image mismatch')
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
        try:
            return self._execute()
        except BaseException:
            release.write_record(self.receipt_path, {'schema':2,'sha':self.sha,'status':'failed','job_ids':self.jobs})
            raise

    def _execute(self):
        ledger = json.loads((Path(self.config['state_dir']) / 'deployment.json').read_text())
        if ledger['sha'] != self.sha or ledger['status'] != 'deploying':
            raise ValueError('Matching active deployment ledger required')
        self.worker_image=ledger['images']['worker']
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
        related = {};digests = {}
        while pending:
            for name in list(pending):
                body=pending[name]
                if body is None:
                    if 'scqubits_export_for_lindblad' not in related:continue
                    body=related['scqubits_export_for_lindblad']['metadata']['lindblad_request']
                    pending[name]=body
                if name not in self.jobs:
                    release.write_record(self.receipt_path, {'schema':2,'sha':self.sha,'status':'submitting',
                        'nonce':nonce,'next_case':name,'jobs':self.jobs})
                    job=json.loads(self.request('/api/jobs',body,f'release:{self.sha}:{nonce}:{name}'))['id']
                    if not re.fullmatch(r'[0-9a-f]{32}',job) or job in self.jobs.values():
                        raise ValueError('Invalid smoke job identity')
                    self.jobs[name]=job
                    release.write_record(self.receipt_path,{'schema':2,'sha':self.sha,'status':'checking','nonce':nonce,'jobs':self.jobs})
                job=self.jobs[name]
                state=json.loads(self.request('/api/jobs/'+job))
                if state.get('id')!=job:raise ValueError('Job identity mismatch')
                if state['status'] in ('FAILED','CANCELLED'):raise ValueError('Smoke job failed')
                if state['status']=='SUCCEEDED':
                    if name=='lindblad_mc_repeat' and 'lindblad_evolve_mc' not in related:continue
                    artifact=self.request('/api/jobs/'+job+'/result')
                    related[name]=verify_result(body,state['summary'],artifact,name=name,related=related) or {}
                    digests[name]=hashlib.sha256(artifact).hexdigest()
                    pending.pop(name)
            if pending:time.sleep(self.remaining(2))
        while not self.probe()['cleaned']:
            time.sleep(self.remaining(2))
        after = self.probe(native=True)
        while after.get('native_succeeded') is False:
            time.sleep(self.remaining(2))
            after = self.probe(native=True)
        if after['heartbeat'] <= before['heartbeat'] or after.get('native_succeeded') is not True:
            raise ValueError('No controller/native progress')
        self.roles(ledger['images'])
        records=after.get('jobs',{})
        if set(records)!=set(cases()):raise ValueError('Missing named native evidence')
        for name,record in records.items():
            if record.get('owner')!=self.config['smoke_owner'] or record.get('result_sha256')!=digests[name]:
                raise ValueError('Native/HTTP result owner or digest mismatch')
            record['mathematics_verified']=True
        result={'schema':2,'matrix':'cpu-physics-v1','gpu_cells':[], 'sha':self.sha,
                'worker_image':self.worker_image,'roles_healthy':sorted(release.ROLES),
                'controller_progress':True,'cases':{name:True for name in cases()},'jobs':records}
        release.verify_acceptance(result,self.sha,worker_image=self.worker_image,smoke_owner=self.config['smoke_owner'])
        release.write_record(self.receipt_path,{**result,'job_ids':self.jobs,'status':'verified'})
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
