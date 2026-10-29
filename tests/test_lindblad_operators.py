"""Native static construction only; synthetic plans do not certify admission."""
import math
import subprocess
import sys

import numpy as np
import pytest

from qsim.lindblad_models import LindbladRequest
from qsim.lindblad_model import (
    NativeLindbladModel, build_lindblad_model, _build_static_model, _operator,
)
from qsim.physics_results import AllocationPlan


def request(modes=None, **model):
    return LindbladRequest(schema_version=1, operation='spectrum', evals_count=1,
        model={'modes': modes or [{'name': 'q', 'kind': 'oscillator', 'dimension': 3}], **model})


def product(factors):
    return {'kind': 'product', 'factors': factors}


def staged(req):
    dimension = req._physical_checks()
    plan = AllocationPlan('synthetic-test-only', 'static-numerics-test',
        {'hilbert': dimension}, (), 0, {}, req._result_plan(dimension))
    return _build_static_model(req, plan)


@pytest.mark.parametrize('primitive,expected', [
    ('a', [[0, 1, 0], [0, 0, math.sqrt(2)], [0, 0, 0]]),
    ('adag', [[0, 0, 0], [1, 0, 0], [0, math.sqrt(2), 0]]),
    ('n', np.diag([0, 1, 2])), ('identity', np.eye(3)),
    ('x', [[0, 1, 0], [1, 0, math.sqrt(2)], [0, math.sqrt(2), 0]]),
    ('y', [[0, -1j, 0], [1j, 0, -1j*math.sqrt(2)], [0, 1j*math.sqrt(2), 0]]),
    ('proj:2,0', [[0, 0, 0], [0, 0, 0], [1, 0, 0]]),
])
def test_native_primitive_explicit_reference(primitive, expected):
    req = request(hamiltonian=[{'operator': product({'q': primitive}), 'hc': True}])
    term = req.model.hamiltonian[0]
    actual = _operator(req, term.operator)
    np.testing.assert_allclose(actual.full(), expected, atol=1e-15)
    assert actual.dims == [[3], [3]]


def test_ordered_tensor_with_omitted_identity_and_permutation():
    modes = [{'name': 'left', 'kind': 'oscillator', 'dimension': 2},
             {'name': 'right', 'kind': 'oscillator', 'dimension': 3}]
    term = {'operator': product({'right': 'proj:2,1'}), 'hc': True}
    req = request(modes, hamiltonian=[term])
    actual = _operator(req, req.model.hamiltonian[0].operator)
    local = np.zeros((3, 3)); local[2, 1] = 1
    np.testing.assert_array_equal(actual.full(), np.kron(np.eye(2), local))
    assert actual.dims == [[2, 3], [2, 3]]
    swapped = request(list(reversed(modes)), hamiltonian=[term])
    permuted = _operator(swapped, swapped.model.hamiltonian[0].operator)
    np.testing.assert_array_equal(permuted.full(), np.kron(local, np.eye(2)))
    assert not np.array_equal(actual.full(), permuted.full())


def test_multimode_static_products_and_complex_hc_conjugate_scalar():
    modes = [{'name': 'a', 'kind': 'oscillator', 'dimension': 2, 'frequency': 1.25},
             {'name': 'b', 'kind': 'oscillator', 'dimension': 2, 'frequency': .75}]
    req = request(modes, hamiltonian=[{'operator': product({'a': 'adag', 'b': 'a'}),
        'coefficient': {'re': .2, 'im': .3}, 'hc': True}])
    native = staged(req)
    a = np.array([[0, 1], [0, 0]], dtype=complex)
    operator = np.kron(a.conj().T, a)
    expected = np.diag([0, .75, 1.25, 2]) + (.2+.3j)*operator + (.2-.3j)*operator.conj().T
    np.testing.assert_allclose(native.hamiltonian.full(), 2*math.pi*expected, atol=1e-15)
    assert isinstance(native, NativeLindbladModel)
    assert native.tensor_order == ('a', 'b')
    assert native.collapse_operators == () and native.coefficient_segments == ()
    assert native.initial_state is None
    assert native.allocation_plan.policy_revision == 'synthetic-test-only'


def test_duffing_kerr_matches_explicit_matrix_mode_and_static_spectrum():
    req = request([{'name': 'q', 'kind': 'duffing', 'dimension': 4, 'frequency': 5, 'kerr': -.2}])
    expected = [0, 5, 9.8, 14.4]
    numeric = request([{'name': 'q', 'kind': 'matrix', 'dimension': 4,
                        'hamiltonian': np.diag(expected).tolist()}])
    native = staged(req)
    np.testing.assert_allclose(native.hamiltonian.full(), staged(numeric).hamiltonian.full(), atol=1e-14)
    np.testing.assert_allclose(native.hamiltonian.eigenenergies()/(2*math.pi), expected, atol=1e-14)


def test_custom_full_matrix_retains_tensor_dims_and_complex_entries():
    modes = [{'name': name, 'kind': 'oscillator', 'dimension': 2} for name in ('a', 'b')]
    matrix = [[0]*4 for _ in range(4)]
    matrix[0][3] = {'re': 1, 'im': 2}
    req = request(modes, matrices={'coupling': matrix}, hamiltonian=[{
        'operator': {'kind': 'matrix', 'name': 'coupling'}, 'coefficient': {'im': 1}, 'hc': True}])
    result = staged(req).hamiltonian
    expected = np.zeros((4, 4), dtype=complex); expected[0, 3] = -2+1j; expected[3, 0] = -2-1j
    np.testing.assert_allclose(result.full(), 2*math.pi*expected)
    assert result.dims == [[2, 2], [2, 2]]


def test_dimension_one_identity_and_zero_hamiltonian():
    native = staged(request([{'name': 'one', 'kind': 'oscillator', 'dimension': 1}]))
    np.testing.assert_array_equal(native.hamiltonian.full(), [[0]])
    assert native.hamiltonian.shape == (1, 1)


@pytest.mark.parametrize('change', ['shape', 'nonhermitian', 'dimension', 'nan'])
def test_native_boundary_rechecks_mutated_request(change):
    req = request([{'name': 'q', 'kind': 'matrix', 'dimension': 2, 'hamiltonian': [[0, 0], [0, 1]]}])
    plan = staged(req).allocation_plan
    if change == 'shape':
        req.model.modes[0].hamiltonian.pop()
    elif change == 'nonhermitian':
        req.model.modes[0].hamiltonian[0][1].re = 1
    elif change == 'dimension':
        req.model.modes[0].dimension = 257
    else:
        req.model.modes[0].hamiltonian[0][0].re = float('nan')
    with pytest.raises(ValueError):
        _build_static_model(req, plan)


def test_public_builder_cannot_use_synthetic_certificate():
    req = request()
    with pytest.raises(ValueError, match='native allocation certificate is unavailable'):
        build_lindblad_model(req, staged(req).allocation_plan)


def test_model_metadata_imports_do_not_load_qutip():
    script = 'import sys; import qsim.lindblad_models; import qsim.lindblad_model; print("qutip" in sys.modules)'
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'False'


@pytest.mark.parametrize('component', ['drives', 'collapse', 'initial_state'])
def test_future_dynamic_pieces_are_explicitly_refused(component):
    body = {'schema_version': 1, 'operation': 'evolve', 'solver': 'me', 'times': [0, 1],
            'model': {'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': 2}]}}
    if component == 'drives':
        body['model']['drives'] = [{'operator': product({'q': 'n'}), 'envelope': {'shape': 'constant'}}]
    elif component == 'collapse':
        body['model']['collapse'] = [{'kind': 'operator', 'operator': product({'q': 'a'}), 'rate': 1}]
    else:
        body['initial_state'] = {'kind': 'product', 'modes': {'q': {'kind': 'fock', 'index': 1}}}
    req = LindbladRequest(**body)
    plan = AllocationPlan('synthetic-test-only', 'static-numerics-test', {}, (), 0, {}, req._result_plan(2))
    with pytest.raises(ValueError, match='static construction does not support'):
        _build_static_model(req, plan)


def test_native_boundary_checks_constructed_hermiticity(monkeypatch):
    import qutip as qt
    import qsim.lindblad_model as module

    req = request([{'name': 'q', 'kind': 'oscillator', 'dimension': 2}],
                  hamiltonian=[{'operator': product({'q': 'n'})}])
    plan = staged(req).allocation_plan
    monkeypatch.setattr(module, '_operator', lambda *_: qt.Qobj([[0, 1], [0, 0]]))
    with pytest.raises(ValueError, match='finite and Hermitian'):
        _build_static_model(req, plan)


def test_stale_result_reservation_refused_before_native_construction():
    from dataclasses import replace
    from qsim.physics_results import plan_result

    req = request()
    plan = replace(staged(req).allocation_plan,
                   result=plan_result((), metadata_characters=0, summary_bytes=0))
    with pytest.raises(ValueError, match='result reservation differs'):
        _build_static_model(req, plan)


def test_unit_dimension_mode_preserves_order_with_nontrivial_mode():
    native = staged(request([{'name': 'one', 'kind': 'oscillator', 'dimension': 1},
                              {'name': 'q', 'kind': 'oscillator', 'dimension': 2, 'frequency': 2}]))
    np.testing.assert_allclose(native.hamiltonian.full(), np.diag([0, 4*math.pi]))
    assert native.tensor_order == ('one', 'q')
    assert native.hamiltonian.dims == [[1, 2], [1, 2]]


@pytest.mark.parametrize('kind', ['oscillator', 'duffing', 'coupling', 'matrix'])
def test_tiny_finite_coefficients_survive_sparse_cleanup(kind):
    epsilon = 1e-20
    if kind == 'coupling':
        req = request(hamiltonian=[{'operator': product({'q': 'a'}),
                                  'coefficient': {'re': epsilon, 'im': 2*epsilon}, 'hc': True}])
        a = np.array([[0, 1, 0], [0, 0, math.sqrt(2)], [0, 0, 0]])
        expected = (1+2j)*a + (1-2j)*a.conj().T
    elif kind == 'matrix':
        req = request([{'name': 'q', 'kind': 'matrix', 'dimension': 3,
                        'hamiltonian': np.diag([0, epsilon, 2*epsilon]).tolist()}])
        expected = np.diag([0, 1, 2])
    else:
        mode = {'name': 'q', 'kind': kind, 'dimension': 3, 'frequency': epsilon}
        if kind == 'duffing':
            mode['kerr'] = -epsilon/4
        req = request([mode])
        expected = np.diag([0, 1, 1.75 if kind == 'duffing' else 2])
    np.testing.assert_allclose(staged(req).hamiltonian.full()/(2*math.pi*epsilon),
                               expected, atol=0, rtol=2e-15)


def test_native_construction_restores_qutip_settings_on_failure(monkeypatch):
    import qutip as qt
    import qsim.lindblad_model as module

    req = request(hamiltonian=[{'operator': product({'q': 'n'})}])
    plan = staged(req).allocation_plan
    before = qt.settings.core['auto_tidyup']
    def fail(*args):
        raise RuntimeError('synthetic construction failure')
    monkeypatch.setattr(module, '_operator', fail)
    with pytest.raises(RuntimeError, match='synthetic construction failure'):
        _build_static_model(req, plan)
    assert qt.settings.core['auto_tidyup'] == before


@pytest.mark.parametrize('initial_tidyup', [True, False])
def test_native_construction_scopes_and_restores_qutip_settings(initial_tidyup, monkeypatch):
    import qutip as qt
    import qsim.lindblad_model as module

    req = request(hamiltonian=[{'operator': product({'q': 'n'})}])
    original = module._operator
    observed = []
    def observed_operator(*args):
        observed.append(qt.settings.core['auto_tidyup'])
        return original(*args)
    monkeypatch.setattr(module, '_operator', observed_operator)
    with qt.CoreOptions(auto_tidyup=initial_tidyup):
        settings_before = qt.settings.core
        staged(req)
        assert qt.settings.core is settings_before
        assert qt.settings.core['auto_tidyup'] is initial_tidyup
    assert observed == [False]


def test_tiny_coupling_is_retained_beside_order_one_diagonal():
    epsilon = 1e-20
    req = request([{'name': 'q', 'kind': 'oscillator', 'dimension': 2, 'frequency': 1}],
                  hamiltonian=[{'operator': product({'q': 'a'}), 'coefficient': epsilon, 'hc': True}])
    matrix = staged(req).hamiltonian.full()
    np.testing.assert_allclose([matrix[0, 1]/(2*math.pi*epsilon), matrix[1, 0]/(2*math.pi*epsilon)],
                               [1, 1], atol=0, rtol=2e-15)
    assert matrix[1, 1] == 2*math.pi
