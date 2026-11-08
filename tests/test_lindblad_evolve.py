"""Real pinned CPU numerics; synthetic plans do not certify public admission."""
import math

import numpy as np
import pytest
import qutip as qt

from qsim import lindblad_adapter as adapter
from qsim.lindblad_model import _build_model
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import AllocationPlan, serialize_physics_result


def request(*, solver='se', retention='all', times=None, dimension=2, frequency=0.,
            collapse=None, initial=None, drives=None, observables=None):
    return LindbladRequest(schema_version=1, operation='evolve', solver=solver,
        times=times if times is not None else np.linspace(0, 10, 41).tolist(),
        store_states=retention, model={'modes': [{'name': 'q', 'kind': 'oscillator',
        'dimension': dimension, 'frequency': frequency}], 'collapse': collapse or [],
        'drives': drives or []}, **({'initial_state': initial} if initial else {}),
        observables=observables if observables is not None else [observable('n', 'n')])


def observable(name, primitive):
    return {'name': name, 'operator': {'kind': 'product', 'factors': {'q': primitive}}}


def drive(envelope=None, amplitude=.025):
    return {'operator': {'kind': 'product', 'factors': {'q': 'x'}},
            'amplitude': amplitude, 'envelope': envelope or {'shape': 'constant'}}


def plan_for(req):
    dimension = req._physical_checks()
    return AllocationPlan('synthetic-test-only', 'evolve-numerics-test',
        {'hilbert': dimension}, (), 0, {}, req._result_plan(dimension))


def staged(req):
    plan = plan_for(req)
    result = adapter._run_evolve(req, _build_model(req, plan))
    serialize_physics_result(result, plan.result)
    return result


@pytest.mark.parametrize('solver', ['se', 'me'])
@pytest.mark.parametrize('retention', ['none', 'final', 'all'])
def test_rabi_retention_axes_complex_expectations_and_trace(solver, retention):
    req = request(solver=solver, retention=retention, times=np.linspace(7, 27, 81).tolist(),
                  drives=[drive()], observables=[observable('lowering', 'a'), observable('number', 'n')])
    result = staged(req)
    times = result.arrays['times']
    angle = 2*math.pi*.025*(times-times[0])
    np.testing.assert_allclose(result.arrays['expectations'][:, 1], np.sin(angle)**2, atol=1e-4)
    np.testing.assert_allclose(result.arrays['expectations'][:, 0],
                               -1j*np.cos(angle)*np.sin(angle), atol=1e-4)
    assert result.metadata['observable_names'] == ['lowering', 'number']
    expected_members = ['times', 'expectations'] + ([] if retention == 'none' else ['state_times', 'states'])
    assert list(result.arrays) == expected_members
    if retention != 'none':
        states = result.arrays['states']
        np.testing.assert_array_equal(result.arrays['state_times'], times[-1:] if retention == 'final' else times)
        assert states.shape == ((1 if retention == 'final' else len(times)), *((2,) if solver == 'se' else (2, 2)))
        if solver == 'me':
            np.testing.assert_allclose(np.trace(states, axis1=1, axis2=2), 1, atol=1e-7)
        else:
            np.testing.assert_allclose(np.sum(abs(states)**2, axis=1), 1, atol=1e-7)
    assert result.metadata['solver'] == solver
    assert 'seed' not in result.metadata and 'trajectories_requested' not in result.work
    assert all(type(value) is str and value.isdecimal() for value in result.work.values())
    s, a, m = (int(result.work[key]) for key in ('set_state_calls', 'integrate_calls', 'mcstep_calls'))
    assert s == 1 and a == len(times)-1 and m == 0
    assert int(result.work['rhs_evaluations_upper_bound']) == 3*s+(10*100001+6)*(a+m)


def test_t1_relative_error():
    req = request(solver='me', times=np.linspace(4, 74, 81).tolist(),
        collapse=[{'kind': 'relaxation', 'mode': 'q', 'T1': 7.}],
        initial={'kind': 'product', 'modes': {'q': {'kind': 'fock', 'index': 1}}})
    result = staged(req)
    np.testing.assert_allclose(result.arrays['expectations'][:, 0],
                              np.exp(-(result.arrays['times']-4)/7), rtol=1e-4, atol=0)


def test_ringdown_relative_error():
    req = request(solver='me', dimension=10, frequency=.017, times=np.linspace(3, 33, 51).tolist(),
        collapse=[{'kind': 'relaxation', 'mode': 'q', 'T1': 11.}],
        initial={'kind': 'product', 'modes': {'q': {'kind': 'coherent', 'amplitude': .4}}},
        observables=[observable('amplitude', 'a')])
    result = staged(req)
    expected = result.arrays['expectations'][0, 0]*np.exp((-.5/11-2j*math.pi*.017)*(result.arrays['times']-3))
    np.testing.assert_allclose(result.arrays['expectations'][:, 0], expected, rtol=1e-3, atol=0)


def test_segment_resets_preserve_state_outputs_and_work():
    req = request(times=[10., 20., 30., 50., 80.],
                  drives=[drive({'shape': 'square', 'start': 23., 'stop': 25.})])
    result = staged(req)
    expected = np.sin(2*math.pi*.025*np.clip(result.arrays['times']-23, 0, 2))**2
    np.testing.assert_allclose(result.arrays['expectations'][:, 0], expected, atol=1e-8)
    assert result.work['set_state_calls'] == '3'
    assert result.work['completed_segments'] == '3'
    assert result.work['output_points'] == '5'
    assert result.work['integrate_calls'] == '6'


def test_no_observables_and_me_never_falls_back_to_kets():
    result = staged(request(solver='me', observables=[]))
    assert list(result.arrays) == ['times', 'state_times', 'states']
    assert result.arrays['states'].shape == (41, 2, 2)


def test_public_admission_refuses_before_native_construction(monkeypatch):
    req = request()
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('constructed before admission'))
    with pytest.raises(ValueError, match='execution_policy_mismatch.*allocation plan'):
        adapter.run_lindblad(req, plan_for(req), execution_context={})


def test_wrong_result_plan_refused_before_output_allocation(monkeypatch):
    req = request()
    model = _build_model(req, plan_for(req))
    from dataclasses import replace
    model = replace(model, allocation_plan=plan_for(request(retention='none')))
    monkeypatch.setattr(adapter.np, 'empty', lambda *a, **k: pytest.fail('allocated before verification'))
    with pytest.raises(ValueError, match='invalid_model.*result'):
        adapter._run_evolve(req, model)


@pytest.mark.parametrize('solver', ['se', 'me'])
@pytest.mark.parametrize('retention', ['none', 'final'])
def test_streaming_never_uses_native_result_history(monkeypatch, retention, solver):
    solver_class = qt.SESolver if solver == 'se' else qt.MESolver
    monkeypatch.setattr(solver_class, 'run', lambda *a, **k: pytest.fail('native history'))
    shapes = []
    original = np.empty
    def allocated(shape, *args, **kwargs):
        shapes.append(shape)
        return original(shape, *args, **kwargs)
    monkeypatch.setattr(adapter.np, 'empty', allocated)
    staged(request(retention=retention, solver=solver))
    assert ((41, 2) if solver == 'se' else (41, 2, 2)) not in shapes


def test_nonconvergence_sanitized_and_failed_call_charged(monkeypatch):
    # Exercise error sanitization separately from real exhaustion below.
    from qutip.solver.integrator import IntegratorException
    from qutip.solver.integrator.qutip_integrator import IntegratorVern7
    def failed(*args, **kwargs):
        raise IntegratorException('private model/path details')
    monkeypatch.setattr(IntegratorVern7, 'integrate', failed)
    with pytest.raises(RuntimeError, match='native_nonconvergence') as caught:
        staged(request())
    assert 'private' not in str(caught.value)
    assert caught.value.work['integrate_calls'] == '1'
    assert caught.value.work['set_state_calls'] == '1'


def test_invalid_native_state_is_refused_without_repair():
    from dataclasses import replace
    req = request(solver='me')
    model = _build_model(req, plan_for(req))
    model = replace(model, initial_state=qt.Qobj([[1.1, 0], [0, -.1]]))
    with pytest.raises(ValueError, match='invalid_result'):
        adapter._run_evolve(req, model)


def test_real_native_exhaustion_keeps_failed_advance_charge():
    from qutip.solver.integrator import IntegratorException
    work = dict(set_state_calls=0, integrate_calls=0, mcstep_calls=0)
    integrator = adapter._AccountedVern7(qt.QobjEvo(-1j*100*qt.sigmax()),
        {'atol': 1e-10, 'rtol': 1e-9, 'nsteps': 1, 'max_step': .001})
    integrator._ledger = work
    integrator.set_state(0., qt.basis(2, 0).data)
    with pytest.raises(IntegratorException):
        integrator.integrate(100.)
    assert work == {'set_state_calls': 1, 'integrate_calls': 1, 'mcstep_calls': 0}
    assert adapter._work_ledger(work)['rhs_evaluations_upper_bound'] == '1000019'


@pytest.mark.parametrize('shape,extra', [('gaussian', {'center': 3.6, 'sigma': .41}),
                                         ('flattop_cosine', {'rise': .6})])
def test_smooth_segments_preserve_certificate_and_refine(shape, extra):
    from scipy.integrate import quad
    from dataclasses import replace
    from qsim.lindblad_model import _coefficient_segments
    req = request(times=[1., 2.4, 3.6, 5.1, 7.],
        drives=[drive({'shape': shape, 'start': 2., 'stop': 6., **extra})])
    model = _build_model(req, plan_for(req))
    ordinary = adapter._run_evolve(req, model)
    refined = adapter._run_evolve(req, replace(model, coefficient_segments=
        _coefficient_segments(req, model.hamiltonian, refinement=2)))
    def envelope(t):
        if not 2 < t < 6:
            return 0.
        if shape == 'gaussian':
            return math.exp(-.5*((t-3.6)/.41)**2)
        distance = min(t-2, 6-t)
        return (1-math.cos(math.pi*distance/.6))/2 if distance < .6 else 1.
    expected = [math.sin(2*math.pi*.025*quad(envelope, 1, t, points=[p for p in [2, 2.6, 5.4, 6] if p<t])[0])**2
                for t in ordinary.arrays['times']]
    np.testing.assert_allclose(ordinary.arrays['expectations'][:, 0], expected, atol=1e-7)
    np.testing.assert_allclose(ordinary.arrays['expectations'], refined.arrays['expectations'], atol=1e-7)
    assert 0 < ordinary.metadata['coefficient_error_bound'] <= 1e-6
    assert refined.metadata['coefficient_error_bound'] < ordinary.metadata['coefficient_error_bound']


def test_registry_and_core_settings_restored_after_success_and_failure(monkeypatch):
    key = '_wqs_accounted_vern7'
    sentinel = object()
    monkeypatch.setitem(qt.SESolver._avail_integrators, key, sentinel)
    setting = qt.settings.core['auto_tidyup']
    staged(request(retention='none'))
    assert qt.SESolver._avail_integrators[key] is sentinel
    assert qt.settings.core['auto_tidyup'] == setting
    from qutip.solver.integrator import IntegratorException
    from qutip.solver.integrator.qutip_integrator import IntegratorVern7
    def failed(*args, **kwargs):
        raise IntegratorException('failure')
    monkeypatch.setattr(IntegratorVern7, 'integrate', failed)
    with pytest.raises(RuntimeError):
        staged(request())
    assert qt.SESolver._avail_integrators[key] is sentinel
    assert qt.settings.core['auto_tidyup'] == setting


def test_work_is_job_local_across_repeated_runs():
    first = staged(request(retention='none'))
    second = staged(request(retention='final'))
    assert first.work == second.work


def test_oversized_result_refused_before_native_construction(monkeypatch):
    req = request(solver='me', dimension=16, times=np.linspace(0, 1, 2048).tolist())
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('native construction'))
    with pytest.raises(ValueError, match='resource_limit.*numeric result'):
        adapter.run_lindblad(req, None, execution_context={})


def test_matrix_observable_preserves_complex_value_and_order():
    body = request(observables=[]).canonical_envelope()
    body['model']['matrices'] = {'complex': [[{'im': 2.}, 0.], [0., 0.]]}
    body['observables'] = [observable('identity', 'identity'),
        {'name': 'matrix', 'operator': {'kind': 'matrix', 'name': 'complex'}}]
    result = staged(LindbladRequest.model_validate(body))
    np.testing.assert_allclose(result.arrays['expectations'], np.tile([1., 2j], (41, 1)))
    assert result.metadata['observable_names'] == ['identity', 'matrix']


def test_fixed_native_options_and_segment_max_step(monkeypatch):
    seen = []
    original = qt.SESolver.__init__
    def constructed(self, *args, **kwargs):
        seen.append(dict(kwargs['options']))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(qt.SESolver, '__init__', constructed)
    req = request(drives=[drive({'shape': 'square', 'start': 4., 'stop': 4.2})])
    model = _build_model(req, plan_for(req))
    adapter._run_evolve(req, model)
    assert [options['max_step'] for options in seen] == [s['max_step'] for s in model.coefficient_segments]
    assert all(options['atol'] == 1e-10 and options['rtol'] == 1e-9
               and options['nsteps'] == 100000 and options['normalize_output'] is False
               and options['store_states'] is False and options['store_final_state'] is False
               for options in seen)


def test_adapter_translates_real_exhaustion_and_retains_ledger(monkeypatch):
    original = adapter._AccountedVern7._prepare
    def constrained(self):
        self._options['nsteps'] = 1
        self._options['max_step'] = 1e-6
        return original(self)
    monkeypatch.setattr(adapter._AccountedVern7, '_prepare', constrained)
    with pytest.raises(RuntimeError, match='native_nonconvergence') as caught:
        staged(request(drives=[drive()]))
    assert caught.value.work['set_state_calls'] == '1'
    assert caught.value.work['integrate_calls'] == '1'
    assert caught.value.work['completed_segments'] == '0'
    assert caught.value.work['output_points'] == '1'
    assert caught.value.work['rhs_evaluations_upper_bound'] == '1000019'


def test_carrier_uses_absolute_first_time_in_complex_dynamics():
    from scipy.linalg import expm
    frequency, amplitude, phase = .07, .02, .3
    times = [7., 7.1, 8.5, 10., 15.]
    req = request(times=times, observables=[observable('a', 'a')], drives=[{
        'operator': {'kind': 'product', 'factors': {'q': 'a'}}, 'hc': True,
        'amplitude': amplitude, 'detuning': frequency, 'phase': phase,
        'envelope': {'shape': 'constant'}}])
    result = staged(req)
    omega, coupling = 2*math.pi*frequency, 2*math.pi*amplitude
    rotating_h = np.array([[omega/2, coupling], [coupling, -omega/2]])
    origin = np.diag(np.exp([-.5j*(omega*times[0]+phase), .5j*(omega*times[0]+phase)]))
    for index, time in enumerate(times):
        rotation = np.diag(np.exp([.5j*(omega*time+phase), -.5j*(omega*time+phase)]))
        expected = rotation @ expm(-1j*rotating_h*(time-times[0])) @ origin @ np.array([1., 0.])
        np.testing.assert_allclose(result.arrays['states'][index], expected, atol=1e-6)
        np.testing.assert_allclose(result.arrays['expectations'][index, 0], expected[0].conjugate()*expected[1], atol=1e-6)


def test_native_model_defaulted_observables_preserves_six_positional_constructor():
    from qsim.lindblad_model import NativeLindbladModel
    req = request(observables=[])
    built = _build_model(req, plan_for(req))
    legacy = NativeLindbladModel(built.hamiltonian, built.collapse_operators,
        built.initial_state, built.tensor_order, built.coefficient_segments, built.allocation_plan)
    assert legacy.observables == () and built.observables == ()


@pytest.mark.parametrize('problem', ['missing', 'extra', 'not-tuple', 'not-native',
                                     'ket', 'shape', 'tensor', 'tensor-order', 'nonfinite'])
def test_observable_handoff_mismatch_refused_before_result_allocation(monkeypatch, problem):
    from dataclasses import replace
    req = LindbladRequest(schema_version=1, operation='evolve', solver='se', times=[0., 1.],
        model={'modes': [{'name': name, 'kind': 'oscillator', 'dimension': 2}
                         for name in ('left', 'right')]},
        observables=[{'name': 'identity', 'operator': {'kind': 'product', 'factors': {}}}])
    model = _build_model(req, plan_for(req))
    invalid = {'missing': (), 'extra': (qt.qeye([2, 2]), qt.qeye([2, 2])),
        'not-tuple': [qt.qeye([2, 2])], 'not-native': (np.eye(4),),
        'ket': (qt.basis([2, 2], [0, 0]),), 'shape': (qt.qeye(2),),
        'tensor': (qt.qeye(4),), 'tensor-order': model.observables,
        'nonfinite': (qt.Qobj(np.full((4, 4), np.nan), dims=[[2, 2], [2, 2]]),)}[problem]
    model = replace(model, observables=invalid)
    if problem == 'tensor-order':
        model = replace(model, tensor_order=('right', 'left'))
    monkeypatch.setattr(adapter.np, 'empty', lambda *a, **k: pytest.fail('result allocation before observable validation'))
    with pytest.raises(ValueError, match='invalid_model.*observable'):
        adapter._run_evolve(req, model)


def test_builder_observable_failure_restores_native_settings(monkeypatch):
    from qsim import lindblad_model as native
    req = request()
    plan = plan_for(req)
    before = qt.settings.core['auto_tidyup']
    def failed(*args, **kwargs):
        assert qt.settings.core['auto_tidyup'] is False
        raise RuntimeError('observable construction failed')
    monkeypatch.setattr(native, '_operator', failed)
    with pytest.raises(RuntimeError, match='observable construction failed'):
        native._build_model(req, plan)
    assert qt.settings.core['auto_tidyup'] == before


def test_normalized_request_preserves_native_observable_order():
    req = request(observables=[observable('second', 'n'), observable('first', 'identity')])
    normalized = LindbladRequest.model_validate(req.canonical_envelope())
    model = _build_model(normalized, plan_for(normalized))
    assert isinstance(model.observables, tuple) and len(model.observables) == 2
    np.testing.assert_array_equal(model.observables[0].full(), np.diag([0, 1]))
    np.testing.assert_array_equal(model.observables[1].full(), np.eye(2))
    result = adapter._run_evolve(normalized, model)
    assert result.metadata['observable_names'] == ['second', 'first']
    np.testing.assert_array_equal(result.arrays['expectations'], np.tile([0, 1], (41, 1)))
