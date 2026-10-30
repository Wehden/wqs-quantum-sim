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


def _build_static_model(request, plan):
    """Construct static numerics; callers supply a separately certified allocation."""
    import qutip as qt

    if not isinstance(request, LindbladRequest) or not isinstance(plan, AllocationPlan):
        raise ValueError('invalid_model: expected a Lindblad request and allocation plan')
    dimension = request._physical_checks()
    if request._result_plan(dimension) != plan.result:
        raise ValueError('invalid_model: allocation result reservation differs from request')
    if request.model.drives or request.model.collapse or request.initial_state is not None:
        raise ValueError('invalid_model: static construction does not support states, collapse or drives yet')
    modes = request.model.modes
    sizes = [mode.dimension for mode in modes]
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
    return NativeLindbladModel(hamiltonian, (), None,
                               tuple(mode.name for mode in modes), (), plan)


def build_lindblad_model(request: LindbladRequest, plan: AllocationPlan) -> NativeLindbladModel:
    """Revalidate admission before allocating native numerical objects."""
    if not isinstance(request, LindbladRequest) or not isinstance(plan, AllocationPlan):
        raise ValueError('invalid_model: expected a Lindblad request and allocation plan')
    if request.admission_plan() != plan:
        raise ValueError('invalid_model: native allocation plan differs from admission')
    return _build_static_model(request, plan)
