"""Native numerical evidence; public execution remains certificate-gated."""
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
from qsim.physics_results import AllocationPlan, serialize_physics_result
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
    result = adapter._run_native(req)
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
    summary, artifact = serialize_physics_result(result, req._result_plan(actual.shape[0]))
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


def test_public_execution_remains_blocked_without_certificate(monkeypatch):
    req = request()
    plan = AllocationPlan('not-a-certificate', 'esys_scipy_dense', {}, (), 0, {}, req._result_plan(61))
    def forbidden(_):
        pytest.fail('native construction preceded certified admission')
    monkeypatch.setattr(adapter, '_native_constructor', forbidden)
    with pytest.raises(ValueError, match='frozen scqubits native allocation certificate'):
        adapter.run_scqubits(req, plan, execution_context={})


def test_import_and_failed_admission_do_not_import_native_libraries():
    code = '''
import sys
from qsim.scqubits_adapter import run_scqubits
from qsim.scqubits_models import ScqubitsRequest
r=ScqubitsRequest(schema_version=1, operation="spectrum", model={"qubit":"Transmon", "params":{"EJ":15.,"EC":.3,"ng":0.,"ncut":30}})
try:
    run_scqubits(r, None, execution_context={})
except ValueError as exc:
    assert "certificate" in str(exc)
else:
    raise AssertionError("uncertified admission succeeded")
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
    result = adapter._run_native(req)
    assert result.arrays['eigenvectors'].shape == (req._physical_checks(), 32)
    assert result.arrays['eigenvalues'].shape == (32,)
    serialize_physics_result(result, req._result_plan(req._physical_checks()))


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
