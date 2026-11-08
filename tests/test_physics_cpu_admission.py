"""Mandatory built-image CPU admission proofs; ordinary host runs do not qualify them."""
import json
import os
import subprocess
import sys
import unittest

_CASES = ('spectrum', 'steadystate', 'evolve_se', 'evolve_me', 'evolve_mc',
          'propagator_se', 'propagator_me', 'max_times', 'coherent', 'max_me_drives',
          'dense_me_generators', 'dense_me_owners', 'evolve_se_max_output',
          'evolve_me_max_output', 'evolve_mc_max_output')

_CHILD = r'''
import os,sys,resource,json,hashlib,time,base64
resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE,(12*1024**2,12*1024**2))
sys.path.insert(0,'/opt/qsim')
from pathlib import Path
assert os.geteuid()==1000
assert Path('/sys/fs/cgroup/memory.max').read_text().strip()==str(4*1024**3)
a,b=Path('/sys/fs/cgroup/cpu.max').read_text().split();assert int(a)==2*int(b)
from importlib.metadata import distribution
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import serialize_physics_result
mode=sys.argv[1]
operation,solver,dimension,points={'spectrum':('spectrum',None,256,0),
 'steadystate':('steadystate',None,32,0),'evolve_se':('evolve','se',256,2),
 'evolve_me':('evolve','me',32,2),'evolve_mc':('evolve','mc',256,2),
 'propagator_se':('propagator','se',256,2),'propagator_me':('propagator','me',20,2),
 'max_times':('evolve','se',256,65536),'coherent':('evolve','se',256,2),
 'max_me_drives':('evolve','me',32,2),'dense_me_generators':('evolve','me',32,2),'dense_me_owners':('evolve','me',32,65536),'evolve_se_max_output':('evolve','se',256,1530),'evolve_me_max_output':('evolve','me',32,383),'evolve_mc_max_output':('evolve','mc',256,5)}[mode]
body={'schema_version':1,'operation':operation,'model':{'modes':[{'name':'q','kind':'oscillator','dimension':dimension,'frequency':.017}]}}
if solver:
 body['solver']=solver
 body['times']=[i*1e-5/(points-1) for i in range(points)]
if operation=='evolve':
 body['initial_state']={'kind':'product','modes':{'q':{'kind':'fock','index':1}}}
 body['store_states']='all' if mode!='max_times' else 'none'
 if mode!='max_times':body['observables']=[{'name':'n'+str(i),'operator':{'kind':'product','factors':{'q':'n'}}} for i in range(32)]
if mode in ('evolve_mc','evolve_mc_max_output'):body['trajectories']=1000
if solver in ('me','mc') or operation=='steadystate':
 body['model']['collapse']=[{'kind':'relaxation','mode':'q','T1':3.} for _ in range(16)]
if operation=='spectrum':body.update(evals_count=256,return_vectors=True)
if mode=='coherent':body['initial_state']['modes']['q']={'kind':'coherent','amplitude':.3}
if mode=='max_me_drives':
 body['model']['drives']=[{'operator':{'kind':'product','factors':{'q':'n'}},'amplitude':1e-5,'envelope':{'shape':'constant'},'hc':False} for _ in range(16)]
if mode in ('dense_me_generators','dense_me_owners'):
 import numpy as np
 rng=np.random.default_rng(441)
 matrices={}
 drives=[]
 for i in range(16):
  raw=rng.normal(size=(32,32))*.01
  matrices['m'+str(i)]=((raw+raw.T)*.5).tolist()
  drives.append({'operator':{'kind':'matrix','name':'m'+str(i)},'amplitude':1e-5,
    'envelope':{'shape':'sampled','times':[0.,1e-5],'values':[.1,1.+i*.01],'interpolation':'linear'},'hc':False})
 body['model'].update(matrices=matrices,drives=drives)
if mode=='dense_me_owners':
 body['store_states']='none'
 body['observables']=[]
if mode.endswith('_max_output'):
 body['observables']=[]
request=LindbladRequest(**body)
plan=request.admission_plan()
for item in plan.coefficient_plan['runtime_files']:
 assert hashlib.sha256(Path(distribution(item['distribution']).locate_file(item['path'])).read_bytes()).hexdigest()==item['sha256'],item
from qsim.lindblad_adapter import run_lindblad
started=time.monotonic()
result=run_lindblad(request,plan,execution_context={'backend':'cpu'})
import numpy as np
import math
arrays=result.arrays
if operation=='spectrum':
 np.testing.assert_allclose(arrays['eigenvalues'],.017*np.arange(256),atol=1e-10)
 np.testing.assert_allclose(np.abs(arrays['eigenvectors']),np.eye(256),atol=1e-10)
elif operation=='steadystate':
 np.testing.assert_allclose(arrays['steady_state'][0,0],1.,atol=1e-8)
 np.testing.assert_allclose(np.trace(arrays['steady_state']),1.,atol=1e-8)
elif operation=='evolve':
 np.testing.assert_array_equal(arrays['times'],np.asarray(body['times']))
 if solver=='mc':
  assert result.work['trajectories_completed']=='1000' and result.work['collapse_events']=='0'
  np.testing.assert_allclose(arrays['states'][:,1,1],1.,atol=1e-8)
 elif mode=='coherent':
  np.testing.assert_allclose(arrays['expectations'],.09,atol=1e-8)
 elif solver=='se' and 'states' in arrays:
  np.testing.assert_allclose(arrays['states'][:,1],np.exp(-2j*math.pi*.017*arrays['times']),atol=1e-8)
  np.testing.assert_allclose(arrays['states'][:,0],0.,atol=1e-8)
 elif solver=='me' and 'states' in arrays:
  expected=np.exp(-16*arrays['times']/3)
  np.testing.assert_allclose(arrays['states'][:,1,1],expected,atol=1e-7)
  np.testing.assert_allclose(arrays['states'][:,0,0],1-expected,atol=1e-7)
elif solver=='se':
 for index,t in enumerate(arrays['times']):
  np.testing.assert_allclose(arrays['propagators'][index],np.diag(np.exp(-2j*math.pi*.017*t*np.arange(dimension))),atol=1e-8)
else:
 for index,t in enumerate(arrays['times']):
  population=arrays['propagators'][index,:,dimension+1].reshape((dimension,dimension),order='F')
  np.testing.assert_allclose(population[1,1],np.exp(-16*t/3),atol=1e-8)
  np.testing.assert_allclose(population[0,0],1-np.exp(-16*t/3),atol=1e-8)
summary,artifact=serialize_physics_result(result,plan.result)
print(json.dumps({'case':mode,'peak_plan_bytes':plan.peak_bytes,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
 'seconds':time.monotonic()-started,'array_shapes':{k:list(v.shape) for k,v in result.arrays.items()},
 'artifact_bytes':len(base64.b64decode(artifact)),'work':dict(result.work)},sort_keys=True))
'''

_WORKSPACE = r'''

import resource, os, sys, json
from pathlib import Path
resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE,(12*1024**2,12*1024**2))
assert os.geteuid()==1000
assert Path('/sys/fs/cgroup/memory.max').read_text().strip()==str(4*1024**3)
a,b=Path('/sys/fs/cgroup/cpu.max').read_text().split();assert int(a)==2*int(b)
assert sys.version_info[:2]==(3,12)
from scipy.linalg.lapack import get_lapack_funcs
heevd=get_lapack_funcs('heevd_lwork',dtype=complex)
gesdd=get_lapack_funcs('gesdd_lwork',dtype=complex)
for n in range(1,257):
 for vectors in (0,1):
  c,i,r,info=heevd(n,compute_v=vectors)
  assert info==0 and c.real<=n*n+33*n+1 and r<=2*n*n+5*n+1 and i<=5*n+3
for n in range(1,1025):
 work,info=gesdd(n,n,compute_uv=0)
 assert info==0 and work.real<=66*n
print(json.dumps({'heevd_sizes':256,'heevd_modes':2,'gesdd_sizes':1024}))
'''


_PREFLIGHT = r'''
import os,sys,resource,json
from pathlib import Path
resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE,(12*1024**2,12*1024**2))
assert os.geteuid()==1000
assert Path('/sys/fs/cgroup/memory.max').read_text().strip()==str(4*1024**3)
a,b=Path('/sys/fs/cgroup/cpu.max').read_text().split();assert int(a)==2*int(b)
sys.path.insert(0,'/opt/qsim')
from pydantic import model_validator
from qsim import lindblad_models as models
constructed=[]
class Leaf(models._Complex):
 @model_validator(mode='before')
 @classmethod
 def observe(cls,value):
  constructed.append(True)
  raise RuntimeError('complex leaf constructed')
class Model(models._Model):
 matrices:dict[str,list[list[Leaf]]]
class Instrumented(models.LindbladRequest):
 model:Model
base={'schema_version':1,'operation':'spectrum','evals_count':1,
      'model':{'modes':[{'name':'q','kind':'oscillator','dimension':256}]}}
small=json.loads(json.dumps(base));small['model']['matrices']={'a':[[0]]}
try:
 Instrumented(**small)
except RuntimeError as error:
 assert str(error)=='complex leaf constructed'
else:
 raise AssertionError('leaf instrumentation inactive')
constructed.clear()
base['model']['matrices']={'m'+str(i):[[0]*256 for _ in range(256)] for i in range(32)}
encoded=json.dumps(base,separators=(',',':'))
assert len(encoded.encode())<5*1024**2
base=json.loads(encoded)
for cls in (Instrumented,models.LindbladRequest):
 try:
  cls(**base)
 except ValueError as error:
  assert 'certified Lindblad input owners exceed' in str(error)
 else:
  raise AssertionError('input-owner budget not enforced')
assert not constructed
print(json.dumps({'input_bytes':len(encoded.encode()),'complex_leaves_constructed':len(constructed)}))
'''

@unittest.skipUnless(os.environ.get('WQS_IMAGE_TEST') == '1',
                     'Run tools/ci/check-worker.sh for actual constrained image proofs')
class PhysicsCPUAdmission(unittest.TestCase):
    def test_all_cpu_operation_boundaries(self):
        for cell in _CASES:
            with self.subTest(cell=cell):
                result = subprocess.run([sys.executable, '-c', _CHILD, cell],
                    env={'PATH':'/usr/bin:/bin','HOME':'/tmp', 'OPENBLAS_NUM_THREADS':'1',
                         'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'},
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt['case'], cell)
                self.assertLessEqual(receipt['peak_plan_bytes'], 2*1024**3)
                self.assertLessEqual(receipt['peak_rss_kib']*1024, receipt['peak_plan_bytes'])
                print(json.dumps(receipt,sort_keys=True),flush=True)

    def test_raw_complex_expansion_preflight(self):
        result = subprocess.run([sys.executable, '-c', _PREFLIGHT],
            env={'PATH':'/usr/bin:/bin','HOME':'/tmp','OPENBLAS_NUM_THREADS':'1',
                 'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'},
            capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['complex_leaves_constructed'], 0)
        self.assertLess(receipt['input_bytes'], 5*1024**2)

    def test_frozen_native_workspace_queries(self):
        result = subprocess.run([sys.executable, '-c', _WORKSPACE],
            env={'PATH':'/usr/bin:/bin','HOME':'/tmp','OPENBLAS_NUM_THREADS':'1',
                 'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'},
            capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {'heevd_sizes':256,'heevd_modes':2,'gesdd_sizes':1024})


if __name__ == '__main__':
    unittest.main()
