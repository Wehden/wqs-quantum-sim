"""Native Lindblad construction inside the isolated numerical child."""
from dataclasses import dataclass
import bisect
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


def _bound_product(*factors):
    """Fail closed if positive bound arithmetic overflows or underflows."""
    if any(not math.isfinite(value) or value < 0 for value in factors):
        raise ValueError('resource_limit: coefficient bound is unrepresentable')
    if 0 in factors:
        return 0.
    product = 1.
    for value in factors:
        product *= value
        if not math.isfinite(product) or product == 0:
            raise ValueError('resource_limit: coefficient bound is unrepresentable')
    return product


def _envelope_bounds(envelope, left, right):
    """Bounds on |e|, |e'| and |e''| within one smooth, one-sided piece."""
    if envelope.shape == 'constant':
        return 1., 0., 0.
    if envelope.shape == 'sampled':
        if right <= envelope.times[0] or left >= envelope.times[-1]:
            return 0., 0., 0.
        index = bisect.bisect_right(envelope.times, left)-1
        index = min(index, len(envelope.times)-2)
        values = [complex(value.re, value.im) for value in envelope.values[index:index+2]]
        width = envelope.times[index+1]-envelope.times[index]
        return max(map(abs, values)), abs(values[1]-values[0])/width, 0.
    if right <= envelope.start or left >= envelope.stop:
        return 0., 0., 0.
    if envelope.shape == 'gaussian':
        return 1., 1/envelope.sigma, _bound_product(1/envelope.sigma, 1/envelope.sigma)
    if envelope.shape == 'flattop_cosine' and (right <= envelope.start+envelope.rise or
                                              left >= envelope.stop-envelope.rise):
        return 1., (.5*math.pi)/envelope.rise, _bound_product(
            math.pi/envelope.rise, math.pi/envelope.rise, .5)
    return 1., 0., 0.


def _envelope_values(envelope, times):
    """Evaluate a segment's one-sided envelope, including its endpoint limits."""
    if envelope.shape == 'constant':
        return np.ones(times.size, dtype=np.complex128)
    if envelope.shape == 'sampled':
        if times[-1] <= envelope.times[0] or times[0] >= envelope.times[-1]:
            return np.zeros(times.size, dtype=np.complex128)
        index = min(bisect.bisect_right(envelope.times, times[0])-1, len(envelope.times)-2)
        first, last = (complex(value.re, value.im) for value in envelope.values[index:index+2])
        weight = (times-envelope.times[index])/(envelope.times[index+1]-envelope.times[index])
        return first*(1-weight)+last*weight
    if times[-1] <= envelope.start or times[0] >= envelope.stop:
        return np.zeros(times.size, dtype=np.complex128)
    if envelope.shape == 'gaussian':
        return np.exp(-.5*((times-envelope.center)/envelope.sigma)**2).astype(complex)
    if envelope.shape == 'flattop_cosine':
        distance = np.minimum(times-envelope.start, envelope.stop-times)
        return np.where(distance < envelope.rise,
                        (1-np.cos(math.pi*np.maximum(distance, 0)/envelope.rise))/2,
                        1.).astype(complex)
    return np.ones(times.size, dtype=np.complex128)


def _carrier_argument_error(frequency, left, duration, phase):
    """Conservative argument uncertainty for factored absolute/relative carriers."""
    rounding = 4*math.ulp(1.) if phase else 0.
    if frequency == 0:
        return rounding
    origin, span = frequency*left, frequency*duration
    if not math.isfinite(origin) or not math.isfinite(span):
        raise ValueError('resource_limit: coefficient carrier is unrepresentable')
    return math.fsum((rounding+12*math.ulp(1.),
                      math.ulp(origin) if left else 0., math.ulp(span),
                      _bound_product(abs(left), 2*math.ulp(frequency)),
                      _bound_product(duration, 2*math.ulp(frequency)),
                      _bound_product(frequency, math.ulp(duration))))


def _coefficient_segments(request, hamiltonian, *, refinement=1):
    """Build bounded linear coefficients; no function/string compilation occurs.

    Each dictionary is a private solver segment. Endpoint limits belong to that
    segment, so a truncated pulse never acquires an artificial ramp. The native
    solver must preserve the incoming state and original absolute time between
    dictionaries. Admission/copy accounting remains separately certificate-gated.
    """
    import qutip as qt

    if not request.model.drives:
        return ()
    if type(refinement) is not int or refinement < 1:
        raise ValueError('invalid_model: coefficient refinement must be a positive integer')
    output = request.canonical_envelope()['times']
    start, stop = output[0], output[-1]
    edges = {start, stop}
    operators = []
    for drive in request.model.drives:
        envelope = drive.envelope
        if envelope.shape == 'sampled':
            edges.update(value for value in envelope.times if start < value < stop)
        elif envelope.shape != 'constant':
            edges.update(value for value in (envelope.start, envelope.stop) if start < value < stop)
            if envelope.shape == 'flattop_cosine':
                if not (envelope.start+envelope.rise > envelope.start and
                        envelope.stop-envelope.rise < envelope.stop):
                    raise ValueError('resource_limit: coefficient feature is not representable')
                edges.update(value for value in (envelope.start+envelope.rise,
                                                envelope.stop-envelope.rise)
                             if start < value < stop)
        operator = (2*math.pi*complex(drive.amplitude.re, drive.amplitude.im))*_operator(request, drive.operator)
        matrix = operator.full()
        scale = max(float(np.max(np.abs(matrix.real))), float(np.max(np.abs(matrix.imag))))
        strength = _bound_product(scale, float(np.linalg.norm(
            matrix.real/scale+1j*(matrix.imag/scale), ord='fro'))) if scale else 0.
        strength = _bound_product(strength, 2 if drive.hc else 1,
                                  2 if request.solver == 'me' else 1)
        if not math.isfinite(strength):
            raise ValueError('resource_limit: coefficient operator bound is unrepresentable')
        operators.append((operator, strength))
    edges = sorted(edges)
    plans = []
    knot_count = 0
    for left, right in zip(edges, edges[1:]):
        duration = right-left
        bounds = []
        phase_bounds = []
        for drive, (_, strength) in zip(request.model.drives, operators):
            try:
                value, slope, curvature = _envelope_bounds(drive.envelope, left, right)
                frequency = abs(2*math.pi*drive.detuning)
                derivative = curvature + _bound_product(2, frequency, slope) + _bound_product(
                    frequency, frequency, value)
                bound = _bound_product(strength, derivative)
                phase_error = _bound_product(duration, strength, value,
                    _carrier_argument_error(frequency, left, duration, drive.phase))
                if phase_error > 1e-6:
                    raise ValueError('resource_limit: coefficient carrier precision is unresolved')
            except (OverflowError, ZeroDivisionError):
                bound = math.inf
            if not math.isfinite(bound):
                raise ValueError('resource_limit: coefficient derivative bound is unrepresentable')
            bounds.append(bound)
            phase_bounds.append(phase_error)
        try:
            curvature = math.fsum(bounds)
        except OverflowError:
            raise ValueError('resource_limit: coefficient derivative sum is unrepresentable') from None
        # Sum over segments and operators to bound integrated generator error.
        error_rate = _bound_product(duration, curvature, len(edges)-1)
        spacing = math.sqrt((8*.99e-6)/error_rate) if error_rate else duration
        ratio = duration/spacing if spacing else math.inf
        if not math.isfinite(ratio) or ratio > 65535/refinement:
            raise ValueError('resource_limit: coefficient grid exceeds 65536 knots')
        intervals = max(1, math.ceil(ratio))*refinement
        knot_count += intervals+1
        if knot_count > 65536:
            raise ValueError('resource_limit: coefficient grid exceeds 65536 knots')
        plans.append((left, right, intervals, curvature, math.fsum(phase_bounds)))
    # Refuse every analytic piece before constructing any numeric time grid.
    grids = []
    knot_count = 0
    for left, right, intervals, curvature, phase_error in plans:
        requested = output[bisect.bisect_left(output, left):bisect.bisect_right(output, right)]
        merged = set(np.linspace(left, right, intervals+1))
        merged.update(requested)
        knot_count += len(merged)
        if knot_count > 65536:
            raise ValueError('resource_limit: coefficient grid exceeds 65536 knots')
        times = np.array(sorted(merged))
        feature_step = ((right-left)/intervals)/2
        if not np.all(np.diff(times) > 0) or not feature_step > 0:
            raise ValueError('resource_limit: coefficient grid has unrepresentable step spacing')
        spacing = float(np.max(np.diff(times)))
        actual_error = _bound_product(right-left, curvature, spacing, spacing, .125)+phase_error
        if actual_error > 1e-6:
            raise ValueError('resource_limit: coefficient error budget cannot be resolved')
        grids.append((left, right, times, feature_step, actual_error))
    # Floating time rounding must not silently coarsen a certified feature grid.
    if math.fsum(grid[-1] for grid in grids) > 1e-6:
        raise ValueError('resource_limit: coefficient error budget cannot be resolved')
    result = []
    for left, right, times, feature_step, actual_error in grids:
        terms = [hamiltonian]
        coefficients = []
        for drive, (operator, _) in zip(request.model.drives, operators):
            values = _envelope_values(drive.envelope, times)
            omega = 2*math.pi*drive.detuning
            # Separate range reduction prevents a large phase/origin from
            # swallowing an otherwise representable varying carrier.
            values *= np.exp(1j*drive.phase)*np.exp(1j*(omega*left))
            values *= np.exp(1j*(omega*(times-left)))
            if not np.isfinite(values).all():
                raise ValueError('invalid_model: coefficient values are not finite')
            coefficients.append(values)
            terms.append([operator, qt.coefficient(values, tlist=times, order=1)])
            if drive.hc:
                conjugate = values.conj()
                coefficients.append(conjugate)
                terms.append([operator.dag(), qt.coefficient(conjugate, tlist=times, order=1)])
        result.append({'start': left, 'stop': right, 'times': times,
                       'coefficients': tuple(coefficients),
                       'hamiltonian': qt.QobjEvo(terms),
                       'max_step': feature_step,
                       'coefficient_error_bound': actual_error})
    return tuple(result)


def _build_model(request, plan):
    """Construct native state/channel numerics under a separately certified plan."""
    import qutip as qt

    if not isinstance(request, LindbladRequest) or not isinstance(plan, AllocationPlan):
        raise ValueError('invalid_model: expected a Lindblad request and allocation plan')
    dimension = request._physical_checks()
    if request._result_plan(dimension) != plan.result:
        raise ValueError('invalid_model: allocation result reservation differs from request')
    with qt.CoreOptions(auto_tidyup=False):
        hamiltonian = _hamiltonian(request)
        state = _initial_state(request)
        channels = _collapse_operators(request)
        segments = _coefficient_segments(request, hamiltonian)
    return NativeLindbladModel(hamiltonian, channels, state,
                               tuple(mode.name for mode in request.model.modes), segments, plan)


def build_lindblad_model(request: LindbladRequest, plan: AllocationPlan) -> NativeLindbladModel:
    """Revalidate admission before allocating native numerical objects."""
    if not isinstance(request, LindbladRequest) or not isinstance(plan, AllocationPlan):
        raise ValueError('invalid_model: expected a Lindblad request and allocation plan')
    if request.admission_plan() != plan:
        raise ValueError('invalid_model: native allocation plan differs from admission')
    return _build_model(request, plan)
