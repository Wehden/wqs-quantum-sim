"""Certified CPU runtime identity and native numerical execution evidence."""
import copy
import io
import base64
import json
import subprocess
import sys

import numpy as np
import pytest
import scqubits as scq

from qsim import scqubits_adapter as adapter
from qsim.physics_results import serialize_physics_result
from qsim.scqubits_models import ScqubitsRequest


PARAMETERS = {
    'Transmon': {'EJ': 15., 'EC': .3, 'ng': 0., 'ncut': 30},
    'TunableTransmon': {'EJmax': 20., 'EC': .3, 'd': .01, 'flux': .2, 'ng': 0., 'ncut': 30},
    'Fluxonium': {'EJ': 8.9, 'EC': 2.5, 'EL': .5, 'flux': .3, 'cutoff': 110},
    'FluxQubit': {'EJ1': 1., 'EJ2': 1., 'EJ3': .8, 'ECJ1': .016, 'ECJ2': .016,
                 'ECJ3': .021, 'ECg1': .83, 'ECg2': .83, 'ng1': 0., 'ng2': 0.,
                 'flux': .4, 'ncut': 5},
}
OPERATORS = {
    'Transmon': ('n_operator', 'cos_phi_operator', 'sin_phi_operator'),
    'TunableTransmon': ('n_operator', 'cos_phi_operator', 'sin_phi_operator'),
    'Fluxonium': ('n_operator', 'phi_operator', 'cos_phi_operator', 'sin_phi_operator'),
    'FluxQubit': ('n_1_operator', 'n_2_operator', 'cos_phi_1_operator',
                  'cos_phi_2_operator', 'sin_phi_1_operator', 'sin_phi_2_operator'),
}


def request(qubit='Transmon', operation='spectrum', **changes):
    body = {'schema_version': 1, 'operation': operation,
            'model': {'qubit': qubit, 'params': copy.deepcopy(PARAMETERS[qubit])}}
    if operation == 'matrix_elements':
        body['operator'] = OPERATORS[qubit][0]
    body.update(changes)
    return ScqubitsRequest(**body)


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_spectrum_matches_pinned_native_eigenpairs_and_npz(qubit):
    req = request(qubit, return_vectors=True)
    plan = req.admission_plan()
    result = adapter.run_scqubits(req, plan, execution_context={"backend": "cpu"})
    direct = getattr(scq, qubit)(**PARAMETERS[qubit], truncated_dim=6,
                               esys_method='esys_scipy_dense', evals_method='evals_scipy_dense')
    energies, vectors = direct.eigensys(evals_count=6)
    np.testing.assert_allclose(result.arrays['eigenvalues'], energies, atol=1e-9, rtol=0)
    # A projector remains invariant under arbitrary phases of the eigenvectors.
    actual = result.arrays['eigenvectors']
    np.testing.assert_allclose(actual @ actual.conj().T, vectors @ vectors.conj().T,
                               atol=1e-9, rtol=0)
    assert result.metadata['units']['eigenvalues'] == 'GHz'
    assert result.metadata['backend'] == 'cpu'
    assert result.metadata['engine_version'] == '4.3.1'
    assert result.metadata['raw_basis_dimension'] == req._physical_checks()
    assert result.work == {'eigensystem_calls': '1', 'raw_basis_dimension': str(actual.shape[0]),
                           'requested_levels': '6', 'operator_transforms': '0'}
    summary, artifact = serialize_physics_result(result, plan.result)
    assert summary['arrays']['eigenvalues']['units'] == 'GHz'
    with np.load(io.BytesIO(base64.b64decode(artifact)), allow_pickle=False) as loaded:
        np.testing.assert_array_equal(loaded['eigenvectors'], actual)
        assert json.loads(str(loaded['metadata_json']))['basis']['phase_convention'] == 'native arbitrary phases'


@pytest.mark.parametrize('qubit,operator', [(q, o) for q, ops in OPERATORS.items() for o in ops])
def test_matrix_elements_reuse_exactly_one_eigensystem(qubit, operator, monkeypatch):
    req = request(qubit, 'matrix_elements', operator=operator)
    native = adapter._native_constructor(req)
    calls = []
    original = native.eigensys
    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(native, 'eigensys', counted)
    monkeypatch.setattr(adapter, '_native_constructor', lambda _: native)
    result = adapter._run_native(req)
    assert calls == [{'evals_count': 6}]
    energies, vectors = original(evals_count=6)
    expected = getattr(native, operator)(energy_esys=(energies, vectors))
    np.testing.assert_allclose(result.arrays['matrix_elements'], expected, atol=1e-9, rtol=0)
    assert list(result.arrays) == ['eigenvalues', 'matrix_elements']
    assert result.work['operator_transforms'] == '1'
    assert result.metadata['operator'] == operator
    assert result.metadata['units']['matrix_elements'] == ('radians' if operator == 'phi_operator' else 'dimensionless')
    serialize_physics_result(result, req._result_plan(native.hilbertdim()))


def test_transmon_analytic_e01():
    result = adapter._run_native(request())
    gap = np.diff(result.arrays['eigenvalues'])[0]
    approximate = np.sqrt(8 * 15 * .3) - .3
    assert abs(gap / approximate - 1) < .02


def test_degenerate_subspace_and_operator_block_are_phase_invariant():
    req = request(evals_count=3, return_vectors=True)
    req.model.params.EJ = 0.
    req.model.params.ncut = 3
    result = adapter._run_native(req)
    energies, vectors = result.arrays['eigenvalues'], result.arrays['eigenvectors']
    assert abs(energies[1] - energies[2]) < 1e-12
    rotation = np.array([[1, 1j], [1j, 1]], dtype=complex) / np.sqrt(2)
    block = vectors[:, 1:3]
    rotated = block @ rotation
    np.testing.assert_allclose(block @ block.conj().T, rotated @ rotated.conj().T, atol=1e-12)
    native = adapter._native_constructor(req)
    charge = native.n_operator()
    np.testing.assert_allclose(np.linalg.eigvalsh(block.conj().T @ charge @ block),
                               np.linalg.eigvalsh(rotated.conj().T @ charge @ rotated), atol=1e-12)
    assert result.metadata['basis']['degenerate_vectors'] == 'arbitrary basis within degenerate subspaces'


@pytest.mark.parametrize('corruption', ['policy', 'peak', 'dimensions', 'algorithm'])
def test_public_execution_rejects_forged_plan_before_native_constructor(corruption, monkeypatch):
    from dataclasses import replace

    req = request()
    plan = req.admission_plan()
    changes = {'policy': {'policy_revision': 'obsolete'}, 'peak': {'peak_bytes': plan.peak_bytes+1},
               'dimensions': {'dimensions': {}}, 'algorithm': {'algorithm': 'uncertified'}}
    plan = replace(plan, **changes[corruption])
    monkeypatch.setattr(adapter, '_native_constructor',
                        lambda _: pytest.fail('native construction preceded certified admission'))
    with pytest.raises(ValueError, match='allocation plan'):
        adapter.run_scqubits(req, plan, execution_context={})


def test_import_and_plan_rejection_do_not_import_native_libraries():
    code = '''
import sys
from qsim.scqubits_adapter import run_scqubits
from qsim.scqubits_models import ScqubitsRequest
r=ScqubitsRequest(schema_version=1, operation="spectrum", model={"qubit":"Transmon", "params":{"EJ":15.,"EC":.3,"ng":0.,"ncut":30}})
p=r.admission_plan()
assert p.policy_revision
try:
    run_scqubits(r, None, execution_context={})
except ValueError as exc:
    assert "allocation plan" in str(exc)
else:
    raise AssertionError("invalid plan succeeded")
assert not any(x in sys.modules for x in ("scqubits", "qutip", "scipy"))
'''
    subprocess.run([sys.executable, '-c', code], check=True)


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_raw_basis_bound_checked_before_native_construction(qubit):
    req = request(qubit)
    if qubit == 'Fluxonium':
        req.model.params.cutoff = 513
    else:
        req.model.params.ncut = 11 if qubit == 'FluxQubit' else 256
    with pytest.raises(ValueError, match='resource_limit'):
        adapter._native_constructor(req)


@pytest.mark.parametrize('corruption', ['nan', 'nonhermitian', 'shape'])
def test_bad_native_hamiltonian_refused_before_diagonalization(corruption, monkeypatch):
    req = request()
    native = adapter._native_constructor(req)
    h = native.hamiltonian().astype(complex)
    if corruption == 'nan':
        h[0, 0] = np.nan
    elif corruption == 'nonhermitian':
        h[0, 1] += 1j
    else:
        h = h[:-1, :-1]
    monkeypatch.setattr(native, 'hamiltonian', lambda: h)
    monkeypatch.setattr(native, 'eigensys', lambda **kw: pytest.fail('unsafe Hamiltonian reached eigensolver'))
    with pytest.raises(ValueError, match='invalid_result'):
        adapter._eigensystem(native, req)


@pytest.mark.parametrize('corruption', ['nan', 'complex_energy', 'orthogonality', 'residual', 'shape'])
def test_bad_native_eigenpairs_refused(corruption, monkeypatch):
    req = request()
    native = adapter._native_constructor(req)
    energies, vectors = native.eigensys(evals_count=6)
    if corruption == 'nan':
        vectors[0, 0] = np.nan
    elif corruption == 'complex_energy':
        energies = energies.astype(complex) + 1j
    elif corruption == 'orthogonality':
        vectors[:, 1] = vectors[:, 0]
    elif corruption == 'residual':
        energies += 1
    else:
        vectors = vectors[:-1]
    monkeypatch.setattr(native, 'eigensys', lambda **kw: (energies, vectors))
    with pytest.raises(ValueError, match='invalid_result'):
        adapter._eigensystem(native, req)


def test_unsupported_export_is_not_implicitly_enabled():
    req = request(operation='export_for_lindblad', operators=[])
    with pytest.raises(ValueError, match='not implemented'):
        adapter._run_native(req)


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_largest_raw_basis_and_32_levels_remain_numerically_valid(qubit):
    req = request(qubit, return_vectors=True, evals_count=32)
    if qubit == 'Fluxonium':
        req.model.params.cutoff = 512
    else:
        req.model.params.ncut = 10 if qubit == 'FluxQubit' else 255
    plan = req.admission_plan()
    result = adapter.run_scqubits(req, plan, execution_context={})
    assert result.arrays['eigenvectors'].shape == (req._physical_checks(), 32)
    assert result.arrays['eigenvalues'].shape == (32,)
    serialize_physics_result(result, plan.result)


@pytest.mark.parametrize('corruption', ['nan', 'nonhermitian', 'shape'])
def test_invalid_native_operator_cannot_be_transformed(corruption, monkeypatch):
    req = request(operation='matrix_elements')
    native = adapter._native_constructor(req)
    esys = adapter._eigensystem(native, req)
    raw = native.n_operator().astype(complex)
    if corruption == 'nan':
        raw[0, 0] = np.nan
    elif corruption == 'nonhermitian':
        raw[0, 1] = 1j
    else:
        raw = raw[:-1]
    monkeypatch.setattr(native, 'n_operator', lambda: raw)
    with pytest.raises(ValueError, match='invalid_result'):
        adapter._operator_matrices(native, req, esys)


def test_native_eigenpairs_are_sorted_together(monkeypatch):
    req = request()
    native = adapter._native_constructor(req)
    energies, vectors = native.eigensys(evals_count=6)
    permutation = np.array([5, 1, 3, 0, 2, 4])
    monkeypatch.setattr(native, 'eigensys', lambda **kw: (energies[permutation], vectors[:, permutation]))
    sorted_energies, sorted_vectors = adapter._eigensystem(native, req)
    np.testing.assert_array_equal(sorted_energies, energies)
    np.testing.assert_array_equal(sorted_vectors, vectors.astype(complex))


@pytest.mark.parametrize('qubit,operator', [(q, o) for q, ops in OPERATORS.items() for o in ops])
def test_public_matrix_elements_use_certified_plan_and_serialization(qubit, operator):
    req = request(qubit, 'matrix_elements', operator=operator)
    plan = req.admission_plan()
    result = adapter.run_scqubits(req, plan, execution_context={})
    assert result.arrays['matrix_elements'].shape == (6, 6)
    assert result.metadata['policy_revision'] == plan.policy_revision
    assert result.metadata['algorithm'] == plan.algorithm
    serialize_physics_result(result, plan.result)


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_largest_matrix_elements_cell_uses_real_certified_plan(qubit):
    req = request(qubit, 'matrix_elements', evals_count=32)
    if qubit == 'Fluxonium':
        req.model.params.cutoff = 512
    else:
        req.model.params.ncut = 10 if qubit == 'FluxQubit' else 255
    plan = req.admission_plan()
    result = adapter.run_scqubits(req, plan, execution_context={})
    assert result.arrays['matrix_elements'].shape == (32, 32)
    serialize_physics_result(result, plan.result)


@pytest.mark.parametrize('package', ['numpy', 'scipy', 'scqubits', 'qutip'])
def test_runtime_version_mismatch_precedes_native_constructor(package, monkeypatch):
    import scipy
    import qutip

    req = request()
    plan = req.admission_plan()
    module = {'numpy': np, 'scipy': scipy, 'scqubits': scq, 'qutip': qutip}[package]
    monkeypatch.setattr(module, '__version__', 'unapproved-runtime')
    monkeypatch.setattr(adapter, '_native_constructor',
                        lambda _: pytest.fail('runtime mismatch reached native constructor'))
    with pytest.raises(ValueError, match='execution_policy_mismatch: scqubits runtime identity'):
        adapter.run_scqubits(req, plan, execution_context={})


def test_export_stays_closed_at_public_admission():
    req = request(operation='export_for_lindblad', operators=[])
    with pytest.raises(ValueError, match='resource_limit'):
        req.admission_plan()


@pytest.mark.parametrize('kind', ['source_hashes', 'native_hashes'])
@pytest.mark.parametrize('corruption', ['content', 'missing', 'extra', 'short'])
def test_runtime_file_mismatch_precedes_native_constructor(kind, corruption, monkeypatch):
    from qsim.scqubits_models import _CPU_CERTIFICATE

    req = request()
    plan = req.admission_plan()
    relative, expected = next(iter(_CPU_CERTIFICATE[kind].items()))
    original_open = open
    def changed_open(path, mode):
        if path.endswith('/' + relative):
            if corruption == 'missing':
                raise OSError('sensitive synthetic installation path')
            size = expected['size'] + (1 if corruption == 'extra' else -1 if corruption == 'short' else 0)
            return io.BytesIO(b'\0' * size)
        return original_open(path, mode)
    monkeypatch.setattr(adapter, 'open', changed_open, raising=False)
    monkeypatch.setattr(adapter, '_native_constructor',
                        lambda _: pytest.fail('changed runtime file reached native constructor'))
    with pytest.raises(ValueError) as error:
        adapter.run_scqubits(req, plan, execution_context={})
    assert str(error.value) == 'execution_policy_mismatch: scqubits runtime identity'
    assert 'sensitive' not in str(error.value)


def test_runtime_hash_reads_are_bounded_and_identity_is_server_owned(monkeypatch):
    from qsim.scqubits_models import _CPU_CERTIFICATE

    calls = []
    original_open = open
    class CheckedFile:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size):
            assert 0 < size <= 65536
            calls.append(size)
            return self.stream.read(size)
    monkeypatch.setattr(adapter, 'open', lambda path, mode: CheckedFile(original_open(path, mode)), raising=False)
    req = request()
    before = req.canonical_envelope()
    result = adapter.run_scqubits(req, req.admission_plan(), execution_context={
        'source_hashes': {}, 'native_hashes': {}, 'versions': {},
    })
    assert calls and max(calls) == 65536
    assert result.metadata['backend'] == 'cpu'
    assert req.canonical_envelope() == before
    assert req.admission_plan().coefficient_plan == {'certificate': _CPU_CERTIFICATE['digest']}


def test_runtime_packages_must_share_certificate_root(monkeypatch):
    req = request()
    plan = req.admission_plan()
    monkeypatch.setattr(scq, '__file__', '/synthetic/other-install/scqubits/__init__.py')
    monkeypatch.setattr(adapter, '_native_constructor',
                        lambda _: pytest.fail('different package root reached native constructor'))
    with pytest.raises(ValueError, match='scqubits runtime identity'):
        adapter.run_scqubits(req, plan, execution_context={})


def test_gpu_context_refused_before_runtime_or_constructor(monkeypatch):
    req = request()
    monkeypatch.setattr(adapter, '_verify_runtime_identity',
                        lambda: pytest.fail('unsupported backend reached runtime verification'))
    with pytest.raises(ValueError, match='unsupported scqubits backend'):
        adapter.run_scqubits(req, req.admission_plan(), execution_context={'backend': 'gpu'})
