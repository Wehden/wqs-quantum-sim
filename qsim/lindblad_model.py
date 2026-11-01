"""Native Lindblad construction inside the isolated numerical child."""
from dataclasses import dataclass
import math

import numpy as np

from .lindblad_models import LindbladRequest, _complex_array, _hermitian
from .physics_results import AllocationPlan

__all__ = ('NativeLindbladModel', 'build_lindblad_model')


@dataclass(frozen=True)
class NativeLindbladModel:
    hamiltonian: object
    collapse_operators: tuple
    initial_state: object
    tensor_order: tuple[str, ...]
    coefficient_segments: tuple
    allocation_plan: AllocationPlan


def _tensor_operators(modes, factors):
    import qutip as qt

    local = []
    for mode in modes:
        size = mode.dimension
        primitive = factors.get(mode.name, 'identity')
        if primitive == 'a':
            value = qt.destroy(size) if size > 1 else qt.qzero(1)
        elif primitive == 'adag':
            value = qt.create(size) if size > 1 else qt.qzero(1)
        elif primitive == 'n':
            value = qt.num(size)
        elif primitive in ('x', 'y'):
            ladder = qt.destroy(size) if size > 1 else qt.qzero(1)
            value = ladder + ladder.dag() if primitive == 'x' else -1j*(ladder-ladder.dag())
        elif primitive == 'identity':
            value = qt.qeye(size)
        else:
            # The request's bounded physical validation checks this projector grammar.
            row, column = (int(index) for index in primitive[5:].split(','))
            value = qt.basis(size, row) * qt.basis(size, column).dag()
        local.append(value)
    return qt.tensor(local)


def _operator(request, operator):
    import qutip as qt

    modes = request.model.modes
    if operator.kind == 'product':
        return _tensor_operators(modes, operator.factors)
    sizes = [mode.dimension for mode in modes]
    dimension = math.prod(sizes)
    matrix = _complex_array(request.model.matrices[operator.name],
                            (dimension, dimension), '/model/matrices/' + operator.name)
    return qt.Qobj(matrix, dims=[sizes, sizes])


def _hamiltonian(request):
    import qutip as qt

    modes = request.model.modes
    sizes = [mode.dimension for mode in modes]
    dimension = math.prod(sizes)
    with qt.CoreOptions(auto_tidyup=False):
        hamiltonian = qt.qzero(sizes)
        for index, mode in enumerate(modes):
            if mode.kind == 'matrix':
                matrix = _complex_array(mode.hamiltonian, (mode.dimension, mode.dimension),
                                        f'/model/modes/{index}/hamiltonian')
                _hermitian(matrix, f'/model/modes/{index}/hamiltonian')
                factors = [qt.qeye(size) for size in sizes]
                factors[index] = qt.Qobj(matrix)
                hamiltonian += qt.tensor(factors)
            else:
                number = _tensor_operators(modes, {mode.name: 'n'})
                hamiltonian += mode.frequency*number + .5*mode.kerr*(number*number-number)
        for term in request.model.hamiltonian:
            weighted = complex(term.coefficient.re, term.coefficient.im)*_operator(request, term.operator)
            hamiltonian += weighted + weighted.dag() if term.hc else weighted
        hamiltonian *= 2*math.pi
    matrix = hamiltonian.full()
    if matrix.shape != (dimension, dimension) or not np.isfinite(matrix).all():
        raise ValueError('invalid_model: native Hamiltonian shape or values differ from request')
    _hermitian(matrix, '/model/hamiltonian')
    return hamiltonian


def _build_static_model(request, plan):
    """Construct static numerics; callers supply a separately certified allocation."""
    if not isinstance(request, LindbladRequest) or not isinstance(plan, AllocationPlan):
        raise ValueError('invalid_model: expected a Lindblad request and allocation plan')
    dimension = request._physical_checks()
    if request._result_plan(dimension) != plan.result:
        raise ValueError('invalid_model: allocation result reservation differs from request')
    if request.model.drives or request.model.collapse or request.initial_state is not None:
        raise ValueError('invalid_model: static construction does not support states, collapse or drives yet')
    hamiltonian = _hamiltonian(request)
    modes = request.model.modes
    return NativeLindbladModel(hamiltonian, (), None,
                               tuple(mode.name for mode in modes), (), plan)


def _initial_state(request):
    import qutip as qt

    if request.operation != 'evolve':
        return None
    sizes = [mode.dimension for mode in request.model.modes]
    dimension = math.prod(sizes)
    supplied = request.initial_state
    if supplied is not None and supplied.kind == 'amplitudes':
        vector = _complex_array(supplied.values, (dimension,), '/initial_state/values')
        scale = max(float(np.max(np.abs(vector.real))), float(np.max(np.abs(vector.imag))))
        vector = vector.real / scale + 1j * (vector.imag / scale)
        state = qt.Qobj(vector / np.linalg.norm(vector), dims=[sizes, [1]*len(sizes)])
    elif supplied is not None and supplied.kind == 'density':
        density = _complex_array(supplied.values, (dimension, dimension), '/initial_state/values')
        if request.solver in ('se', 'mc'):
            _, vectors = np.linalg.eigh(density)
            state = qt.Qobj(vectors[:, -1], dims=[sizes, [1]*len(sizes)])
        else:
            state = qt.Qobj(density, dims=[sizes, sizes])
    else:
        factors = []
        for mode in request.model.modes:
            factor = supplied.modes.get(mode.name) if supplied is not None else None
            if factor is None or factor.kind == 'fock':
                value = qt.basis(mode.dimension, factor.index if factor is not None else 0)
            elif mode.dimension == 1:
                value = qt.basis(1, 0)
            elif factor.kind == 'coherent':
                amplitude = complex(factor.amplitude.re, factor.amplitude.im)
                value = qt.coherent(mode.dimension, amplitude, method='operator')
            elif factor.occupation == 0:
                value = qt.basis(mode.dimension, 0)
            else:
                value = qt.thermal_dm(mode.dimension, factor.occupation, method='analytic')
                value = value / value.tr()
            factors.append(value)
        if request.solver == 'me':
            factors = [qt.ket2dm(value) if value.isket else value for value in factors]
        state = qt.tensor(factors)
    if request.solver == 'me' and state.isket:
        state = qt.ket2dm(state)
    values = state.full()
    shape = (dimension, dimension) if request.solver == 'me' else (dimension, 1)
    if values.shape != shape or not np.isfinite(values).all():
        raise ValueError('invalid_model: native initial state shape or values differ from request')
    norm = state.tr() if request.solver == 'me' else state.norm()
    if not np.isfinite(norm) or abs(norm - 1) > 1e-10:
        raise ValueError('invalid_model: native initial state is not normalized')
    return state


def _collapse_operators(request):
    channels = []
    modes = request.model.modes
    for channel in request.model.collapse:
        if channel.kind == 'operator':
            channels.append(math.sqrt(channel.rate) * _operator(request, channel.operator))
        elif channel.kind == 'relaxation':
            channels.append(math.sqrt(1/channel.T1) * _tensor_operators(modes, {channel.mode: 'a'}))
        elif channel.kind == 'dephasing':
            channels.append(math.sqrt(2/channel.Tphi) * _tensor_operators(modes, {channel.mode: 'n'}))
        else:
            channels.append(math.sqrt(channel.rate*(channel.occupation+1)) *
                            _tensor_operators(modes, {channel.mode: 'a'}))
            channels.append(math.sqrt(channel.rate*channel.occupation) *
                            _tensor_operators(modes, {channel.mode: 'adag'}))
    dimension = math.prod(mode.dimension for mode in modes)
    if any(value.shape != (dimension, dimension) or not np.isfinite(value.full()).all()
           for value in channels):
        raise ValueError('invalid_model: native collapse shape or values differ from request')
    return tuple(channels)


def _build_model(request, plan):
    """Construct native state/channel numerics under a separately certified plan."""
    import qutip as qt

    if not isinstance(request, LindbladRequest) or not isinstance(plan, AllocationPlan):
        raise ValueError('invalid_model: expected a Lindblad request and allocation plan')
    dimension = request._physical_checks()
    if request._result_plan(dimension) != plan.result:
        raise ValueError('invalid_model: allocation result reservation differs from request')
    if request.model.drives:
        raise ValueError('invalid_model: numeric drives are not supported yet')
    with qt.CoreOptions(auto_tidyup=False):
        hamiltonian = _hamiltonian(request)
        state = _initial_state(request)
        channels = _collapse_operators(request)
    return NativeLindbladModel(hamiltonian, channels, state,
                               tuple(mode.name for mode in request.model.modes), (), plan)


def build_lindblad_model(request: LindbladRequest, plan: AllocationPlan) -> NativeLindbladModel:
    """Revalidate admission before allocating native numerical objects."""
    if not isinstance(request, LindbladRequest) or not isinstance(plan, AllocationPlan):
        raise ValueError('invalid_model: expected a Lindblad request and allocation plan')
    if request.admission_plan() != plan:
        raise ValueError('invalid_model: native allocation plan differs from admission')
    return _build_model(request, plan)
