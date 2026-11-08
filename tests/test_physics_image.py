"""Execute inside the built worker via check_worker_image.sh, not a host venv."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('WQS_IMAGE_TEST') == '1', 'Run tools/ci/check-worker.sh for actual image checks')
class PhysicsWorkerImage(unittest.TestCase):
    def test_package_boundary_and_distribution_notices(self):
        import pwd
        self.assertEqual(pwd.getpwuid(os.geteuid()).pw_name, 'qsim')
        self.assertEqual(Path('/sys/fs/cgroup/memory.max').read_text().strip(), str(4*1024**3))
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        self.assertEqual(int(quota), 2*int(period))
        root = Path('/opt/qsim/qsim')
        for name in ('physics_results', 'lindblad_models', 'lindblad_model', 'lindblad_adapter',
                     'scqubits_models', 'scqubits_adapter', 'josim_models', 'josim_validation', 'josim_adapter'):
            self.assertTrue((root/(name+'.py')).is_file(), name)
        for name in ('api', 'store', 'auth', 'controller', 'settings'):
            self.assertFalse((root/(name+'.py')).exists(), name)
        code = "from qsim import worker; import sys; assert not any(n in sys.modules for n in ('qutip','scqubits','scipy','numpy','qsim.auth','qsim.store','qsim.controller'))"
        subprocess.run([sys.executable, '-c', code], check=True)
        import importlib.metadata
        for distribution in ('numpy', 'scipy', 'qutip', 'scqubits'):
            notices = [f for f in importlib.metadata.files(distribution) if 'license' in str(f).lower()]
            self.assertTrue(notices, distribution)
            self.assertTrue(all(Path(importlib.metadata.distribution(distribution).locate_file(f)).is_file()
                                for f in notices))

    def test_real_certified_scqubits_child_and_closed_admission(self):
        import numpy as np
        env = {'PATH': '/usr/bin:/bin', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
        body = {'engine': 'scqubits', 'input_format': 'scqubits_model', 'schema_version': 1,
            'operation': 'spectrum', 'evals_count': 4,
            'model': {'qubit': 'Transmon', 'params': {'EJ': 15., 'EC': .3, 'ng': 0., 'ncut': 30}}}
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'input.json', Path(directory)/'output.json'
            source.write_text(json.dumps(body))
            subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
                           env=env, check=True)
            payload = json.loads(output.read_text())
            self.assertEqual(payload['status'], 'SUCCEEDED', payload)
            with np.load(io.BytesIO(base64.b64decode(payload['artifact'])), allow_pickle=False) as result:
                self.assertEqual(result['eigenvalues'].shape, (4,))
                self.assertTrue(np.isfinite(result['eigenvalues']).all())
            body['operation'] = 'export_for_lindblad'
            body['operators'] = ['n_operator']
            source.write_text(json.dumps(body))
            subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
                           env=env, check=True)
            payload = json.loads(output.read_text())
            self.assertEqual(payload['status'], 'FAILED')
            self.assertTrue(payload['error'].startswith('resource_limit:'))

    def test_josim_provenance_and_corresponding_source(self):
        from qsim.josim_adapter import _BINARY, _BINARY_SHA256
        self.assertTrue(_BINARY.is_file())
        self.assertEqual(hashlib.sha256(_BINARY.read_bytes()).hexdigest(), _BINARY_SHA256)
        manifest = json.loads(Path('/opt/josim/share/build-manifest.json').read_text())
        self.assertEqual(manifest['binary_sha256'], _BINARY_SHA256)
        for path, expected in manifest['runtime_libraries_sha256'].items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), expected, path)
        for path in ('/usr/share/doc/libc6/copyright','/usr/share/doc/libstdc++6/copyright'):
            self.assertTrue(Path(path).is_file(), path)
        from qsim.josim_models import JoSIMRequest
        plan = JoSIMRequest(netlist=Path('/proof/tests/fixtures/physics/josim/sfq.cir').read_text())._certified_allocation_plan()
        self.assertEqual(plan.coefficient_plan['build_image'], manifest['build_base'])
        self.assertEqual(plan.coefficient_plan['compiler'], manifest['compiler'])
        self.assertEqual(manifest['source_commit'], '02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224')
        self.assertTrue(Path('/opt/josim/share/corresponding-source.tar.gz').is_file())
        self.assertTrue(Path('/opt/josim/share/licenses/LGPL-2.1').is_file())
        self.assertTrue(Path('/opt/josim/share/licenses/JoSIM-LICENSE').is_file())
        self.assertTrue(Path('/opt/josim/share/licenses/WQS-LICENSE').is_file())
        for name in ('AMD.txt','BTF.txt','COLAMD.txt','KLU.txt','SuiteSparse_config.txt','GPL-2'):
            self.assertTrue((Path('/opt/josim/share/licenses')/name).is_file(), name)
        subprocess.run([str(_BINARY), '-v'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


    def test_maximum_certified_scqubits_shapes(self):
        import numpy as np
        parameters = {
            'Transmon': {'EJ':15.,'EC':.3,'ng':0.,'ncut':255},
            'TunableTransmon': {'EJmax':20.,'EC':.3,'d':.01,'flux':.2,'ng':0.,'ncut':255},
            'Fluxonium': {'EJ':8.9,'EC':2.5,'EL':.5,'flux':.3,'cutoff':512},
            'FluxQubit': {'EJ1':1.,'EJ2':1.,'EJ3':.8,'ECJ1':.016,'ECJ2':.016,'ECJ3':.021,
                          'ECg1':.83,'ECg2':.83,'ng1':0.,'ng2':0.,'flux':.4,'ncut':10},
        }
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'input.json', Path(directory)/'output.json'
            for qubit, params in parameters.items():
                for operation in ('spectrum','matrix_elements'):
                    with self.subTest(qubit=qubit,operation=operation):
                        body={'engine':'scqubits','schema_version':1,'operation':operation,
                              'evals_count':32,'model':{'qubit':qubit,'params':params}}
                        if operation=='spectrum': body['return_vectors']=True
                        else: body['operator']='n_1_operator' if qubit=='FluxQubit' else 'n_operator'
                        source.write_text(json.dumps(body))
                        subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(output)],
                            env={'PATH':'/usr/bin:/bin','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'},
                            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
                        payload=json.loads(output.read_text())
                        self.assertEqual(payload['status'],'SUCCEEDED',payload)
                        with np.load(io.BytesIO(base64.b64decode(payload['artifact'])),allow_pickle=False) as arrays:
                            self.assertEqual(arrays['eigenvalues'].shape,(32,))
                            self.assertTrue(all(np.isfinite(arrays[k]).all() for k in arrays.files if k!='metadata_json'))
                            if operation=='matrix_elements': self.assertEqual(arrays['matrix_elements'].shape,(32,32))

    def test_frozen_native_equivalence_and_bounded_capacity(self):
        import runpy
        p = runpy.run_path('/proof/spec/physics-engines/probe.py')
        binary, audit = Path('/opt/josim/bin/josim-cli'), Path('/audit')
        manifest = json.loads(Path('/opt/josim/share/build-manifest.json').read_text())
        self.assertEqual(hashlib.sha256(audit.read_bytes()).hexdigest(), manifest['audit_binary_sha256'])
        for name, (netlist, success) in p['_fixtures']().items():
            with self.subTest(frozen=name):
                result = p['_run'](binary, netlist, success, None)
                actual = {key: result[0][key] for key in ('status', 'diagnostic_sha256', 'csv_sha256', 'array_sha256', 'rows', 'bytes')}
                actual['header'] = list(result[2][0]) if success else []
                actual['fixture_sha256'] = hashlib.sha256(netlist.encode()).hexdigest()
                self.assertEqual(actual, p['FROZEN'][name])
        for name, netlist in p['_capacity_cases']().items():
            with self.subTest(capacity=name):
                measured = p['_run'](audit, netlist, True, None, audit=True)
                p['_verify_capacity'](measured[0]['history'])
                p['_compare'](p['_run'](binary, netlist, True, None), measured)

    def test_corresponding_source_rebuild_and_compiler_representation(self):
        import tarfile
        prefix = Path('/out')
        manifest = json.loads((prefix/'share/build-manifest.json').read_text())
        with tempfile.TemporaryDirectory(dir='/compile') as directory:
            root = Path(directory)
            source = root/'source'
            source.mkdir()
            with tarfile.open(prefix/'share/corresponding-source.tar.gz') as archive:
                archive.extractall(source, filter='data')
            for relative, expected in manifest['source_files'].items():
                self.assertEqual(hashlib.sha256((source/relative).read_bytes()).hexdigest(), expected, relative)
            subprocess.run(['bash', str(source/'rebuild.sh'), str(root/'build')], check=True,
                           stdout=subprocess.DEVNULL)
            self.assertEqual(hashlib.sha256((root/'build/josim-cli').read_bytes()).hexdigest(), manifest['binary_sha256'])
            unit = root/'representation.cpp'
            unit.write_text(r'''
#include "JoSIM/Transient.hpp"
#include <cassert>
#include <cstdint>
#include <limits>
#include <optional>
#include <string>
#include <vector>
int main() {
 static_assert(sizeof(double)==8 && sizeof(int64_t)==8);
 static_assert(sizeof(std::string)==32);
 static_assert(sizeof(std::vector<double>)<=32);
 static_assert(sizeof(std::optional<std::vector<double>>)<=32);
 for (size_t n : {1,16,80,4096}) {
  std::vector<double> v; v.reserve(n); assert(v.capacity()==n);
  std::string s; s.reserve(n); assert(s.capacity()<=15 || s.capacity()<=2*(n+1));
 }
 JoSIM::Transient t; t.tstop(1.0); t.tstep(1.0/4096); assert(t.simsize()==4096);
 for (double step : {0.0,-1.0,std::numeric_limits<double>::quiet_NaN(),
                     std::numeric_limits<double>::infinity(),std::ldexp(1.0,-63),
                     std::numeric_limits<double>::denorm_min()}) {
  t.tstep(step); bool rejected=false;
  try {t.simsize();} catch(const std::runtime_error&) {rejected=true;}
  assert(rejected);
 }
}
''')
            executable = root/'representation'
            subprocess.run(['c++', '-std=c++17', '-I'+str(source/'josim/include'), str(unit), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_real_private_lindblad_and_export_families(self):
        import numpy as np
        code = '''
import sys
from pathlib import Path
from qsim import worker,scqubits_models,lindblad_models,josim_models
from qsim.physics_results import AllocationPlan

def fixture_plan(self):
 d=self._physical_checks()
 return AllocationPlan('synthetic-image-test-only','private-image-numerics',{'hilbert':d},(),0,{},self._result_plan(d))
lindblad_models.LindbladRequest.admission_plan=fixture_plan
scqubits_models.ScqubitsRequest.admission_plan=fixture_plan
josim_models.JoSIMRequest.admission_plan=josim_models.JoSIMRequest._certified_allocation_plan
worker.simulate(Path(sys.argv[1]),Path(sys.argv[2]))
'''
        cases = []
        for operation, solver in [('evolve','se'),('evolve','me'),('evolve','mc'),
                                  ('propagator','se'),('propagator','me'),('spectrum',None),('steadystate',None)]:
            body = {'engine':'lindblad','schema_version':1,'operation':operation,
                    'model':{'modes':[{'name':'q','kind':'oscillator','dimension':2,'frequency':.017}]}}
            if operation in ('evolve','propagator'):
                body.update(solver=solver,times=[7.,8.,10.])
            if operation in ('evolve','steadystate'):
                body.update(observables=[{'name':'n','operator':{'kind':'product','factors':{'q':'n'}}}])
            if operation=='evolve':
                body.update(store_states='all',initial_state={'kind':'product','modes':{'q':{'kind':'fock','index':1}}})
            if solver in ('me','mc') or operation=='steadystate':
                body['model']['collapse']=[{'kind':'relaxation','mode':'q','T1':3.}]
            if solver=='mc':
                body.update(seed=42,trajectories=8)
            if operation=='spectrum':
                body.update(evals_count=2,return_vectors=True)
            cases.append(body)
        cases.append({'engine':'scqubits','schema_version':1,'operation':'export_for_lindblad',
            'evals_count':4,'operators':['n_operator'],
            'model':{'qubit':'Transmon','params':{'EJ':15.,'EC':.3,'ng':0.,'ncut':30}}})
        cases.append({'engine':'josim','schema_version':1,
            'netlist':Path('/proof/tests/fixtures/physics/josim/sfq.cir').read_text()})
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'input.json', Path(directory)/'output.json'
            for body in cases:
                with self.subTest(engine=body['engine'],operation=body.get('operation'),solver=body.get('solver')):
                    source.write_text(json.dumps(body))
                    subprocess.run([sys.executable,'-c',code,str(source),str(output)],
                        env={'PATH':'/usr/bin:/bin','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'},check=True,
                        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    payload=json.loads(output.read_text())
                    self.assertEqual(payload['status'],'SUCCEEDED',payload)
                    with np.load(io.BytesIO(base64.b64decode(payload['artifact'])),allow_pickle=False) as arrays:
                        self.assertTrue(all(np.isfinite(arrays[k]).all() for k in arrays.files if k!='metadata_json'))
                        if body['engine']=='josim':
                            times,traces=arrays['times'],arrays['traces']
                            self.assertEqual(traces.shape,(999,5))
                            window=(times>=50e-12)&(times<=200e-12)
                            for column in (0,2):
                                self.assertLess(abs(np.trapezoid(traces[window,column],times[window])/2.0678338484619295e-15-1),.02)
                        elif body['engine']=='lindblad' and body['operation']=='spectrum':
                            np.testing.assert_allclose(arrays['eigenvalues'],[0.,.017],atol=1e-12)
                        elif body['engine']=='scqubits':
                            np.testing.assert_allclose(arrays['hamiltonian'],np.diag(arrays['eigenvalues']),atol=1e-12)


if __name__ == '__main__':
    unittest.main()
