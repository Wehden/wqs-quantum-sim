"""Numerical drive construction only; synthetic plans do not certify admission."""
import math

import numpy as np
import pytest
from scipy.integrate import quad

from qsim import lindblad_model as native
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import AllocationPlan


def request(envelope, *, times=None, amplitude=.03, detuning=0., phase=0., hc=False, solver='se'):
    return LindbladRequest(schema_version=1, operation='evolve', solver=solver,
        times=times or [0., 10.], store_states='final', model={
            'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': 2}],
            'drives': [{'operator': {'kind': 'product', 'factors': {'q': 'a' if hc else 'x'}},
                        'amplitude': amplitude, 'hc': hc, 'envelope': envelope,
                        'detuning': detuning, 'phase': phase}]})


def staged(req):
    dimension = req._physical_checks()
    plan = AllocationPlan('synthetic-test-only', 'drive-numerics-test',
        {'hilbert': dimension}, (), 0, {}, req._result_plan(dimension))
    return native._build_model(req, plan)


def propagated(segments):
    import qutip as qt
    state = qt.basis(2, 0)
    for segment in segments:
        state = qt.sesolve(segment['hamiltonian'], state,
            [segment['start'], segment['stop']], options={
                'method': 'vern7', 'atol': 1e-10, 'rtol': 1e-9, 'nsteps': 100000,
                'max_step': segment['max_step']}).states[-1]
    return state.full().ravel()


def test_square_entirely_between_output_times_has_exact_one_sided_edges():
    segments = staged(request({'shape': 'square', 'start': 40., 'stop': 42.},
                             times=[0., 100.])).coefficient_segments
    assert [(s['start'], s['stop']) for s in segments] == [(0, 40), (40, 42), (42, 100)]
    for segment, expected in zip(segments, (0, 1, 0)):
        np.testing.assert_array_equal(segment['coefficients'][0], expected)
        assert segment['coefficient_error_bound'] == 0
    expected = np.array([math.cos(2*math.pi*.03*2), -1j*math.sin(2*math.pi*.03*2)])
    np.testing.assert_allclose(propagated(segments), expected, atol=2e-9)
    assert segments[0]['max_step'] == 20 and segments[1]['max_step'] == 1


def test_constant_amplitude_has_single_angular_conversion():
    value = staged(request({'shape': 'constant'}, amplitude=.125)).coefficient_segments[0]
    np.testing.assert_allclose(value['hamiltonian'](3).full(),
                              2*math.pi*.125*np.array([[0, 1], [1, 0]]), atol=1e-15)


def test_complex_modulation_uses_original_time_and_conjugates_both_factors():
    req = request({'shape': 'square', 'start': 2., 'stop': 4.},
                  amplitude={'re': .02, 'im': -.01}, detuning=.17, phase=.4, hc=True)
    segment = staged(req).coefficient_segments[1]
    for index in (0, len(segment['times'])//2, -1):
        time = segment['times'][index]
        scalar = 2*math.pi*(.02-.01j)*np.exp(1j*(2*math.pi*.17*time+.4))
        expected = np.array([[0, scalar], [scalar.conjugate(), 0]])
        np.testing.assert_allclose(segment['hamiltonian'](time).full(), expected, atol=1e-15)
    np.testing.assert_array_equal(segment['coefficients'][1], segment['coefficients'][0].conj())


def test_sampled_linear_knots_and_zero_extrapolation_are_explicit():
    envelope = {'shape': 'sampled', 'times': [2., 3., 7.],
                'values': [1., {'im': 1.}, 2.]}
    segments = staged(request(envelope, hc=True, times=[0., 2.5, 10.])).coefficient_segments
    assert [(s['start'], s['stop']) for s in segments] == [(0, 2), (2, 3), (3, 7), (7, 10)]
    np.testing.assert_array_equal(segments[0]['coefficients'][0], 0)
    np.testing.assert_array_equal(segments[-1]['coefficients'][0], 0)
    assert 2.5 in segments[1]['times']
    assert segments[1]['coefficients'][0][0] == 1
    assert segments[1]['coefficients'][0][-1] == 1j
    assert segments[2]['coefficients'][0][-1] == 2
    np.testing.assert_allclose(segments[1]['hamiltonian'](2.5).full()[0, 1],
                              2*math.pi*.03*(.5+.5j), atol=1e-15)


@pytest.mark.parametrize('shape,controls', [
    ('gaussian', {'center': 3.6, 'sigma': .41}),
    ('flattop_cosine', {'rise': .6}),
])
def test_smooth_pulses_certify_generator_error_and_converge_under_grid_doubling(shape, controls):
    import qutip as qt
    envelope = {'shape': shape, 'start': 2., 'stop': 6., **controls}
    req = request(envelope, times=[0., 10.])
    ordinary = staged(req).coefficient_segments
    refined = native._coefficient_segments(req, qt.qzero(2), refinement=2)
    certificate = sum(s['coefficient_error_bound'] for s in ordinary)
    assert 0 < certificate <= 1e-6
    interpolation = sum(s['coefficient_interpolation_bound'] for s in ordinary)
    assert sum(s['coefficient_interpolation_bound'] for s in refined) <= interpolation/4*(1+1e-10)
    if shape == 'gaussian':
        analytic = lambda t: math.exp(-.5*((t-controls['center'])/controls['sigma'])**2)
    else:
        def analytic(t):
            distance = min(t-2, 6-t)
            return (1-math.cos(math.pi*distance/controls['rise']))/2 if distance < controls['rise'] else 1
    angle = 2*math.pi*.03*quad(analytic, 2, 6, points=[2+controls.get('rise', 0), 6-controls.get('rise', 0)],
                               epsabs=1e-12)[0]
    exact = np.array([math.cos(angle), -1j*math.sin(angle)])
    error = np.linalg.norm(propagated(ordinary)-exact)
    refined_error = np.linalg.norm(propagated(refined)-exact)
    assert error <= certificate+2e-9
    assert refined_error < error*.4
    for segment in ordinary:
        assert np.all(np.diff(segment['times']) > 0)
        assert segment['times'][0] == segment['start'] and segment['times'][-1] == segment['stop']
        assert segment['max_step'] > 0


def test_truncated_gaussian_nonzero_endpoints_never_ramp_across_empty_support():
    segments = staged(request({'shape': 'gaussian', 'start': 2., 'stop': 4.,
                               'center': 3., 'sigma': 2.})).coefficient_segments
    assert len(segments) == 3
    assert segments[0]['coefficients'][0][-1] == 0
    assert segments[1]['coefficients'][0][0] == pytest.approx(math.exp(-.125))
    assert segments[1]['coefficients'][0][-1] == pytest.approx(math.exp(-.125))
    assert segments[2]['coefficients'][0][0] == 0


def test_me_certificate_counts_commutator_factor_two():
    envelope = {'shape': 'constant'}
    se = staged(request(envelope, detuning=.25, hc=True)).coefficient_segments
    me = staged(request(envelope, detuning=.25, hc=True, solver='me')).coefficient_segments
    assert len(me[0]['times']) > len(se[0]['times'])
    assert sum(s['coefficient_error_bound'] for s in me) <= 1e-6


@pytest.mark.parametrize('envelope,detuning', [
    ({'shape': 'constant'}, 1e8),
    ({'shape': 'gaussian', 'start': 2., 'stop': 4., 'center': 3., 'sigma': 1e-200}, 0),
])
def test_unresolved_grid_refused_before_numeric_grid_allocation(monkeypatch, envelope, detuning):
    import qutip as qt
    req = request(envelope, hc=True, detuning=detuning)
    req._physical_checks()
    def forbidden(*args, **kwargs):
        raise AssertionError('numeric grid allocated before refusal')
    monkeypatch.setattr(native.np, 'linspace', forbidden)
    with pytest.raises(ValueError, match='resource_limit: coefficient'):
        native._coefficient_segments(req, qt.qzero(2))


@pytest.mark.parametrize('envelope', [
    {'shape': 'drag', 'start': 1., 'stop': 2.},
    {'shape': 'square', 'start': 1., 'stop': 2., 'rise': .1},
])
def test_deferred_pulse_shapes_remain_refused(envelope):
    with pytest.raises(ValueError):
        request(envelope)


def test_native_public_admission_remains_fail_closed():
    req = request({'shape': 'constant'})
    dimension = req._physical_checks()
    plan = AllocationPlan('synthetic-test-only', 'drive-numerics-test',
        {'hilbert': dimension}, (), 0, {}, req._result_plan(dimension))
    with pytest.raises(ValueError, match='certificate'):
        native.build_lindblad_model(req, plan)


def test_unrepresentable_flattop_edge_is_refused_instead_of_erasing_pulse():
    req = request({'shape': 'flattop_cosine', 'start': 2., 'stop': 4., 'rise': 1e-320})
    with pytest.raises(ValueError, match='feature is not representable'):
        staged(req)


def test_full_output_grid_budget_includes_required_output_knots():
    req = request({'shape': 'constant'}, times=np.linspace(0., 10., 65536).tolist())
    segment = staged(req).coefficient_segments[0]
    assert len(segment['times']) == 65536
    np.testing.assert_array_equal(segment['times'], req.times)


def test_combined_segment_knots_refused_before_building_native_coefficients(monkeypatch):
    import qutip as qt
    req = request({'shape': 'square', 'start': 2., 'stop': 4.},
                  times=np.linspace(0., 10., 65536).tolist())
    req._physical_checks()
    def forbidden(*args, **kwargs):
        raise AssertionError('native coefficients allocated before complete knot count')
    monkeypatch.setattr(native, '_linear_coefficient', forbidden)
    with pytest.raises(ValueError, match='grid exceeds 65536'):
        native._coefficient_segments(req, qt.qzero(2))


def test_sixteen_scalar_channels_and_overflow_refusal_are_revalidated():
    req = request({'shape': 'constant'}, hc=True)
    body = req.canonical_envelope()
    body['model']['drives'] *= 8
    result = staged(LindbladRequest(**body))
    assert len(result.coefficient_segments[0]['coefficients']) == 16
    body['model']['drives'].append(body['model']['drives'][0])
    with pytest.raises(ValueError, match='16 scalar coefficient channels'):
        staged(LindbladRequest(**body))


def test_clipped_support_keeps_active_endpoint_limits_without_extra_segments():
    segment = staged(request({'shape': 'square', 'start': -2., 'stop': 20.})).coefficient_segments
    assert len(segment) == 1
    np.testing.assert_array_equal(segment[0]['coefficients'][0], 1)


def test_extreme_scales_cannot_underflow_to_false_zero_error_certificate():
    req = request({'shape': 'constant'}, amplitude=1e-200, detuning=1e-200,
                  times=[0., 1e200], hc=True)
    with pytest.raises(ValueError, match='resource_limit: coefficient'):
        staged(req)


def test_close_observation_knots_do_not_force_tiny_steps_across_empty_segment():
    segments = staged(request({'shape': 'square', 'start': 40., 'stop': 42.},
                             times=[0., 1e-12, 100.])).coefficient_segments
    assert segments[0]['max_step'] == 20
    expected = np.array([math.cos(2*math.pi*.03*2), -1j*math.sin(2*math.pi*.03*2)])
    np.testing.assert_allclose(propagated(segments), expected, atol=2e-9)


def test_absolute_time_rounding_cannot_coarsen_certified_grid():
    req = request({'shape': 'constant'}, times=[1e16, 1e16+100],
                  amplitude=.03, detuning=.0001, hc=True)
    with pytest.raises(ValueError, match='resource_limit: coefficient'):
        staged(req)


def test_large_phase_offset_keeps_carrier_in_independent_factored_reference():
    phase = 1e20
    segment = staged(request({'shape': 'constant'}, amplitude=.03, detuning=.1,
                             phase=phase, hc=True)).coefficient_segments[0]
    factor = complex(math.cos(phase), math.sin(phase))
    for time in (0., 2.5, 5., 7.5, 10.):
        carrier = complex(math.cos(2*math.pi*.1*time), math.sin(2*math.pi*.1*time))
        expected = 2*math.pi*.03*factor*carrier
        pointwise_bound = segment['coefficient_error_bound']/(segment['stop']-segment['start'])
        assert abs(segment['hamiltonian'](time).full()[0, 1]-expected) <= pointwise_bound


def test_large_absolute_carrier_argument_is_refused_without_precision_certificate():
    # Long absolute origin, short requested interval: the coefficient grid fits,
    # but forming omega*t has uncertainty far above the generator error budget.
    req = request({'shape': 'constant'}, times=[1e12, 1e12+1],
                  amplitude=.03, detuning=.1, hc=True)
    with pytest.raises(ValueError, match='resource_limit: coefficient'):
        staged(req)


def test_clipped_sampled_roundoff_cannot_claim_zero_error_at_large_coupling():
    from fractions import Fraction
    exact = Fraction(4, 3)
    rounded = Fraction.from_float(1.3333333333333335)
    assert (rounded-exact)*10**12/2 > Fraction(1, 10**6)
    req = request({'shape': 'sampled', 'times': [0., 3.], 'values': [1., 2.]},
                  times=[1., 1.5], amplitude=1e12)
    with pytest.raises(ValueError, match='resource_limit: coefficient'):
        staged(req)


@pytest.mark.parametrize('values,times,amplitude', [
    ([1., 2.], [1., 1.5], .03),
    ([5e-324, 1e-323], [1., 1.5], .03),
    ([1e12, -1e12], [1.499999999999, 1.5], 1e-12),
])
def test_sampled_normal_subnormal_and_cancellation_roundoff_has_exact_reference(values, times, amplitude):
    from fractions import Fraction
    envelope = {'shape': 'sampled', 'times': [0., 3.], 'values': values}
    segments = staged(request(envelope, times=times, amplitude=amplitude)).coefficient_segments
    assert sum(s['coefficient_error_bound'] for s in segments) > 0
    for segment in segments:
        bound = segment['coefficient_error_bound']
        strength = 2*math.pi*amplitude*math.sqrt(2)
        pointwise = bound/((segment['stop']-segment['start'])*strength)
        for time, actual in zip(segment['times'], segment['coefficients'][0]):
            weight = Fraction.from_float(float(time))/3
            exact = Fraction.from_float(values[0])*(1-weight)+Fraction.from_float(values[1])*weight
            error = abs(Fraction.from_float(float(actual.real))-exact)
            assert error <= Fraction.from_float(float(pointwise))


def test_native_linear_interpolation_uses_actual_nearly_uniform_knots():
    from fractions import Fraction
    times = np.array([0., 1e-9, 2.1e-9, 3e-9])
    values = np.array([0., 1., 1., 0.], dtype=complex)
    coefficient, bound = native._linear_coefficient(times, values)
    time = 2.05e-9
    exact = Fraction(1)
    error = abs(Fraction.from_float(float(coefficient(time).real))-exact)
    assert error <= Fraction.from_float(bound)


@pytest.mark.parametrize('shape,controls', [
    ('gaussian', {'center': 3., 'sigma': 2.}),
    ('flattop_cosine', {'rise': 1.}),
])
def test_analytic_envelope_roundoff_is_charged_against_high_precision_reference(shape, controls):
    from decimal import Decimal, localcontext
    envelope = {'shape': shape, 'start': 2., 'stop': 6., **controls}
    segments = staged(request(envelope)).coefficient_segments
    with localcontext() as context:
        context.prec = 80
        pi = Decimal('3.141592653589793238462643383279502884197169399375105820974944592307816406286209')
        for segment in segments:
            if segment['start'] < 2 or segment['stop'] > 6:
                continue
            assert segment['coefficient_roundoff_bound'] > 0
            strength = 2*math.pi*.03*math.sqrt(2)
            limit = Decimal.from_float(segment['coefficient_roundoff_bound']/(
                (segment['stop']-segment['start'])*strength))
            for index in (0, len(segment['times'])//2, -1):
                time = Decimal.from_float(float(segment['times'][index]))
                if shape == 'gaussian':
                    scaled = (time-Decimal(3))/2
                    exact = (-scaled*scaled/2).exp()
                else:
                    distance = min(time-2, 6-time)
                    if distance >= 1:
                        exact = Decimal(1)
                    else:
                        angle = pi*distance
                        cosine = term = Decimal(1)
                        for order in range(1, 65):
                            term *= -angle*angle/((2*order-1)*(2*order))
                            cosine += term
                        exact = (1-cosine)/2
                actual = Decimal.from_float(float(segment['coefficients'][0][index].real))
                assert abs(actual-exact) <= limit


def test_native_slope_underflow_has_a_nonzero_absolute_error_allowance():
    from fractions import Fraction
    times = np.array([0., 1e308])
    values = np.array([0., 1e-200], dtype=complex)
    coefficient, bound = native._linear_coefficient(times, values)
    assert bound > 0
    exact = Fraction.from_float(1e-200)/2
    actual = Fraction.from_float(float(coefficient(5e307).real))
    assert abs(actual-exact) <= Fraction.from_float(bound)


@pytest.mark.parametrize('times,values,error', [
    ([0., 1e-320], [0., 1.], 'resource_limit'),
    ([0., 1.], [0., float('nan')], 'invalid_model'),
    ([0., 1.], [0., float('inf')], 'invalid_model'),
    ([0., 0.], [0., 1.], 'invalid_model'),
    ([-1e308, 1e308], [1., 1.], 'invalid_model'),
])
def test_invalid_or_unrepresentable_native_interpolation_is_refused(times, values, error):
    with pytest.raises(ValueError, match=error):
        native._linear_coefficient(np.array(times), np.array(values, dtype=complex))
