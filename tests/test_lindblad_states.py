"""Native state/channel numerics; synthetic plans never certify admission."""
import math

import numpy as np
import pytest

from qsim import lindblad_model as native
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import AllocationPlan


def request(*, solver='me', state=None, collapse=None, modes=None, operation='evolve'):
    body = {'schema_version': 1, 'operation': operation,
            'model': {'modes': modes or [{'name': 'q', 'kind': 'oscillator', 'dimension': 2}]}}
    if operation in ('evolve', 'propagator'):
        body.update(solver=solver, times=[0, 1])
    if operation == 'spectrum':
        body['evals_count'] = 1
    if state is not None:
        body['initial_state'] = state
    if collapse is not None:
        body['model']['collapse'] = collapse
    return LindbladRequest(**body)


def plan_for(req):
    dimension = req._physical_checks()
    return AllocationPlan('synthetic-test-only', 'state-numerics-test',
                          {'hilbert': dimension}, (), 0, {}, req._result_plan(dimension))


def staged(req):
    return native._build_model(req, plan_for(req))


def factor(kind, **controls):
    return {'kind': 'product', 'modes': {'q': {'kind': kind, **controls}}}


def relaxation(mode='q', time=7):
    return {'kind': 'relaxation', 'mode': mode, 'T1': time}


@pytest.mark.parametrize('solver', ['se', 'me', 'mc'])
def test_vacuum_default_has_solver_representation(solver):
    req = request(solver=solver, collapse=[relaxation()] if solver == 'mc' else None)
    result = staged(req)
    state = result.initial_state
    np.testing.assert_array_equal(state.full(), [[1, 0], [0, 0]] if solver == 'me' else [[1], [0]])
    assert state.dims == [[2], [2]] if solver == 'me' else state.dims == [[2], [1]]
    assert result.tensor_order == ('q',)


@pytest.mark.parametrize('operation', ['spectrum', 'steadystate', 'propagator'])
def test_non_evolution_operations_have_no_initial_state(operation):
    assert staged(request(operation=operation)).initial_state is None


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_product_fock_order_and_missing_mode_vacuum(solver):
    modes = [{'name': 'left', 'kind': 'oscillator', 'dimension': 2},
             {'name': 'right', 'kind': 'matrix', 'dimension': 3, 'hamiltonian': np.eye(3).tolist()}]
    state = {'kind': 'product', 'modes': {'right': {'kind': 'fock', 'index': 2}}}
    result = staged(request(solver=solver, state=state, modes=modes))
    vector = np.kron([1, 0], [0, 0, 1]).astype(complex)
    expected = np.outer(vector, vector.conj()) if solver == 'me' else vector[:, None]
    np.testing.assert_array_equal(result.initial_state.full(), expected)
    assert result.initial_state.dims == ([[2, 3], [2, 3]] if solver == 'me' else [[2, 3], [1]])


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_coherent_uses_normalized_native_finite_displacement(solver):
    import qutip as qt
    alpha = 1.2 + .3j
    req = request(solver=solver, state=factor('coherent', amplitude={'re': alpha.real, 'im': alpha.imag}))
    value = staged(req).initial_state
    expected = qt.coherent(2, alpha, method='operator')
    if solver == 'me':
        expected = qt.ket2dm(expected)
    np.testing.assert_allclose(value.full(), expected.full(), atol=1e-15, rtol=1e-15)
    assert abs(value.tr() - 1) < 1e-14 if solver == 'me' else abs(value.norm() - 1) < 1e-14


@pytest.mark.parametrize('occupation', [0., .75, 1e-320, 1e308, float(np.finfo(float).max)])
def test_generated_thermal_state_is_normalized_and_finite(occupation):
    req = request(state=factor('thermal', occupation=occupation),
                  modes=[{'name': 'q', 'kind': 'oscillator', 'dimension': 5}])
    value = staged(req).initial_state.full()
    ratio = occupation / (1 + occupation)
    weights = np.array([ratio**i for i in range(5)])
    expected = np.diag(weights / weights.sum())
    np.testing.assert_allclose(value, expected, atol=0, rtol=2e-14)
    assert np.isfinite(value).all()
    assert abs(np.trace(value) - 1) < 1e-14


def test_mixed_product_thermal_and_pure_state_keeps_tensor_order():
    import qutip as qt
    modes = [{'name': 'heat', 'kind': 'oscillator', 'dimension': 3},
             {'name': 'cold', 'kind': 'oscillator', 'dimension': 2}]
    req = request(modes=modes, state={'kind': 'product', 'modes': {
        'cold': {'kind': 'fock', 'index': 1}, 'heat': {'kind': 'thermal', 'occupation': .8}}})
    expected = qt.tensor(qt.thermal_dm(3, .8), qt.fock_dm(2, 1))
    np.testing.assert_allclose(staged(req).initial_state.full(), expected.full(), atol=1e-15)


@pytest.mark.parametrize('solver', ['se', 'mc'])
@pytest.mark.parametrize('state', [factor('thermal', occupation=.5),
    {'kind': 'density', 'values': [[.5, 0], [0, .5]]}])
def test_pure_state_solvers_reject_mixed_inputs(solver, state):
    req = request(solver=solver, state=state, collapse=[relaxation()] if solver == 'mc' else None)
    with pytest.raises(ValueError, match='pure-state solver rejects mixed state'):
        staged(req)


@pytest.mark.parametrize('solver', ['se', 'mc'])
@pytest.mark.parametrize('occupation,dimension', [(0., 2), (2., 1)])
def test_pure_thermal_boundaries_can_be_native_kets(solver, occupation, dimension):
    modes = [{'name': 'one', 'kind': 'oscillator', 'dimension': 2},
             {'name': 'q', 'kind': 'oscillator', 'dimension': dimension}]
    req = request(solver=solver, state=factor('thermal', occupation=occupation), modes=modes,
                  collapse=[relaxation('one')] if solver == 'mc' else None)
    value = staged(req).initial_state
    assert value.isket
    assert abs(value.norm() - 1) < 1e-15
    assert value.dims == [[2, dimension], [1]]


@pytest.mark.parametrize('values', [[{'re': 3}, {'im': 4}],
                                  [{'re': 1e308, 'im': 1e308}, {'re': 1e308}],
                                  [1e-320, 0.]])
@pytest.mark.parametrize('solver', ['se', 'me'])
def test_amplitudes_normalize_without_overflow_or_underflow(values, solver):
    req = request(solver=solver, state={'kind': 'amplitudes', 'values': values})
    before = req.canonical_envelope()
    value = staged(req).initial_state.full()
    assert np.isfinite(value).all()
    assert abs(np.trace(value) - 1) < 1e-14 if solver == 'me' else abs(np.linalg.norm(value) - 1) < 1e-14
    assert req.canonical_envelope() == before
    if values == [1e-320, 0.]:
        np.testing.assert_array_equal(value, [[1, 0], [0, 0]] if solver == 'me' else [[1], [0]])


@pytest.mark.parametrize('density', [[[.25, 0], [0, .75]],
                                   [[1+5e-11, 0], [0, -5e-11]]])
def test_supplied_density_is_not_clipped_or_renormalized(density):
    req = request(state={'kind': 'density', 'values': density})
    before = req.canonical_envelope()
    np.testing.assert_array_equal(staged(req).initial_state.full(), density)
    assert req.canonical_envelope() == before


@pytest.mark.parametrize('solver', ['se', 'mc'])
def test_pure_density_recovers_ket_without_changing_supplied_density(solver):
    expected = np.array([[.5, -.5j], [.5j, .5]])
    density = [[.5, {'im': -.5}], [{'im': .5}, .5]]
    req = request(solver=solver, state={'kind': 'density', 'values': density},
                  collapse=[relaxation()] if solver == 'mc' else None)
    before = req.canonical_envelope()
    ket = staged(req).initial_state
    assert ket.isket
    np.testing.assert_allclose((ket * ket.dag()).full(), expected, atol=2e-15)
    assert req.canonical_envelope() == before


@pytest.mark.parametrize('kind,control,multiplier', [('relaxation', 'T1', 1), ('dephasing', 'Tphi', 2)])
def test_decay_scaling_uses_inverse_nanoseconds_without_2pi(kind, control, multiplier):
    req = request(collapse=[{'kind': kind, 'mode': 'q', control: 7}])
    operator = staged(req).collapse_operators[0].full()
    primitive = np.array([[0, 1], [0, 0]]) if kind == 'relaxation' else np.diag([0, 1])
    np.testing.assert_allclose(operator, math.sqrt(multiplier/7)*primitive, atol=0, rtol=1e-15)


@pytest.mark.parametrize('occupation', [0., .5])
def test_thermal_channels_keep_downward_upward_order_and_zero_channel(occupation):
    req = request(collapse=[{'kind': 'thermal', 'mode': 'q', 'rate': .4, 'occupation': occupation}])
    channels = staged(req).collapse_operators
    assert isinstance(channels, tuple) and len(channels) == 2
    a = np.array([[0, 1], [0, 0]])
    np.testing.assert_allclose(channels[0].full(), math.sqrt(.4*(occupation+1))*a, atol=0, rtol=1e-15)
    np.testing.assert_allclose(channels[1].full(), math.sqrt(.4*occupation)*a.T, atol=0, rtol=1e-15)


def test_general_collapse_product_and_named_complex_matrix():
    modes = [{'name': 'left', 'kind': 'oscillator', 'dimension': 2},
             {'name': 'right', 'kind': 'oscillator', 'dimension': 2}]
    req = request(modes=modes, collapse=[{'kind': 'operator', 'operator': {
        'kind': 'product', 'factors': {'right': 'a'}}, 'rate': .2}])
    # Construct the full-matrix variant through strict schema validation.
    body = req.canonical_envelope()
    body['model']['matrices'] = {'custom': [[0, {'im': 1}, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]}
    body['model']['collapse'].append({'kind': 'operator', 'operator': {'kind': 'matrix', 'name': 'custom'}, 'rate': .3})
    req = LindbladRequest(**body)
    channels = staged(req).collapse_operators
    np.testing.assert_allclose(channels[0].full(), math.sqrt(.2)*np.kron(np.eye(2), [[0, 1], [0, 0]]))
    expected = np.zeros((4, 4), dtype=complex); expected[0, 1] = 1j*math.sqrt(.3)
    np.testing.assert_array_equal(channels[1].full(), expected)
    assert channels[0].dims == channels[1].dims == [[2, 2], [2, 2]]


@pytest.mark.parametrize('kind', ['relaxation', 'dephasing', 'thermal'])
def test_dimension_one_collapse_is_a_valid_zero_operator(kind):
    controls = {'T1': 1} if kind == 'relaxation' else {'Tphi': 1} if kind == 'dephasing' else {'rate': 1, 'occupation': 2}
    req = request(modes=[{'name': 'q', 'kind': 'oscillator', 'dimension': 1}],
                  collapse=[{'kind': kind, 'mode': 'q', **controls}])
    assert all(np.array_equal(value.full(), [[0]]) for value in staged(req).collapse_operators)


@pytest.mark.parametrize('solver,collapse', [('se', [relaxation()]), ('mc', []),
    ('mc', [{'kind': 'operator', 'operator': {'kind': 'product', 'factors': {'q': 'a'}}, 'rate': 0}])])
def test_solver_dissipation_rejections_precede_native_construction(solver, collapse):
    with pytest.raises(ValueError, match='se rejects dissipation|mc requires a nonzero'):
        staged(request(solver=solver, collapse=collapse))


@pytest.mark.parametrize('kind', ['coherent', 'amplitudes', 'collapse'])
def test_tiny_finite_state_and_channel_terms_survive(kind):
    epsilon = 1e-20
    if kind == 'coherent':
        req = request(solver='se', state=factor('coherent', amplitude=epsilon))
        actual = staged(req).initial_state.full()[1, 0]
        assert actual/epsilon == pytest.approx(1, rel=1e-14, abs=0)
    elif kind == 'amplitudes':
        req = request(solver='se', state={'kind': 'amplitudes', 'values': [1, epsilon]})
        assert staged(req).initial_state.full()[1, 0] == epsilon
    else:
        req = request(collapse=[{'kind': 'operator', 'operator': {'kind': 'product', 'factors': {'q': 'a'}}, 'rate': epsilon**2}])
        assert staged(req).collapse_operators[0].full()[0, 1]/epsilon == pytest.approx(1, rel=1e-14, abs=0)


@pytest.mark.parametrize('change', ['zero', 'nan', 'density', 'fock'])
def test_mutated_invalid_state_is_rechecked(change):
    state = {'kind': 'amplitudes', 'values': [1, 0]} if change in ('zero', 'nan') else (
        {'kind': 'density', 'values': [[1, 0], [0, 0]]} if change == 'density' else factor('fock', index=0))
    req = request(state=state); plan = plan_for(req)
    if change == 'zero':
        req.initial_state.values[0].re = 0
    elif change == 'nan':
        req.initial_state.values[0].re = float('nan')
    elif change == 'density':
        req.initial_state.values[1][1].re = -.2
    else:
        req.initial_state.modes['q'].index = 2
    with pytest.raises(ValueError):
        native._build_model(req, plan)


def test_future_drives_still_refused_and_public_builder_stays_gated():
    req = request()
    body = req.canonical_envelope()
    body['model']['drives'] = [{'operator': {'kind': 'product', 'factors': {'q': 'n'}}, 'envelope': {'shape': 'constant'}}]
    req = LindbladRequest(**body)
    with pytest.raises(ValueError, match='drives'):
        native._build_model(req, plan_for(req))
    req = request(state=factor('fock', index=1), collapse=[relaxation()])
    with pytest.raises(ValueError, match='native allocation certificate is unavailable'):
        native.build_lindblad_model(req, plan_for(req))


@pytest.mark.parametrize('kind', ['relaxation', 'dephasing'])
def test_native_channels_reproduce_two_level_decay_rates(kind):
    import qutip as qt
    if kind == 'relaxation':
        req = request(state=factor('fock', index=1), collapse=[relaxation(time=7)])
    else:
        req = request(state={'kind': 'amplitudes', 'values': [1, 1]},
                      collapse=[{'kind': 'dephasing', 'mode': 'q', 'Tphi': 7}])
    model = staged(req)
    times = np.linspace(0, 14, 31)
    result = qt.mesolve(model.hamiltonian, model.initial_state, times,
                        model.collapse_operators, options={'method': 'vern7', 'atol': 1e-12, 'rtol': 1e-11})
    actual = np.array([value.full()[1, 1].real if kind == 'relaxation' else value.full()[0, 1].real for value in result.states])
    expected = np.exp(-times/7) * (1 if kind == 'relaxation' else .5)
    np.testing.assert_allclose(actual, expected, atol=1e-10, rtol=1e-9)


def test_qutip_options_restore_after_state_construction_and_failure(monkeypatch):
    import qutip as qt
    req = request(state=factor('coherent', amplitude=.2))
    with qt.CoreOptions(auto_tidyup=True):
        original = qt.settings.core
        staged(req)
        assert qt.settings.core is original
        def fail(*args, **kwargs):
            assert qt.settings.core['auto_tidyup'] is False
            raise RuntimeError('synthetic native state failure')
        monkeypatch.setattr(qt, 'coherent', fail)
        with pytest.raises(RuntimeError, match='synthetic native state failure'):
            staged(req)
        assert qt.settings.core is original


@pytest.mark.parametrize('solver,dimension', [('se', 256), ('me', 32), ('mc', 256)])
def test_largest_admitted_state_shapes(solver, dimension):
    req = request(solver=solver, modes=[{'name': 'q', 'kind': 'oscillator', 'dimension': dimension}],
                  state={'kind': 'amplitudes', 'values': [1]+[0]*(dimension-1)},
                  collapse=[relaxation()] if solver == 'mc' else None)
    value = staged(req).initial_state
    assert value.shape == ((dimension, dimension) if solver == 'me' else (dimension, 1))
    assert np.isfinite(value.full()).all()


@pytest.mark.parametrize('occupation', [0., 1e-320, float(np.finfo(float).max)])
def test_one_dimensional_thermal_state_is_vacuum(occupation):
    req = request(state=factor('thermal', occupation=occupation),
                  modes=[{'name': 'q', 'kind': 'oscillator', 'dimension': 1}])
    value = staged(req).initial_state
    np.testing.assert_array_equal(value.full(), [[1]])
    assert value.tr() == 1


def test_two_level_coherent_state_matches_explicit_finite_displacement():
    alpha = 1.2 + .3j
    value = staged(request(solver='se', state=factor('coherent', amplitude={'re': alpha.real, 'im': alpha.imag}))).initial_state
    expected = [[math.cos(abs(alpha))], [alpha/abs(alpha)*math.sin(abs(alpha))]]
    np.testing.assert_allclose(value.full(), expected, rtol=2e-15, atol=2e-15)


def test_largest_explicit_pure_density_recovers_ordered_ket():
    size = 256
    values = [[0.0]*size for _ in range(size)]
    values[-1][-1] = 1.0
    req = request(solver='se', state={'kind': 'density', 'values': values},
                  modes=[{'name': name, 'kind': 'oscillator', 'dimension': 2}
                         for name in ('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h')])
    before = req.canonical_envelope()
    value = staged(req).initial_state
    expected = np.zeros((size, 1)); expected[-1, 0] = 1
    np.testing.assert_array_equal(value.full(), expected)
    assert value.dims == [[2]*8, [1]]
    assert req.canonical_envelope() == before


@pytest.mark.parametrize('solver', ['se', 'me', 'mc'])
def test_dimension_one_coherent_factor_is_native_vacuum(solver):
    modes = [{'name': 'one', 'kind': 'oscillator', 'dimension': 2},
             {'name': 'q', 'kind': 'oscillator', 'dimension': 1}]
    req = request(solver=solver, modes=modes, state=factor('coherent', amplitude=.7),
                  collapse=[relaxation('one')] if solver == 'mc' else None)
    value = staged(req).initial_state
    expected = [[1, 0], [0, 0]] if solver == 'me' else [[1], [0]]
    np.testing.assert_array_equal(value.full(), expected)


@pytest.mark.parametrize('kind,dimension', [('coherent', 256), ('thermal', 32)])
def test_largest_generated_state_shapes(kind, dimension):
    state = factor('coherent', amplitude=.7) if kind == 'coherent' else factor('thermal', occupation=1.)
    req = request(solver='se' if kind == 'coherent' else 'me', state=state,
                  modes=[{'name': 'q', 'kind': 'oscillator', 'dimension': dimension}])
    value = staged(req).initial_state
    assert np.isfinite(value.full()).all()
    assert abs((value.norm() if kind == 'coherent' else value.tr()) - 1) < 1e-12
    if kind == 'thermal':
        assert np.linalg.eigvalsh(value.full()).min() >= 0
    assert value.shape == ((dimension, 1) if kind == 'coherent' else (dimension, dimension))
