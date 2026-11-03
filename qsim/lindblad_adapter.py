"""Deterministic CPU evolution inside the isolated numerical child."""
from collections.abc import Mapping
import hashlib
import math

import numpy as np
import qutip as qt
from qutip.solver.integrator import IntegratorException
from qutip.solver.integrator.qutip_integrator import IntegratorVern7

from .lindblad_model import NativeLindbladModel, build_lindblad_model
from .lindblad_models import LindbladRequest
from .physics_results import AllocationPlan, PhysicsExecutionResult

__all__ = ('run_lindblad',)


class _AccountedVern7(IntegratorVern7):
    """Count native entries before delegation, including failed advances."""

    def set_state(self, t, state):
        self._ledger['set_state_calls'] += 1
        return super().set_state(t, state)

    def integrate(self, t, copy=True):
        self._ledger['integrate_calls'] += 1
        return super().integrate(t, copy=copy)

    def mcstep(self, t, copy=True):
        self._ledger['mcstep_calls'] += 1
        return super().mcstep(t, copy=copy)


def _work_ledger(work):
    bound = 3*work['set_state_calls'] + (10*100001+6)*(
        work['integrate_calls']+work['mcstep_calls'])
    return {**{key: str(value) for key, value in work.items()},
            'rhs_evaluations_upper_bound': str(bound)}


def _validate_state(request, state):
    dimension = math.prod(mode.dimension for mode in request.model.modes)
    values = state.full()
    expected = (dimension, 1) if request.solver == 'se' else (dimension, dimension)
    if values.shape != expected or not np.isfinite(values).all():
        raise ValueError('invalid_result: native state shape or values')
    if request.solver == 'se':
        if abs(np.vdot(values, values)-1) > 1e-7:
            raise ValueError('invalid_result: native ket norm')
    elif (abs(np.trace(values)-1) > 1e-7
          or np.max(np.abs(values-values.conj().T)) > 1e-7
          or np.linalg.eigvalsh(values).min() < -1e-8):
        raise ValueError('invalid_result: native density trace, Hermiticity or positivity')
    return values[:, 0] if request.solver == 'se' else values


def _run_evolve(request, model):
    """Private numerical stage; its caller supplies a separately certified model."""
    if (not isinstance(request, LindbladRequest) or request.operation != 'evolve'
            or request.solver not in ('se', 'me')
            or not isinstance(model, NativeLindbladModel)):
        raise ValueError('invalid_model: expected deterministic evolution')
    dimension = request._physical_checks()
    result_plan = request._result_plan(dimension)
    if model.allocation_plan.result != result_plan:
        raise ValueError('invalid_model: allocation result reservation differs from request')
    observables = model.observables
    sizes = [mode.dimension for mode in request.model.modes]
    if (type(observables) is not tuple or len(observables) != len(request.observables)
            or model.tensor_order != tuple(mode.name for mode in request.model.modes)):
        raise ValueError('invalid_model: native observable count or tensor order differs from request')
    for observable in observables:
        if (not isinstance(observable, qt.Qobj) or not observable.isoper
                or observable.shape != (dimension, dimension)
                or observable.dims != [sizes, sizes]
                or not np.isfinite(observable.full()).all()):
            raise ValueError('invalid_model: native observable shape, tensor dimensions or values')
    # This source-derived bound is valid only for the frozen native tableau.
    if qt.__version__ != '5.3.1' or np.__version__ != '2.5.4':
        raise ValueError('execution_policy_mismatch: Lindblad runtime version')
    source = qt.__file__.rsplit('/', 1)[0] + '/solver/integrator/explicit_rk.pyx'
    try:
        with open(source, 'rb') as stream:
            digest = hashlib.sha256(stream.read(131073)).hexdigest()
    except OSError:
        raise ValueError('execution_policy_mismatch: Lindblad integrator source') from None
    if (digest != 'a2b6f7d8e1c4cdce982d8d11f8a4bcfd353ee9bcd833c4c7fa5eace763c7b01e'
            or IntegratorVern7.tableau['a'].shape != (16, 16)
            or len(IntegratorVern7.tableau['b']) != 10):
        raise ValueError('execution_policy_mismatch: Lindblad integrator source/tableau')
    arrays = {spec.name: np.empty(spec.shape, dtype=spec.dtype) for spec in result_plan.arrays}
    times = arrays['times']
    times[:] = request.canonical_envelope()['times']
    if request.store_states != 'none':
        arrays['state_times'][:] = times[-1:] if request.store_states == 'final' else times
    work = dict(set_state_calls=0, integrate_calls=0, mcstep_calls=0,
                completed_segments=0, output_points=0)
    segments = model.coefficient_segments or ({'start': times[0], 'stop': times[-1],
        'hamiltonian': model.hamiltonian, 'max_step': 0., 'coefficient_error_bound': 0.},)
    solver_class = qt.SESolver if request.solver == 'se' else qt.MESolver
    method = '_wqs_accounted_vern7'
    previous = solver_class._avail_integrators.get(method)
    solver_class.add_integrator(_AccountedVern7, method)
    try:
        with qt.CoreOptions(auto_tidyup=False):
            state = model.initial_state
            _validate_state(request, state)
            index = 0
            for segment in segments:
                options = dict(method=method, atol=1e-10, rtol=1e-9, nsteps=100000,
                               max_step=segment['max_step'], first_step=0., min_step=0.,
                               interpolate=True, normalize_output=False,
                               store_states=False, store_final_state=False, progress_bar='')
                # Release the preceding native workspace before constructing another.
                solver = None
                solver = (solver_class(segment['hamiltonian'], options=options)
                          if request.solver == 'se' else
                          solver_class(segment['hamiltonian'], model.collapse_operators, options=options))
                solver._integrator._ledger = work
                solver.start(state, segment['start'])
                last_time = segment['start']
                while index < times.size and times[index] <= segment['stop']:
                    if times[index] > last_time:
                        state = solver.step(times[index])
                        last_time = times[index]
                    values = _validate_state(request, state)
                    if observables:
                        arrays['expectations'][index] = [qt.expect(op, state) for op in observables]
                        if not np.isfinite(arrays['expectations'][index]).all():
                            raise ValueError('invalid_result: native expectations are not finite')
                    if request.store_states == 'all':
                        arrays['states'][index] = values
                    elif request.store_states == 'final' and index == times.size-1:
                        arrays['states'][0] = values
                    work['output_points'] += 1
                    index += 1
                if last_time < segment['stop']:
                    state = solver.step(segment['stop'])
                    _validate_state(request, state)
                work['completed_segments'] += 1
    except IntegratorException:
        error = RuntimeError('native_nonconvergence: Lindblad evolution failed')
        error.work = _work_ledger(work)
        raise error from None
    finally:
        if previous is None:
            solver_class._avail_integrators.pop(method, None)
        else:
            solver_class._avail_integrators[method] = previous
    metadata = {'engine': 'lindblad', 'operation': 'evolve', 'solver': request.solver,
                'schema_version': 1, 'engine_version': qt.__version__, 'precision': 'complex128',
                'backend': 'cpu', 'tensor_order': list(model.tensor_order),
                'dimensions': [mode.dimension for mode in request.model.modes],
                'numpy_version': np.__version__,
                'observable_names': [item.name for item in request.observables],
                'policy_revision': model.allocation_plan.policy_revision,
                'algorithm': model.allocation_plan.algorithm, 'integrator': 'vern7',
                'atol': 1e-10, 'rtol': 1e-9, 'nsteps': 100000,
                'coefficient_error_bound': math.fsum(s['coefficient_error_bound'] for s in segments),
                'rhs_bound_kind': 'source-derived upper bound'}
    return PhysicsExecutionResult(arrays, metadata, _work_ledger(work))


def run_lindblad(request: LindbladRequest, plan: AllocationPlan, *,
                 execution_context: Mapping) -> PhysicsExecutionResult:
    """Reproduce certified admission before constructing any native object."""
    normalized = LindbladRequest.model_validate(request.canonical_envelope())
    reproduced = normalized.admission_plan()
    if not isinstance(plan, AllocationPlan) or plan != reproduced:
        raise ValueError('execution_policy_mismatch: Lindblad allocation plan')
    if execution_context.get('backend', 'cpu') != 'cpu':
        raise ValueError('execution_policy_mismatch: unsupported Lindblad backend')
    if normalized.operation != 'evolve' or normalized.solver not in ('se', 'me'):
        raise ValueError('invalid_model: only deterministic evolution is implemented')
    return _run_evolve(normalized, build_lindblad_model(normalized, plan))
