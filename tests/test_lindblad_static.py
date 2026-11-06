"""Real pinned CPU static numerics; private plans do not certify admission."""
from dataclasses import replace
import math

import numpy as np
import pytest
import qutip as qt

from qsim import lindblad_adapter as adapter
from qsim.lindblad_model import _build_model
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import AllocationPlan, serialize_physics_result


def request(operation='spectrum', *, dimension=4, frequency=.3, collapse=None, **controls):
    return LindbladRequest(schema_version=1, operation=operation,
        model={'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': dimension,
                         'frequency': frequency}], 'collapse': collapse or []},
        **({'evals_count': min(4, dimension)} if operation == 'spectrum' else {}), **controls)


def model_for(req):
    dimension = req._physical_checks()
    plan = AllocationPlan('synthetic-test-only', 'static-private-numerics',
        {'hilbert': dimension}, (), 0, {}, req._result_plan(dimension))
    return _build_model(req, plan)


def staged(req, model=None):
    model = model if model is not None else model_for(req)
    result = adapter._run_static(req, model)
    serialize_physics_result(result, model.allocation_plan.result)
    return result


@pytest.mark.parametrize('vectors', [False, True])
def test_sorted_spectrum_in_linear_ghz_and_optional_vectors(vectors):
    req = request(return_vectors=vectors)
    result = staged(req)
    assert list(result.arrays) == ['eigenvalues'] + (['eigenvectors'] if vectors else [])
    np.testing.assert_allclose(result.arrays['eigenvalues'], [.0, .3, .6, .9], atol=1e-12)
    if vectors:
        np.testing.assert_allclose(result.arrays['eigenvectors'], np.eye(4), atol=1e-12)
    assert result.metadata['energy_reference'] == 'absolute'
    assert result.metadata['energy_units'] == 'GHz'
    assert 'seed' not in result.metadata and 'solver' not in result.metadata
    assert result.work['eigensolver_calls'] == '1'


@pytest.mark.parametrize('return_state', [False, True])
def test_thermal_occupation_and_optional_stationary_state(return_state):
    req = request('steadystate', dimension=16, return_state=return_state,
        collapse=[{'kind': 'thermal', 'mode': 'q', 'rate': .2, 'occupation': .2}],
        observables=[{'name': 'number', 'operator': {'kind': 'product', 'factors': {'q': 'n'}}}])
    result = staged(req)
    assert list(result.arrays) == ['expectations'] + (['steady_state'] if return_state else [])
    assert result.arrays['expectations'].shape == (1,)
    assert abs(result.arrays['expectations'][0]-.2) < 1e-6
    assert result.metadata['observable_names'] == ['number']
    assert result.metadata['stationary_nullity'] == 1
    assert result.metadata['stationary_residual'] <= result.metadata['stationary_residual_limit']
    assert result.work['svd_calls'] == result.work['stationary_solver_calls'] == '1'
    if return_state:
        rho = result.arrays['steady_state']
        assert rho.shape == (16, 16)
        np.testing.assert_allclose(np.trace(rho), 1., atol=1e-7)
        np.testing.assert_allclose(rho, rho.conj().T, atol=1e-7)
        assert np.linalg.eigvalsh(rho).min() >= -1e-8


@pytest.mark.parametrize('collapse', [[], [{'kind': 'dephasing', 'mode': 'q', 'Tphi': 3.}]])
def test_nonunique_stationary_problem_refuses_before_native_solve(collapse, monkeypatch):
    req = request('steadystate', dimension=2, collapse=collapse)
    monkeypatch.setattr(qt, 'steadystate', lambda *a, **k: pytest.fail('selected nonunique state'))
    with pytest.raises(ValueError, match='nonunique_stationary_state'):
        staged(req)


def test_complex_matrix_spectrum_eigenpair_residuals_and_projectors():
    req = LindbladRequest(schema_version=1, operation='spectrum', evals_count=2, return_vectors=True,
        model={'modes': [{'name': 'spin', 'kind': 'matrix', 'dimension': 2,
                         'hamiltonian': [[1., {'re': 0., 'im': 2.}],
                                         [{'re': 0., 'im': -2.}, -1.]]}]})
    result = staged(req)
    values, vectors = result.arrays.values()
    h = np.array([[1., 2j], [-2j, -1.]])
    np.testing.assert_allclose(values, [-math.sqrt(5), math.sqrt(5)], atol=1e-12)
    np.testing.assert_allclose(h@vectors, vectors*values, atol=1e-12)
    np.testing.assert_allclose(vectors.conj().T@vectors, np.eye(2), atol=1e-12)
    assert result.metadata['tensor_order'] == ['spin']


def test_degenerate_spectrum_does_not_choose_an_artificial_ordered_basis():
    req = request(frequency=0., return_vectors=True)
    result = staged(req)
    np.testing.assert_array_equal(result.arrays['eigenvalues'], np.zeros(4))
    vectors = result.arrays['eigenvectors']
    np.testing.assert_allclose(vectors.conj().T@vectors, np.eye(4), atol=1e-12)


def test_dispersive_shift_matches_declared_perturbative_reference():
    req = LindbladRequest(schema_version=1, operation='spectrum', evals_count=30, return_vectors=True,
        model={'modes': [{'name': 'q', 'kind': 'duffing', 'dimension': 5,
                          'frequency': 6., 'kerr': -.25},
                         {'name': 'r', 'kind': 'oscillator', 'dimension': 6, 'frequency': 5.}],
               'hamiltonian': [{'coefficient': .05, 'hc': True,
                   'operator': {'kind': 'product', 'factors': {'q': 'adag', 'r': 'a'}}}]})
    result = staged(req)
    energies, vectors = result.arrays.values()
    dressed = {}
    for q, r in ((0, 0), (0, 1), (1, 0), (1, 1)):
        dressed[q, r] = energies[np.argmax(abs(vectors[q*6+r])**2)]
    shift = dressed[1, 1]-dressed[1, 0]-dressed[0, 1]+dressed[0, 0]
    reference = 2*.05**2*(-.25)/(1.*(1.-.25))
    assert abs(shift/reference-1) < .05
    assert result.metadata['tensor_order'] == ['q', 'r']
    assert result.metadata['dimensions'] == [5, 6]


@pytest.mark.parametrize('vectors', [False, True])
def test_largest_structural_spectrum_and_exact_result_reservation(vectors):
    size = 256
    matrix = np.diag(np.ones(size-1), 1)+np.diag(np.ones(size-1), -1)
    req = LindbladRequest(schema_version=1, operation='spectrum', evals_count=size,
        return_vectors=vectors, model={'modes': [{'name': 'chain', 'kind': 'matrix',
            'dimension': size, 'hamiltonian': matrix.tolist()}]})
    result = staged(req)
    expected = np.sort(2*np.cos(np.arange(1, size+1)*np.pi/(size+1)))
    np.testing.assert_allclose(result.arrays['eigenvalues'], expected, atol=1e-12)
    assert req._result_plan(size).numeric_bytes == size*8+(size*size*16 if vectors else 0)


@pytest.mark.parametrize('scale', [1e-120, 1., 1e120])
def test_uniqueness_and_residual_are_scale_relative(scale):
    req = request('steadystate', dimension=2, frequency=0.,
        collapse=[{'kind': 'thermal', 'mode': 'q', 'rate': scale, 'occupation': .25}])
    result = staged(req)
    np.testing.assert_allclose(result.arrays['steady_state'], np.diag([5/6, 1/6]), atol=1e-12)
    assert result.metadata['stationary_residual'] <= result.metadata['stationary_residual_limit']


@pytest.mark.parametrize('operation', ['spectrum', 'steadystate'])
def test_scalar_space_and_native_core_restoration(operation):
    req = request(operation, dimension=1)
    core = dict(qt.settings.core.options)
    result = staged(req)
    np.testing.assert_array_equal(next(iter(result.arrays.values())),
                                   [0.] if operation == 'spectrum' else [[1.]])
    assert dict(qt.settings.core.options) == core


def test_no_observables_no_return_state_has_no_output_state_but_validates_it(monkeypatch):
    req = request('steadystate', dimension=2, return_state=False,
                  collapse=[{'kind': 'relaxation', 'mode': 'q', 'T1': 3.}])
    result = staged(req)
    assert result.arrays == {}
    assert result.work['stationary_solver_calls'] == '1'
    monkeypatch.setattr(qt, 'steadystate', lambda *a, **k: qt.Qobj(np.diag([1.1, -.1])))
    with pytest.raises(ValueError, match='density trace, Hermiticity or positivity'):
        staged(req)


@pytest.mark.parametrize('operation', ['spectrum', 'steadystate'])
def test_static_drives_refused_before_native_construction(operation, monkeypatch):
    body = request(operation).canonical_envelope()
    body['model']['drives'] = [{'operator': {'kind': 'product', 'factors': {'q': 'x'}},
        'amplitude': .1, 'envelope': {'shape': 'constant'}}]
    req = LindbladRequest.model_validate(body)
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('native allocation'))
    with pytest.raises(ValueError, match='static operation rejects drives'):
        adapter.run_lindblad(req, None, execution_context={})


@pytest.mark.parametrize('operation', ['spectrum', 'steadystate'])
def test_public_static_admission_stays_certificate_gated(operation, monkeypatch):
    req = request(operation)
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('native allocation'))
    with pytest.raises(ValueError, match='certificate'):
        adapter.run_lindblad(req, None, execution_context={})


def test_wrong_result_reservation_refuses_before_native_solver(monkeypatch):
    req = request(return_vectors=True)
    model = model_for(req)
    model = replace(model, allocation_plan=model_for(request()).allocation_plan)
    monkeypatch.setattr(adapter.la, 'eigh', lambda *a, **k: pytest.fail('solver before result budget'))
    with pytest.raises(ValueError, match='allocation result reservation'):
        staged(req, model)


@pytest.mark.parametrize('stage', ['spectrum', 'svd', 'stationary'])
def test_native_failure_is_sanitized_charged_and_restores_settings(stage, monkeypatch):
    req = request() if stage == 'spectrum' else request('steadystate', dimension=2,
        collapse=[{'kind': 'relaxation', 'mode': 'q', 'T1': 3.}])
    model = model_for(req)
    def failed(*a, **k):
        raise np.linalg.LinAlgError('private path or model contents')
    if stage == 'spectrum':
        monkeypatch.setattr(adapter.la, 'eigh', failed)
    elif stage == 'svd':
        monkeypatch.setattr(adapter.la, 'svdvals', failed)
    else:
        monkeypatch.setattr(qt, 'steadystate', failed)
    core = dict(qt.settings.core.options)
    with pytest.raises(RuntimeError, match='native_nonconvergence') as caught:
        staged(req, model)
    assert 'private' not in str(caught.value)
    key = {'spectrum': 'eigensolver_calls', 'svd': 'svd_calls',
           'stationary': 'stationary_solver_calls'}[stage]
    assert caught.value.work[key] == '1'
    assert dict(qt.settings.core.options) == core


@pytest.mark.parametrize('problem', ['not_null', 'multiple', 'ambiguous'])
def test_approved_singular_value_gap_threshold(problem, monkeypatch):
    req = request('steadystate', dimension=2,
                  collapse=[{'kind': 'relaxation', 'mode': 'q', 'T1': 3.}])
    tau = 100*np.finfo(float).eps*4
    values = {'not_null': [1., .5, .25, 2*tau], 'multiple': [1., .5, .5*tau, 0.],
              'ambiguous': [1., .5, 5*tau, 0.]}[problem]
    monkeypatch.setattr(adapter.la, 'svdvals', lambda *a: np.array(values))
    monkeypatch.setattr(qt, 'steadystate', lambda *a, **k: pytest.fail('ambiguous solve'))
    with pytest.raises(ValueError, match='nonunique_stationary_state') as caught:
        staged(req)
    assert caught.value.work['svd_calls'] == '1'
    assert caught.value.work['stationary_solver_calls'] == '0'


def test_physical_but_nonstationary_native_density_is_rejected(monkeypatch):
    req = request('steadystate', dimension=2,
                  collapse=[{'kind': 'relaxation', 'mode': 'q', 'T1': 3.}])
    monkeypatch.setattr(qt, 'steadystate', lambda *a, **k: qt.qeye(2)/2)
    with pytest.raises(ValueError, match='native stationary residual'):
        staged(req)


def test_stationary_complex_expectations_preserve_names_and_order():
    amplitude, gamma = .017, .2
    req = LindbladRequest(schema_version=1, operation='steadystate', return_state=False,
        model={'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': 2, 'frequency': 0.}],
            'hamiltonian': [{'coefficient': amplitude,
                'operator': {'kind': 'product', 'factors': {'q': 'x'}}}],
            'collapse': [{'kind': 'relaxation', 'mode': 'q', 'T1': 1/gamma}]},
        observables=[{'name': name, 'operator': {'kind': 'product', 'factors': {'q': op}}}
                     for name, op in [('lowering', 'a'), ('occupation', 'n')]])
    result = staged(req)
    omega = 4*math.pi*amplitude
    denominator = gamma**2+2*omega**2
    np.testing.assert_allclose(result.arrays['expectations'],
        [-1j*omega*gamma/denominator, omega**2/denominator], atol=1e-12)
    assert result.metadata['observable_names'] == ['lowering', 'occupation']
    assert result.arrays['expectations'].shape == (2,)
    assert 'steady_state' not in result.arrays


@pytest.mark.parametrize('problem', ['values', 'vectors', 'orthogonality', 'residual'])
def test_invalid_native_eigenpairs_fail_without_repair(problem, monkeypatch):
    req = request(return_vectors=True)
    values = np.array([0., .3, .6, .9])
    vectors = np.eye(4, dtype=complex)
    if problem == 'values':
        values[0] = np.nan
    elif problem == 'vectors':
        vectors[0, 0] = np.nan
    elif problem == 'orthogonality':
        vectors *= .9
    else:
        vectors = vectors[:, ::-1]
    monkeypatch.setattr(adapter.la, 'eigh', lambda *a, **k: (values, vectors))
    with pytest.raises(ValueError, match='invalid_result'):
        staged(req)


def test_invalid_stationary_shape_is_checked_when_output_is_omitted(monkeypatch):
    req = request('steadystate', dimension=2, return_state=False,
                  collapse=[{'kind': 'relaxation', 'mode': 'q', 'T1': 3.}])
    monkeypatch.setattr(qt, 'steadystate', lambda *a, **k: qt.qeye(3)/3)
    with pytest.raises(ValueError, match='native state shape'):
        staged(req)


def test_unrequested_eigenvectors_are_not_constructed(monkeypatch):
    original = adapter.la.eigh
    seen = []
    def native(*a, **k):
        seen.append(k)
        return original(*a, **k)
    monkeypatch.setattr(adapter.la, 'eigh', native)
    staged(request())
    assert seen == [{'eigvals_only': True, 'driver': 'evd'}]


@pytest.mark.parametrize('rate', [1e-15, 2e-12])
def test_real_weakly_connected_stationary_gap_is_refused(rate):
    req = LindbladRequest(schema_version=1, operation='steadystate',
        model={'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': 3, 'frequency': 0.}],
            'collapse': [{'kind': 'operator', 'rate': strength,
                'operator': {'kind': 'product', 'factors': {'q': primitive}}}
                for strength, primitive in [(1., 'proj:0,1'), (rate, 'proj:1,2')]]})
    with pytest.raises(ValueError, match='nonunique_stationary_state'):
        staged(req)


@pytest.mark.parametrize('operation,dimension', [('spectrum', 257), ('steadystate', 33)])
def test_structural_dimension_limit_refuses_before_native_build(operation, dimension, monkeypatch):
    req = request(operation, dimension=dimension)
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('native allocation'))
    with pytest.raises(ValueError, match='resource_limit'):
        adapter.run_lindblad(req, None, execution_context={})


def test_largest_structural_stationary_density_including_unreturned_internal_state():
    req = request('steadystate', dimension=32, frequency=.017, return_state=False,
        collapse=[{'kind': 'thermal', 'mode': 'q', 'rate': .01, 'occupation': .05}],
        observables=[{'name': 'number', 'operator': {'kind': 'product', 'factors': {'q': 'n'}}}])
    result = staged(req)
    np.testing.assert_allclose(result.arrays['expectations'], [.05], atol=1e-6)
    assert req._result_plan(32).numeric_bytes == 16
    assert result.metadata['stationary_residual'] <= result.metadata['stationary_residual_limit']


def test_tensor_stationary_density_and_ordered_observables():
    req = LindbladRequest(schema_version=1, operation='steadystate',
        model={'modes': [{'name': name, 'kind': 'oscillator', 'dimension': dimension,
                         'frequency': .1} for name, dimension in [('left', 2), ('right', 3)]],
            'collapse': [{'kind': 'relaxation', 'mode': name, 'T1': time}
                         for name, time in [('left', 3.), ('right', 7.)]]},
        observables=[{'name': name, 'operator': {'kind': 'product', 'factors': {mode: 'n'}}}
                     for name, mode in [('right occupation', 'right'), ('left occupation', 'left')]])
    result = staged(req)
    np.testing.assert_allclose(result.arrays['steady_state'], np.diag([1., 0, 0, 0, 0, 0]), atol=1e-12)
    np.testing.assert_allclose(result.arrays['expectations'], [0., 0.], atol=1e-12)
    assert result.metadata['tensor_order'] == ['left', 'right']
    assert result.metadata['observable_names'] == ['right occupation', 'left occupation']


@pytest.mark.parametrize('problem', ['tensor_order', 'operator_dimensions'])
def test_native_static_tensor_mismatch_refuses_before_solver(problem, monkeypatch):
    req = request()
    model = model_for(req)
    model = (replace(model, tensor_order=('other',)) if problem == 'tensor_order' else
             replace(model, hamiltonian=qt.Qobj(model.hamiltonian.full(), dims=[[2, 2], [2, 2]])))
    monkeypatch.setattr(adapter.la, 'eigh', lambda *a, **k: pytest.fail('solver before validation'))
    with pytest.raises(ValueError, match='invalid_model'):
        staged(req, model)
