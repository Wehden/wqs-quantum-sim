"""Pinned native propagators; private fixtures are not admission certificates."""
from dataclasses import replace
import math

import numpy as np
import pytest
import qutip as qt
from scipy.linalg import expm

from qsim import lindblad_adapter as adapter
from qsim.lindblad_model import _build_model
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import AllocationPlan, serialize_physics_result


def request(*, solver='se', times=None, amplitude=.017, dimension=2, collapse=None, drives=None):
    return LindbladRequest(schema_version=1, operation='propagator', solver=solver,
        times=times if times is not None else [0., 5., 17., 50.],
        model={'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': dimension,
                          'frequency': 0.}],
               'hamiltonian': [{'coefficient': amplitude,
                   'operator': {'kind': 'product', 'factors': {'q': 'x'}}}],
               'collapse': collapse or [], 'drives': drives or []})


def model_for(req):
    size = req._physical_checks()
    plan = AllocationPlan('synthetic-test-only', 'propagator-private-numerics',
        {'hilbert': size}, (), 0, {}, req._result_plan(size))
    return _build_model(req, plan)


def staged(req, model=None):
    model = model or model_for(req)
    result = adapter._run_propagator(req, model)
    serialize_physics_result(result, model.allocation_plan.result)
    return result


def test_rabi_unitary_at_fifty_ns_and_no_hidden_result_members():
    req = request()
    result = staged(req)
    assert list(result.arrays) == ['times', 'propagators']
    assert result.arrays['propagators'].shape == (4, 2, 2)
    assert result.arrays['propagators'].dtype == np.complex128
    for time, value in zip(req.times, result.arrays['propagators']):
        np.testing.assert_allclose(value, expm(-1j*2*math.pi*.017*qt.sigmax().full()*time), atol=1e-6)
        np.testing.assert_allclose(value.conj().T@value, np.eye(2), atol=1e-7)
    np.testing.assert_array_equal(result.arrays['propagators'][0], np.eye(2))
    assert result.metadata['operation'] == 'propagator'
    assert result.metadata['representation'] == 'hilbert-unitary'
    assert 'seed' not in result.metadata and 'trajectories_requested' not in result.work
    assert result.work['set_state_calls'] == '1'
    assert result.work['integrate_calls'] == '3'
    assert result.work['mcstep_calls'] == '0'
    assert result.work['output_points'] == '4'


def test_empty_collapse_me_remains_column_stacked_superoperator(monkeypatch):
    seen = []
    original = qt.MESolver.__init__
    def constructed(self, generator, *a, **k):
        seen.append(generator.issuper)
        assert not a and 'c_ops' not in k
        return original(self, generator, *a, **k)
    monkeypatch.setattr(qt.MESolver, '__init__', constructed)
    monkeypatch.setattr(qt.SESolver, '__init__', lambda *a, **k: pytest.fail('ME fell back to SE'))
    req = request(solver='me')
    result = staged(req)
    assert seen == [True]
    assert result.arrays['propagators'].shape == (4, 4, 4)
    assert result.metadata['representation'] == 'liouville-superoperator'
    assert result.metadata['vectorization'] == 'column-stacking'
    for time, value in zip(req.times, result.arrays['propagators']):
        u = qt.Qobj(expm(-1j*2*math.pi*.017*qt.sigmax().full()*time))
        np.testing.assert_allclose(value, qt.sprepost(u, u.dag()).full(), atol=1e-6)


def test_amplitude_damping_channel_preserves_trace_hermiticity_and_positivity():
    req = request(solver='me', amplitude=0., times=[10., 12., 20., 30.],
        collapse=[{'kind': 'relaxation', 'mode': 'q', 'T1': 7.}])
    result = staged(req)
    density = np.array([[.5, .2+.3j], [.2-.3j, .5]])
    for time, value in zip(req.times, result.arrays['propagators']):
        p = math.exp(-(time-10)/7)
        expected = np.diag([1., math.sqrt(p), math.sqrt(p), p]).astype(complex)
        expected[0, 3] = 1-p
        np.testing.assert_allclose(value, expected, atol=1e-7)
        evolved = (value@density.reshape(4, order='F')).reshape((2, 2), order='F')
        np.testing.assert_allclose(evolved, evolved.conj().T, atol=1e-7)
        np.testing.assert_allclose(np.trace(evolved), 1., atol=1e-7)
        assert np.linalg.eigvalsh(evolved).min() >= -1e-8
        channel = qt.Qobj(value, dims=[[[2], [2]], [[2], [2]]], superrep='super')
        assert qt.to_choi(channel).eigenenergies().min() >= -1e-8


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_tensor_order_and_first_supplied_time(solver):
    req = LindbladRequest(schema_version=1, operation='propagator', solver=solver,
        times=[7., 8., 10.], model={'modes': [
            {'name': name, 'kind': 'oscillator', 'dimension': dimension, 'frequency': 0.}
            for name, dimension in [('left', 2), ('right', 3)]],
            'hamiltonian': [{'coefficient': .017,
                'operator': {'kind': 'product', 'factors': {'left': 'x'}}}]})
    result = staged(req)
    assert result.metadata['tensor_order'] == ['left', 'right']
    assert result.metadata['dimensions'] == [2, 3]
    for time, value in zip(req.times, result.arrays['propagators']):
        u = np.kron(expm(-1j*2*math.pi*.017*qt.sigmax().full()*(time-7)), np.eye(3))
        expected = u if solver == 'se' else np.kron(u.conj(), u)
        np.testing.assert_allclose(value, expected, atol=1e-6)
    assert result.arrays['propagators'].shape == ((3, 6, 6) if solver == 'se' else (3, 36, 36))


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_piecewise_pulse_keeps_cumulative_propagator_and_boundary_work(solver):
    req = request(solver=solver, amplitude=0., times=[7., 7.5, 8.5, 10.], drives=[{
        'operator': {'kind': 'product', 'factors': {'q': 'x'}}, 'amplitude': .017,
        'envelope': {'shape': 'square', 'start': 8., 'stop': 9.}}])
    result = staged(req)
    for time, value in zip(req.times, result.arrays['propagators']):
        duration = np.clip(time-8, 0, 1)
        u = expm(-1j*2*math.pi*.017*qt.sigmax().full()*duration)
        expected = u if solver == 'se' else np.kron(u.conj(), u)
        np.testing.assert_allclose(value, expected, atol=1e-7)
    assert result.work['set_state_calls'] == '3'
    assert result.work['completed_segments'] == '3'
    assert result.work['integrate_calls'] == '5'
    assert result.work['output_points'] == '4'


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_rotating_complex_drive_uses_absolute_carrier_time(solver):
    frequency, amplitude, phase = .07, .02, .3
    req = request(solver=solver, amplitude=0., times=[7., 7.1, 8.5, 10., 15.], drives=[{
        'operator': {'kind': 'product', 'factors': {'q': 'a'}}, 'hc': True,
        'amplitude': amplitude, 'detuning': frequency, 'phase': phase,
        'envelope': {'shape': 'constant'}}])
    result = staged(req)
    omega, coupling = 2*math.pi*frequency, 2*math.pi*amplitude
    h = np.array([[omega/2, coupling], [coupling, -omega/2]])
    origin = np.diag(np.exp([-.5j*(omega*7+phase), .5j*(omega*7+phase)]))
    for time, value in zip(req.times, result.arrays['propagators']):
        rotation = np.diag(np.exp([.5j*(omega*time+phase), -.5j*(omega*time+phase)]))
        u = rotation@expm(-1j*h*(time-7))@origin
        expected = u if solver == 'se' else np.kron(u.conj(), u)
        np.testing.assert_allclose(value, expected, atol=1e-6)


@pytest.mark.parametrize('solver,dimension', [('se', 256), ('me', 16)])
def test_exact_output_boundary_refuses_before_native_build(solver, dimension, monkeypatch):
    allowed = request(solver=solver, dimension=dimension, times=[0., 1., 2., 3., 4.])
    plan = allowed._result_plan(allowed._physical_checks())
    assert plan.numeric_bytes == 5*256*256*16+5*8
    oversized = request(solver=solver, dimension=dimension, times=[0., 1., 2., 3., 4., 5.])
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('native build before budget'))
    with pytest.raises(ValueError, match='resource_limit.*numeric result'):
        adapter.run_lindblad(oversized, None, execution_context={})


def test_public_entry_stays_certificate_gated(monkeypatch):
    req = request()
    model = model_for(req)
    monkeypatch.setattr(adapter, 'build_lindblad_model', lambda *a: pytest.fail('native build before certificate'))
    with pytest.raises(ValueError, match='certificate'):
        adapter.run_lindblad(req, model.allocation_plan, execution_context={})


def test_wrong_result_plan_refuses_before_liouvillian_and_output(monkeypatch):
    req = request(solver='me')
    model = model_for(req)
    wrong = model_for(request()).allocation_plan.result
    model = replace(model, allocation_plan=replace(model.allocation_plan, result=wrong))
    monkeypatch.setattr(qt, 'liouvillian', lambda *a, **k: pytest.fail('Liouvillian before budget'))
    monkeypatch.setattr(adapter.np, 'empty', lambda *a, **k: pytest.fail('output before budget'))
    with pytest.raises(ValueError, match='allocation result reservation'):
        adapter._run_propagator(req, model)


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_no_native_memoizer_and_fixed_native_options(solver, monkeypatch):
    monkeypatch.setattr(qt, 'Propagator', lambda *a, **k: pytest.fail('native memoized history'))
    monkeypatch.setattr(qt, 'propagator', lambda *a, **k: pytest.fail('native result history'))
    seen = []
    cls = qt.SESolver if solver == 'se' else qt.MESolver
    original = cls.__init__
    def constructed(self, *a, **k):
        seen.append(dict(k['options']))
        return original(self, *a, **k)
    monkeypatch.setattr(cls, '__init__', constructed)
    staged(request(solver=solver))
    assert seen and all(o['normalize_output'] is False and o['store_states'] is False
        and o['store_final_state'] is False and o['nsteps'] == 100000
        and o['atol'] == 1e-10 and o['rtol'] == 1e-9 for o in seen)


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_native_failure_keeps_charge_and_restores_settings(solver, monkeypatch):
    from qutip.solver.integrator import IntegratorException
    from qutip.solver.integrator.qutip_integrator import IntegratorVern7
    cls = qt.SESolver if solver == 'se' else qt.MESolver
    before = dict(cls._avail_integrators)
    core = qt.settings.core['auto_tidyup']
    def failed(*a, **k):
        raise IntegratorException('private model or path')
    monkeypatch.setattr(IntegratorVern7, 'integrate', failed)
    with pytest.raises(RuntimeError, match='native_nonconvergence') as caught:
        staged(request(solver=solver))
    assert 'private' not in str(caught.value)
    assert caught.value.work['set_state_calls'] == '1'
    assert caught.value.work['integrate_calls'] == '1'
    assert caught.value.work['output_points'] == '1'
    assert cls._avail_integrators == before and qt.settings.core['auto_tidyup'] == core


@pytest.mark.parametrize('problem', ['shape', 'nonfinite', 'unitarity', 'trace', 'hermiticity'])
def test_invalid_native_maps_fail_without_repair(problem, monkeypatch):
    solver = 'me' if problem in ('trace', 'hermiticity') else 'se'
    req = request(solver=solver)
    cls = qt.MESolver if solver == 'me' else qt.SESolver
    if problem == 'shape':
        invalid = qt.qeye(3)
    elif problem == 'nonfinite':
        invalid = qt.Qobj(np.full((2, 2), np.nan))
    elif problem == 'unitarity':
        invalid = .9*qt.qeye(2)
    else:
        values = np.eye(4, dtype=complex)
        if problem == 'trace':
            values *= .9
        else:
            values[1, 1] = 1j
        invalid = qt.Qobj(values, dims=[[[2], [2]], [[2], [2]]], superrep='super')
    monkeypatch.setattr(cls, 'step', lambda *a, **k: invalid)
    with pytest.raises(ValueError, match='invalid_result'):
        staged(req)


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_scalar_hilbert_space_keeps_logical_representation(solver):
    req = LindbladRequest(schema_version=1, operation='propagator', solver=solver,
        times=[7., 8., 9.], model={'modes': [{'name': 'scalar', 'kind': 'matrix',
            'dimension': 1, 'hamiltonian': [[.125]]}]})
    result = staged(req)
    assert result.arrays['propagators'].shape == (3, 1, 1)
    expected = np.exp(-1j*2*np.pi*.125*np.array([0., 1., 2.])) if solver == 'se' else np.ones(3)
    np.testing.assert_allclose(result.arrays['propagators'][:, 0, 0], expected, atol=1e-7)
    if solver == 'me':
        assert result.metadata['representation'] == 'liouville-superoperator'
        assert result.metadata['vectorization'] == 'column-stacking'


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_native_workspace_is_released_between_segments(solver, monkeypatch):
    import weakref
    cls = qt.SESolver if solver == 'se' else qt.MESolver
    original = cls.__init__
    references = []
    core = dict(qt.settings.core.options)
    def constructed(self, *a, **k):
        assert all(value() is None for value in references)
        original(self, *a, **k)
        references.append(weakref.ref(self._integrator))
    monkeypatch.setattr(cls, '__init__', constructed)
    staged(request(solver=solver, drives=[{
        'operator': {'kind': 'product', 'factors': {'q': 'x'}}, 'amplitude': .1,
        'envelope': {'shape': 'square', 'start': 8., 'stop': 9.}}]))
    assert len(references) == 3 and all(value() is None for value in references)
    assert dict(qt.settings.core.options) == core


def test_incomplete_native_segments_cannot_return_partial_success():
    req = request()
    model = model_for(req)
    segment = dict(start=0., stop=5., hamiltonian=model.hamiltonian,
                   max_step=0., coefficient_error_bound=0.)
    with pytest.raises(ValueError, match='missing output points'):
        staged(req, replace(model, coefficient_segments=(segment,)))


def test_native_runtime_mismatch_refuses_before_output_allocation(monkeypatch):
    req = request()
    model = model_for(req)
    monkeypatch.setattr(qt, '__version__', 'uncertified')
    monkeypatch.setattr(adapter.np, 'empty', lambda *a, **k: pytest.fail('allocation before pin check'))
    with pytest.raises(ValueError, match='runtime version'):
        adapter._run_propagator(req, model)
