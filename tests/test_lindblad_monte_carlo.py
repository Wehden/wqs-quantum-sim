"""Real pinned serial trajectories; private fixtures do not certify admission."""
from dataclasses import replace

import numpy as np
import pytest
import qutip as qt

from qsim import lindblad_adapter as adapter
from qsim.lindblad_model import _build_model
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import AllocationPlan, serialize_physics_result


def request(*, retention='all', trajectories=64, seed=42, times=None,
            observables=True, drives=None):
    return LindbladRequest(schema_version=1, operation='evolve', solver='mc',
        times=times if times is not None else [7., 8., 9., 10.],
        seed=seed, trajectories=trajectories, store_states=retention,
        model={'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': 2,
                          'frequency': 0.}],
               'collapse': [{'kind': 'relaxation', 'mode': 'q', 'T1': 1.}],
               'drives': drives or []},
        initial_state={'kind': 'product', 'modes': {'q': {'kind': 'fock', 'index': 1}}},
        observables=[{'name': 'number', 'operator': {'kind': 'product',
                     'factors': {'q': 'n'}}}] if observables else [])


def model_for(req):
    size = req._physical_checks()
    plan = AllocationPlan('synthetic-test-only', 'mc-private-numerics',
        {'hilbert': size}, (), 0, {}, req._result_plan(size))
    return _build_model(req, plan)


def staged(req, model=None):
    model = model or model_for(req)
    result = adapter._run_evolve(req, model)
    serialize_physics_result(result, model.allocation_plan.result)
    return result


@pytest.mark.parametrize('retention', ['none', 'final', 'all'])
@pytest.mark.parametrize('trajectories', [1, 32])
def test_ensemble_retention_shapes_trace_and_work(retention, trajectories):
    req = request(retention=retention, trajectories=trajectories)
    result = staged(req)
    assert list(result.arrays) == ['times', 'expectations'] + (
        [] if retention == 'none' else ['state_times', 'states'])
    np.testing.assert_array_equal(result.arrays['times'], req.times)
    assert result.arrays['expectations'].shape == (4, 1)
    if retention != 'none':
        states = result.arrays['states']
        assert states.shape == ((1 if retention == 'final' else 4), 2, 2)
        np.testing.assert_allclose(np.trace(states, axis1=1, axis2=2), 1., atol=1e-7)
        assert np.linalg.eigvalsh(states).min() >= -1e-8
        np.testing.assert_array_equal(result.arrays['state_times'],
                                     req.times[-1:] if retention == 'final' else req.times)
        np.testing.assert_allclose(states[:, 1, 1], result.arrays['expectations'][
            -1:, 0] if retention == 'final' else result.arrays['expectations'][:, 0])
    assert result.metadata['solver'] == 'mc'
    assert result.metadata['seed'] == '42'
    assert result.metadata['bitgenerator'] == 'PCG64'
    assert all(type(value) is str and value.isdecimal() for value in result.work.values())
    assert result.work['trajectories_requested'] == str(trajectories)
    assert result.work['trajectories_completed'] == str(trajectories)
    assert result.work['completed_segments'] == str(trajectories)
    assert result.work['output_points'] == str(4*trajectories)
    s, a, m = (int(result.work[k]) for k in ('set_state_calls', 'integrate_calls', 'mcstep_calls'))
    assert s >= trajectories and m >= 3*trajectories
    assert int(result.work['rhs_evaluations_upper_bound']) == 3*s+(10*100001+6)*(a+m)


def test_fixed_thousand_trajectory_t1_matches_me_within_three_standard_errors():
    # Fixed discovery fixture and declared time points; no seed/time-point search.
    req = request(trajectories=1000, retention='all',
                  times=(7+np.linspace(0, 3000, 21)).tolist())
    body = req.canonical_envelope()
    body['model']['collapse'][0]['T1'] = 1000.
    req = LindbladRequest.model_validate(body)
    result = staged(req)
    reference = qt.mesolve(0*qt.num(2), qt.basis(2, 1), req.times,
        [qt.destroy(2)/np.sqrt(1000.)], e_ops=[qt.num(2)],
        options={'method': 'vern7', 'atol': 1e-10, 'rtol': 1e-9, 'progress_bar': ''})
    p = np.exp(-(np.asarray(req.times)-7)/1000.)
    sigma = np.sqrt(p*(1-p)/1000.)
    assert np.all(abs(result.arrays['expectations'][:, 0]-reference.expect[0]) <= 3*sigma+1e-8)


def test_full_seed_bits_and_repeat_are_preserved():
    req = request(seed=(1 << 60)+7)
    first, repeat = staged(req), staged(req)
    for name in first.arrays:
        np.testing.assert_array_equal(first.arrays[name], repeat.arrays[name])
    assert first.work == repeat.work and first.metadata == repeat.metadata
    assert first.metadata['seed'] == str((1 << 60)+7)
    assert not np.array_equal(first.arrays['expectations'], staged(request(seed=7)).arrays['expectations'])


def test_seed_mapping_matches_native_serial_mcsolve():
    req = request(seed=2**63-1, trajectories=32)
    model = model_for(req)
    result = staged(req, model)
    reference = qt.mcsolve(model.hamiltonian, model.initial_state, req.times,
        list(model.collapse_operators), e_ops=list(model.observables), ntraj=32,
        seeds=req.seed, options={'method': 'vern7', 'atol': 1e-10, 'rtol': 1e-9,
        'nsteps': 100000, 'map': 'serial', 'bitgenerator': 'PCG64', 'progress_bar': ''})
    np.testing.assert_allclose(result.arrays['expectations'][:, 0], reference.expect[0], atol=1e-14)


def test_segment_handoff_preserves_random_hazard_and_unnormalized_state(monkeypatch):
    req = request(times=[7., 7.4, 8., 8.4, 9., 10.], trajectories=128)
    model = model_for(req)
    segment = {'hamiltonian': model.hamiltonian, 'max_step': 0., 'coefficient_error_bound': 0.}
    split = replace(model, coefficient_segments=(dict(segment, start=7., stop=8.),
                                                 dict(segment, start=8., stop=10.)))
    generators = []
    original = qt.MCSolver._get_generator
    def generated(self, seed):
        value = original(self, seed)
        generators.append(value)
        return value
    monkeypatch.setattr(qt.MCSolver, '_get_generator', generated)
    whole = staged(req, model)
    rng_states = [g.bit_generator.state for g in generators]
    generators.clear()
    segmented = staged(req, split)
    assert len(generators) == 128 and rng_states == [g.bit_generator.state for g in generators]
    # Reinitializing the native RK workspace can change last-bit rounding.
    np.testing.assert_allclose(whole.arrays['expectations'], segmented.arrays['expectations'], atol=1e-14, rtol=0)
    np.testing.assert_allclose(whole.arrays['states'], segmented.arrays['states'], atol=1e-14, rtol=0)
    assert whole.work['collapse_events'] == segmented.work['collapse_events']
    assert segmented.work['completed_segments'] == '256'


@pytest.mark.parametrize('retention', ['none', 'final'])
def test_no_native_or_per_trajectory_full_history(monkeypatch, retention):
    monkeypatch.setattr(qt.MCSolver, 'run', lambda *a, **k: pytest.fail('native trajectory history'))
    req = request(retention=retention, times=np.linspace(7, 10, 41).tolist())
    original = np.empty
    shapes = []
    def allocated(shape, *a, **k):
        shapes.append(shape)
        return original(shape, *a, **k)
    monkeypatch.setattr(adapter.np, 'empty', allocated)
    staged(req)
    assert (41, 2) not in shapes and (41, 2, 2) not in shapes


def test_no_observables_is_valid():
    result = staged(request(observables=False, retention='none', trajectories=2))
    assert list(result.arrays) == ['times']


def test_public_mc_entry_stays_closed_before_native_construction(monkeypatch):
    req = request()
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('native construction'))
    with pytest.raises(ValueError, match='certificate'):
        adapter.run_lindblad(req, model_for(req).allocation_plan, execution_context={})


def test_native_failure_is_sanitized_and_failed_work_is_charged(monkeypatch):
    from qutip.solver.integrator.qutip_integrator import IntegratorVern7
    def failure(*args, **kwargs):
        raise RuntimeError('private native path or model')
    monkeypatch.setattr(IntegratorVern7, 'mcstep', failure)
    with pytest.raises(RuntimeError, match='native_nonconvergence') as caught:
        staged(request(trajectories=3))
    assert 'private' not in str(caught.value)
    assert caught.value.work['trajectories_requested'] == '3'
    assert caught.value.work['trajectories_completed'] == '0'
    assert caught.value.work['mcstep_calls'] == '1'


def test_mc_registry_and_core_settings_restore_on_success_and_failure(monkeypatch):
    method = '_wqs_accounted_vern7'
    sentinel = object()
    monkeypatch.setitem(qt.MCSolver._avail_integrators, method, sentinel)
    before = qt.settings.core['auto_tidyup']
    staged(request(trajectories=2))
    assert qt.MCSolver._avail_integrators[method] is sentinel
    assert qt.settings.core['auto_tidyup'] == before
    def failed(*args, **kwargs):
        raise RuntimeError('native private failure')
    monkeypatch.setattr(qt.MCSolver, 'step', failed)
    with pytest.raises(RuntimeError, match='native_nonconvergence'):
        staged(request(trajectories=2))
    assert qt.MCSolver._avail_integrators[method] is sentinel
    assert qt.settings.core['auto_tidyup'] == before


@pytest.mark.parametrize('change', ['mixed', 'empty', 'zero', 'too_many', 'negative_seed'])
def test_invalid_mc_requests_refused_without_native_launch(monkeypatch, change):
    body = request().canonical_envelope()
    if change == 'mixed':
        body['initial_state'] = {'kind': 'density', 'values': [[.5, 0.], [0., .5]]}
    elif change == 'empty':
        body['model']['collapse'] = []
    elif change == 'zero':
        body['model']['collapse'] = [{'kind': 'thermal', 'mode': 'q', 'rate': 0., 'occupation': 0.}]
    elif change == 'too_many':
        body['trajectories'] = 1001
    else:
        body['seed'] = -1
    monkeypatch.setattr(qt, 'MCSolver', lambda *a, **k: pytest.fail('native launch'))
    with pytest.raises(ValueError):
        req = LindbladRequest.model_validate(body)
        req._physical_checks()


def test_zero_effective_channel_refusal_is_physical_not_schema():
    body = request().canonical_envelope()
    body['model']['collapse'] = [{'kind': 'thermal', 'mode': 'q', 'rate': 0., 'occupation': 0.}]
    req = LindbladRequest.model_validate(body)
    with pytest.raises(ValueError, match='mc requires a nonzero collapse channel'):
        req._physical_checks()


def test_retention_changes_neither_random_stream_nor_expectations():
    full = staged(request(retention='all'))
    for retention in ('none', 'final'):
        result = staged(request(retention=retention))
        np.testing.assert_array_equal(result.arrays['expectations'], full.arrays['expectations'])
        assert result.work == full.work
        if retention == 'final':
            np.testing.assert_array_equal(result.arrays['states'][0], full.arrays['states'][-1])


def test_ordered_complex_matrix_observables_are_not_cast_to_real():
    body = request().canonical_envelope()
    body['model']['matrices'] = {'complex': [[0., 0.], [0., {'im': 2.}]]}
    body['observables'].insert(0, {'name': 'complex', 'operator': {'kind': 'matrix', 'name': 'complex'}})
    result = staged(LindbladRequest.model_validate(body))
    assert result.metadata['observable_names'] == ['complex', 'number']
    np.testing.assert_array_equal(result.arrays['expectations'][:, 0], 2j*result.arrays['expectations'][:, 1])


def test_repeated_jumps_use_a_constant_size_native_history(monkeypatch):
    from qutip.solver.mcsolve import MCIntegrator
    original = MCIntegrator._do_collapse
    seen = []
    def collapsed(self, *a, **k):
        before = self.collapses.work['collapse_events']
        value = original(self, *a, **k)
        assert len(self.collapses) == 0
        seen.append(self.collapses.work['collapse_events']-before)
        return value
    monkeypatch.setattr(MCIntegrator, '_do_collapse', collapsed)
    req = request(trajectories=16, times=np.linspace(7, 27, 21).tolist(), drives=[{
        'operator': {'kind': 'product', 'factors': {'q': 'x'}},
        'amplitude': .25, 'envelope': {'shape': 'constant'}}])
    result = staged(req)
    assert sum(seen) == int(result.work['collapse_events']) > 50


def test_square_segments_match_native_piecewise_reference():
    req = request(trajectories=32, times=[7., 7.5, 8., 8.5, 9., 10.], drives=[{
        'operator': {'kind': 'product', 'factors': {'q': 'x'}}, 'amplitude': .1,
        'envelope': {'shape': 'square', 'start': 8., 'stop': 9.}}])
    model = model_for(req)
    result = staged(req, model)
    # A separate native QobjEvo implements the same hard pulse boundaries.
    hamiltonian = qt.QobjEvo([0*qt.num(2), [2*np.pi*.1*qt.sigmax(),
        lambda t: 1. if 8. <= t < 9. else 0.]])
    reference = qt.mcsolve(hamiltonian, model.initial_state, req.times,
        list(model.collapse_operators), e_ops=list(model.observables),
        ntraj=32, seeds=42, options={'method': 'vern7', 'atol': 1e-10,
        'rtol': 1e-9, 'max_step': .001, 'map': 'serial',
        'bitgenerator': 'PCG64', 'progress_bar': ''})
    np.testing.assert_allclose(result.arrays['expectations'][:, 0], reference.expect[0], atol=1e-5, rtol=0)
    assert result.work['completed_segments'] == str(32*len(model.coefficient_segments))


@pytest.mark.parametrize('filename', ['mcsolve.py', 'multitraj.py'])
def test_changed_mc_source_is_rejected_before_result_allocation(monkeypatch, filename):
    import builtins
    import io
    req = request()
    model = model_for(req)
    original = builtins.open
    def opened(path, *a, **k):
        return io.BytesIO(b'changed native source') if str(path).endswith('/'+filename) else original(path, *a, **k)
    monkeypatch.setattr(adapter, 'open', opened, raising=False)
    monkeypatch.setattr(adapter.np, 'empty', lambda *a, **k: pytest.fail('output allocated before source validation'))
    with pytest.raises(ValueError, match='execution_policy_mismatch.*source'):
        adapter._run_evolve(req, model)


@pytest.mark.parametrize('problem', ['empty', 'zero', 'wrong_dims', 'nonfinite', 'density_input'])
def test_bad_native_mc_model_refused_before_output_allocation(monkeypatch, problem):
    req = request()
    model = model_for(req)
    if problem == 'density_input':
        model = replace(model, initial_state=qt.ket2dm(model.initial_state))
    else:
        invalid = {'empty': (), 'zero': (0*qt.destroy(2),),
            'wrong_dims': (qt.qeye(4),), 'nonfinite': (qt.Qobj([[np.nan, 0], [0, 0]]),)}[problem]
        model = replace(model, collapse_operators=invalid)
    monkeypatch.setattr(adapter.np, 'empty', lambda *a, **k: pytest.fail('output allocated before validation'))
    with pytest.raises(ValueError, match='invalid_model.*mc requires'):
        adapter._run_evolve(req, model)


def test_oversized_mc_output_refused_before_native_construction(monkeypatch):
    body = request(times=np.linspace(7, 10, 16).tolist()).canonical_envelope()
    body['model']['modes'][0]['dimension'] = 256
    req = LindbladRequest.model_validate(body)
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('native construction'))
    with pytest.raises(ValueError, match='resource_limit.*numeric result'):
        adapter.run_lindblad(req, None, execution_context={})


def test_failed_second_trajectory_is_never_returned_as_partial_success(monkeypatch):
    original = qt.MCSolver.start
    starts = []
    def started(self, *a, **k):
        starts.append(True)
        if len(starts) == 2:
            raise RuntimeError('native private failure')
        return original(self, *a, **k)
    monkeypatch.setattr(qt.MCSolver, 'start', started)
    with pytest.raises(RuntimeError, match='native_nonconvergence') as caught:
        staged(request(trajectories=3))
    assert caught.value.work['trajectories_completed'] == '1'
    assert caught.value.work['output_points'] == '4'


def test_mc_default_seed_is_effective_42():
    body = request().canonical_envelope()
    body.pop('seed')
    implicit = staged(LindbladRequest.model_validate(body))
    explicit = staged(request())
    assert implicit.metadata == explicit.metadata
    for name in explicit.arrays:
        np.testing.assert_array_equal(implicit.arrays[name], explicit.arrays[name])


def test_previous_native_workspace_is_released_before_next_construction(monkeypatch):
    import weakref
    original = qt.MCSolver.__init__
    references = []
    def constructed(self, *a, **k):
        assert all(value() is None for value in references)
        original(self, *a, **k)
        references.append(weakref.ref(self._integrator._integrator))
    monkeypatch.setattr(qt.MCSolver, '__init__', constructed)
    req = request(trajectories=8, drives=[{
        'operator': {'kind': 'product', 'factors': {'q': 'x'}}, 'amplitude': .1,
        'envelope': {'shape': 'square', 'start': 8., 'stop': 9.}}])
    staged(req)
    assert len(references) == 24 and all(value() is None for value in references)


def test_keyboard_interrupt_propagates_and_restores_native_registry(monkeypatch):
    before = dict(qt.MCSolver._avail_integrators)
    core = qt.settings.core['auto_tidyup']
    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(qt.MCSolver, 'step', interrupted)
    with pytest.raises(KeyboardInterrupt):
        staged(request(trajectories=8))
    assert qt.MCSolver._avail_integrators == before
    assert qt.settings.core['auto_tidyup'] == core


def test_incomplete_native_segments_cannot_claim_completed_trajectory():
    req = request()
    model = model_for(req)
    model = replace(model, coefficient_segments=({'start': 7., 'stop': 8.,
        'hamiltonian': model.hamiltonian, 'max_step': 0., 'coefficient_error_bound': 0.},))
    with pytest.raises(ValueError, match='missing output points'):
        staged(req, model)


def test_wrong_result_reservation_refused_before_output_allocation(monkeypatch):
    req = request()
    model = model_for(req)
    wrong = replace(model.allocation_plan, result=model_for(request(retention='none')).allocation_plan.result)
    model = replace(model, allocation_plan=wrong)
    monkeypatch.setattr(adapter.np, 'empty', lambda *a, **k: pytest.fail('output allocated before plan validation'))
    with pytest.raises(ValueError, match='allocation result reservation'):
        adapter._run_evolve(req, model)


def test_native_mc_options_fix_serial_execution_and_disable_history(monkeypatch):
    original = qt.MCSolver.__init__
    options = []
    def constructed(self, *a, **k):
        options.append(dict(k['options']))
        return original(self, *a, **k)
    monkeypatch.setattr(qt.MCSolver, '__init__', constructed)
    result = staged(request(trajectories=2))
    assert len(options) == 2
    for item in options:
        assert item['map'] == 'serial' and item['bitgenerator'] == 'PCG64'
        assert item['keep_runs_results'] is False and item['store_states'] is False
        assert item['store_final_state'] is False and item['improved_sampling'] is False
        assert item['nsteps'] == 100000 and item['norm_steps'] == result.metadata['norm_steps'] == 25
        assert 'timeout' not in item and 'target_tol' not in item


def test_real_native_collapse_search_failure_is_sanitized(monkeypatch):
    original = qt.MCSolver.__init__
    def constrained(self, *a, **k):
        k['options'] = {**k['options'], 'norm_steps': 0}
        return original(self, *a, **k)
    monkeypatch.setattr(qt.MCSolver, '__init__', constrained)
    with pytest.raises(RuntimeError, match='native_nonconvergence') as caught:
        staged(request(trajectories=2))
    assert 'collapse time' not in str(caught.value)
    assert caught.value.work['trajectories_completed'] == '0'
    assert caught.value.work['output_points'] == '1'
    assert int(caught.value.work['mcstep_calls']) > 0


@pytest.mark.parametrize('initial', [None,
    {'kind': 'amplitudes', 'values': [1., {'im': 1.}]},
    {'kind': 'density', 'values': [[0., 0.], [0., 1.]]},
    {'kind': 'product', 'modes': {'q': {'kind': 'thermal', 'occupation': 0.}}}])
def test_accepted_pure_input_representations_reach_native_mc(initial):
    body = request(trajectories=4).canonical_envelope()
    if initial is None:
        body.pop('initial_state')
    else:
        body['initial_state'] = initial
    req = LindbladRequest.model_validate(body)
    model = model_for(req)
    result = staged(req, model)
    np.testing.assert_allclose(result.arrays['states'][0], qt.ket2dm(model.initial_state).full(), atol=1e-14, rtol=0)
