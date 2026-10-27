"""Combined dependency and API/native import boundaries for the CPU physics pins."""
import json
from importlib.metadata import metadata, version
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
from packaging.markers import Marker
from packaging.requirements import Requirement


PHYSICS_PINS = {'qutip': '5.3.1', 'scqubits': '4.3.1', 'scipy': '1.18.1'}
EXISTING_PINS = {'numpy': '2.5.4', 'clifft': '0.12.0', 'qsimcirq': '0.22.1',
                 'cirq-core': '1.7.0', 'qiskit': '2.6.0', 'qiskit-aer': '0.17.2',
                 'qiskit-qasm3-import': '0.6.0'}


def test_frozen_worker_versions_and_advertised_metadata():
    assert {name: version(name) for name in EXISTING_PINS} == EXISTING_PINS
    if sys.platform == 'linux':
        assert {name: version(name) for name in PHYSICS_PINS} == PHYSICS_PINS
    from qsim.api import capabilities
    advertised = capabilities(user='synthetic-version-probe')['engines']
    for engine, package in {'lindblad': 'qutip', 'scqubits': 'scqubits'}.items():
        if engine in advertised:
            assert advertised[engine]['version'] == version(package)


def test_physics_markers_preserve_existing_platforms_and_upstream_metadata():
    manifest = tomllib.loads(Path('pyproject.toml').read_text())
    requirements = {req.name: req for text in manifest['project']['optional-dependencies']['worker']
                    for req in (Requirement(text),)}
    environments = [Marker(text) for text in manifest['tool']['uv']['environments']]
    for platform in ('linux', 'darwin', 'win32', 'emscripten', 'freebsd'):
        environment = {'sys_platform': platform, 'python_version': '3.12', 'python_full_version': '3.12.3'}
        # The resolver partition covers all platforms, with exactly one fork.
        assert sum(marker.evaluate(environment) for marker in environments) == 1
        for package, expected in PHYSICS_PINS.items():
            req = requirements[package]
            assert str(req.specifier) == '==' + expected
            assert req.marker is not None and req.marker.evaluate(environment) == (platform == 'linux')
        for package in ('clifft', 'qsimcirq', 'qiskit', 'qiskit-aer', 'qiskit-qasm3-import'):
            assert requirements[package].marker is None
    base = {Requirement(text).name: Requirement(text) for text in manifest['project']['dependencies']}
    assert str(base['numpy'].specifier) == '==2.5.4' and base['numpy'].marker is None
    assert not set(PHYSICS_PINS) & set(base)
    lock = tomllib.loads(Path('uv.lock').read_text())
    scqubits = next(package for package in lock['package'] if package['name'] == 'scqubits')
    assert all(Marker(dep['marker']).evaluate({'sys_platform': 'linux'})
               and not Marker(dep['marker']).evaluate({'sys_platform': 'darwin'})
               for dep in scqubits['dependencies'])
    assert 'override-dependencies' not in manifest['tool']['uv']
    if sys.platform == 'linux':
        # Verify installed upstream metadata retains its incompatible Darwin bound.
        upstream = [Requirement(text) for text in metadata('scqubits').get_all('Requires-Dist')]
        assert any(req.name == 'scipy' and '<=1.13.1' in str(req.specifier)
                   and req.marker.evaluate({'sys_platform': 'darwin', 'python_version': '3.12'})
                   for req in upstream)


def test_api_import_stays_free_of_native_physics_solvers():
    subprocess.run([sys.executable, '-c', '''
import sys
import numpy
import qsim.api
assert not any(name.partition('.')[0] in {'qutip', 'scqubits', 'scipy', 'cupy', 'jax'}
               for name in sys.modules)
'''], check=True, capture_output=True, text=True)


@pytest.mark.skipif(sys.platform != 'linux', reason='Only new physics execution is certified on Linux')
def test_combined_native_imports_and_operations_fit_cpu_child_limits():
    completed = subprocess.run([sys.executable, '-c', '''
import json, resource
resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2, 12 * 1024**2))
import numpy as np
import scipy
import qutip as qt
import scqubits as scq
h = qt.sigmax()
state = qt.sesolve(h, qt.basis(2, 0), [0., .25]).states[-1]
assert abs(state.norm() - 1) < 1e-9
model = scq.Transmon(EJ=15., EC=.3, ng=0., ncut=30, truncated_dim=6,
                     esys_method='esys_scipy_dense')
energies, vectors = model.eigensys(evals_count=6)
assert np.all(np.isfinite(energies)) and vectors.shape == (61, 6)
assert abs((energies[1]-energies[0]) / 5.7 - 1) < .02
print(json.dumps({'rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  'address_limit': resource.getrlimit(resource.RLIMIT_AS)[0],
                  'file_limit': resource.getrlimit(resource.RLIMIT_FSIZE)[0]}))
'''], env={'PATH': str(Path(sys.executable).parent), 'OPENBLAS_NUM_THREADS': '1',
           'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'},
        check=True, capture_output=True, text=True)
    receipt = json.loads(completed.stdout)
    assert receipt['address_limit'] == 3 * 1024**3
    assert receipt['file_limit'] == 12 * 1024**2
    assert receipt['rss_kib'] > 0
