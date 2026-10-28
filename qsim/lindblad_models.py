"""Strict declarative Lindblad requests; no native solver or scheduling authority."""
import json
import math
import re
from typing import Annotated, Literal, TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .physics_results import AllocationPlan

__all__ = ('LindbladRequest',)


def _finite(value):
    if type(value) not in (int, float):
        raise ValueError('expected a finite JSON number')
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError('expected a representable finite number') from None
    if not math.isfinite(number):
        raise ValueError('expected a finite JSON number')
    return 0.0 if number == 0 else number


_Number = Annotated[float, BeforeValidator(_finite)]
_Name = Annotated[str, Field(min_length=1, max_length=64)]


class _Base(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class _Complex(_Base):
    re: _Number = 0.0
    im: _Number = 0.0


def _complex_input(value):
    if type(value) in (int, float):
        return {'re': _finite(value)}
    return value


_ComplexNumber = Annotated[_Complex, BeforeValidator(
    _complex_input, json_schema_input_type=_Number | _Complex)]
_Vector = Annotated[list[_ComplexNumber], Field(min_length=1, max_length=256)]
_Matrix = Annotated[list[_Vector], Field(min_length=1, max_length=256)]


def _error(pointer, message, category='physical_validation'):
    error = ValueError(f'{category}: {message}; pointer={pointer}')
    error.pointer = pointer
    raise error


def _mode_schema(schema):
    schema['oneOf'] = [
        {'properties': {'kind': {'const': 'matrix'}, 'hamiltonian': {'not': {'type': 'null'}}}, 'required': ['hamiltonian'],
         'not': {'anyOf': [{'required': ['frequency']}, {'required': ['kerr']}]}},
        {'properties': {'kind': {'const': 'oscillator'}},
         'not': {'anyOf': [{'required': ['hamiltonian']}, {'required': ['kerr']}]}},
        {'properties': {'kind': {'const': 'duffing'}}, 'not': {'required': ['hamiltonian']}},
    ]


class _Mode(_Base):
    model_config = ConfigDict(extra='forbid', strict=True, json_schema_extra=_mode_schema)
    name: _Name
    kind: Literal['oscillator', 'duffing', 'matrix']
    dimension: int
    frequency: _Number = 0.0
    kerr: _Number = 0.0
    hamiltonian: _Matrix | None = None

    @model_validator(mode='after')
    def _fields(self):
        if self.kind == 'matrix':
            if self.hamiltonian is None or self.model_fields_set & {'frequency', 'kerr'}:
                raise ValueError('matrix mode requires hamiltonian and rejects oscillator controls')
        elif self.hamiltonian is not None or 'hamiltonian' in self.model_fields_set:
            raise ValueError('oscillator/duffing mode rejects matrix hamiltonian')
        elif self.kind == 'oscillator' and 'kerr' in self.model_fields_set:
            raise ValueError('oscillator mode rejects kerr; use duffing')
        return self


class _ProductOperator(_Base):
    kind: Literal['product']
    factors: dict[_Name, Annotated[str, Field(max_length=32)]] = Field(default_factory=dict, max_length=8)


class _MatrixOperator(_Base):
    kind: Literal['matrix']
    name: _Name


_Operator = Annotated[_ProductOperator | _MatrixOperator, Field(discriminator='kind')]


class _Term(_Base):
    operator: _Operator
    coefficient: _ComplexNumber = Field(default_factory=lambda: _Complex(re=1.0))
    hc: bool = False


def _collapse_schema(schema):
    controls = {'operator', 'mode', 'rate', 'T1', 'Tphi', 'occupation'}
    branches = []
    for kind, required in (('operator', {'operator', 'rate'}), ('relaxation', {'mode', 'T1'}),
                           ('dephasing', {'mode', 'Tphi'}), ('thermal', {'mode', 'rate', 'occupation'})):
        branches.append({'properties': {'kind': {'const': kind}, **{key: {'not': {'type': 'null'}} for key in required}}, 'required': sorted(required),
                         'not': {'anyOf': [{'required': [key]} for key in sorted(controls - required)]}})
    schema['oneOf'] = branches


class _Collapse(_Base):
    model_config = ConfigDict(extra='forbid', strict=True, json_schema_extra=_collapse_schema)
    kind: Literal['operator', 'relaxation', 'dephasing', 'thermal']
    operator: _Operator | None = None
    mode: _Name | None = None
    rate: _Number | None = None
    T1: _Number | None = None
    Tphi: _Number | None = None
    occupation: _Number | None = None

    @model_validator(mode='after')
    def _fields(self):
        required = {'operator': {'operator', 'rate'}, 'relaxation': {'mode', 'T1'},
                    'dephasing': {'mode', 'Tphi'}, 'thermal': {'mode', 'rate', 'occupation'}}[self.kind]
        if (self.model_fields_set - {'kind'}) != required or any(getattr(self, key) is None for key in required):
            raise ValueError('collapse channel requires exactly its kind-specific controls')
        return self


class _Fock(_Base):
    kind: Literal['fock']
    index: int = 0


class _Coherent(_Base):
    kind: Literal['coherent']
    amplitude: _ComplexNumber


class _Thermal(_Base):
    kind: Literal['thermal']
    occupation: _Number


class _ProductState(_Base):
    kind: Literal['product']
    modes: dict[_Name, Annotated[_Fock | _Coherent | _Thermal, Field(discriminator='kind')]] = Field(
        default_factory=dict, max_length=8)


class _AmplitudeState(_Base):
    kind: Literal['amplitudes']
    values: _Vector


class _DensityState(_Base):
    kind: Literal['density']
    values: _Matrix


_State = Annotated[_ProductState | _AmplitudeState | _DensityState, Field(discriminator='kind')]


def _envelope_schema(schema):
    controls = {'start', 'stop', 'rise', 'center', 'sigma', 'times', 'values', 'interpolation'}
    branches = []
    for shape, required, optional in (
        ('constant', set(), set()), ('square', {'start', 'stop'}, {'rise'}),
        ('gaussian', {'start', 'stop', 'center', 'sigma'}, set()),
        ('flattop_cosine', {'start', 'stop', 'rise'}, set()),
        ('sampled', {'times', 'values'}, {'interpolation'}),
    ):
        branch = {'properties': {'shape': {'const': shape}}, 'required': sorted(required),
                  'not': {'anyOf': [{'required': [key]} for key in sorted(controls - required - optional)]}}
        branch['properties'].update({key: {'not': {'type': 'null'}} for key in required | optional})
        if shape == 'square':
            branch['properties']['rise'] = {'const': 0}
        branches.append(branch)
    schema['oneOf'] = branches


class _Envelope(_Base):
    model_config = ConfigDict(extra='forbid', strict=True, json_schema_extra=_envelope_schema)
    shape: Literal['constant', 'square', 'gaussian', 'flattop_cosine', 'sampled']
    start: _Number | None = None
    stop: _Number | None = None
    rise: _Number | None = None
    center: _Number | None = None
    sigma: _Number | None = None
    times: list[_Number] | None = Field(default=None, min_length=2, max_length=65536)
    values: list[_ComplexNumber] | None = Field(default=None, min_length=2, max_length=65536)
    interpolation: Literal['linear'] | None = None

    @model_validator(mode='after')
    def _fields(self):
        required = {'constant': set(), 'square': {'start', 'stop'},
                    'gaussian': {'start', 'stop', 'center', 'sigma'},
                    'flattop_cosine': {'start', 'stop', 'rise'},
                    'sampled': {'times', 'values'}}[self.shape]
        allowed = required | ({'rise'} if self.shape == 'square' else set())
        if self.shape == 'sampled':
            allowed.add('interpolation')
        supplied = self.model_fields_set - {'shape'}
        if not required <= supplied or supplied - allowed or any(getattr(self, key) is None for key in required):
            raise ValueError('envelope requires exactly its shape-specific controls')
        if any(getattr(self, key) is None for key in supplied):
            raise ValueError('envelope controls must not be null')
        if self.shape == 'sampled':
            self.interpolation = self.interpolation or 'linear'
        if self.shape == 'square':
            if self.rise not in (None, 0.0):
                raise ValueError('positive square rise is not supported in schema v1')
            self.rise = 0.0
        return self


class _Drive(_Base):
    operator: _Operator
    amplitude: _ComplexNumber = Field(default_factory=lambda: _Complex(re=1.0))
    hc: bool = False
    envelope: _Envelope
    detuning: _Number = 0.0
    phase: _Number = 0.0


class _Observable(_Base):
    name: _Name
    operator: _Operator


class _Grid(_Base):
    start: _Number
    stop: _Number
    points: int = Field(ge=2, le=65536)


class _Model(_Base):
    modes: list[_Mode] = Field(min_length=1, max_length=8)
    matrices: dict[_Name, _Matrix] = Field(default_factory=dict, max_length=32)
    hamiltonian: list[_Term] = Field(default_factory=list, max_length=256)
    collapse: list[_Collapse] = Field(default_factory=list, max_length=16)
    drives: list[_Drive] = Field(default_factory=list, max_length=16)


def _operation_schema(schema):
    branches = []
    for operation, allowed, required in (
        ('evolve', {'solver', 'times', 'initial_state', 'observables', 'store_states', 'seed', 'trajectories'}, {'times'}),
        ('propagator', {'solver', 'times'}, {'times'}),
        ('spectrum', {'evals_count', 'return_vectors'}, set()),
        ('steadystate', {'observables', 'return_state'}, set()),
    ):
        all_controls = {'solver', 'times', 'initial_state', 'observables', 'store_states',
                        'seed', 'trajectories', 'evals_count', 'return_vectors', 'return_state'}
        branch = {'properties': {'operation': {'const': operation}},
                  'required': ['operation', *sorted(required)],
                  'not': {'anyOf': [{'required': [key]} for key in sorted(all_controls - allowed)]}}
        branch['properties'].update({key: {'not': {'type': 'null'}} for key in allowed})
        if operation == 'propagator':
            branch['properties']['solver'] = {'enum': ['se', 'me']}
        if operation == 'evolve':
            branch['allOf'] = [{'if': {'properties': {'solver': {'const': 'mc'}}, 'required': ['solver']},
                                'else': {'not': {'anyOf': [{'required': ['seed']}, {'required': ['trajectories']}]}}}]
        branches.append(branch)
    schema['oneOf'] = branches


def _complex_array(values, shape, pointer):
    if len(values) != shape[0] or (len(shape) == 2 and any(len(row) != shape[1] for row in values)):
        _error(pointer, 'array shape differs from declared dimension')
    if len(shape) == 1:
        result = np.array([complex(value.re, value.im) for value in values], dtype=np.complex128)
    else:
        result = np.array([[complex(value.re, value.im) for value in row] for row in values], dtype=np.complex128)
    return result


def _operator_matrix(operator, modes, matrices, pointer):
    dimension = math.prod(mode.dimension for mode in modes)
    if operator.kind == 'matrix':
        if operator.name not in matrices:
            _error(pointer + '/name', 'unknown matrix name')
        return matrices[operator.name]
    names = {mode.name for mode in modes}
    for name in operator.factors:
        if name not in names:
            _error(pointer + '/factors/' + name.replace('~', '~0').replace('/', '~1'), 'unknown mode')
    result = np.ones((1, 1), dtype=np.complex128)
    for mode in modes:
        size = mode.dimension
        primitive = operator.factors.get(mode.name, 'identity')
        if primitive in ('a', 'adag', 'n', 'x', 'y', 'identity'):
            ladder = np.diag(np.sqrt(np.arange(1, size, dtype=float)), 1).astype(np.complex128)
            local = {'a': ladder, 'adag': ladder.conj().T, 'n': np.diag(np.arange(size)),
                     'x': ladder + ladder.conj().T, 'y': -1j * (ladder - ladder.conj().T),
                     'identity': np.eye(size)}[primitive]
        else:
            match = re.fullmatch(r'proj:(0|[1-9][0-9]*),(0|[1-9][0-9]*)', primitive)
            if match is None or any(int(index) >= size for index in match.groups()):
                _error(pointer + '/factors/' + mode.name.replace('~', '~0').replace('/', '~1'),
                       'unsupported primitive or projector index')
            local = np.zeros((size, size), dtype=np.complex128)
            local[int(match[1]), int(match[2])] = 1
        result = np.kron(result, local)
    assert result.shape == (dimension, dimension)
    return result


def _hermitian(matrix, pointer, *, density=False):
    scale = 1.0 if density else max(1.0, float(np.max(np.abs(matrix.real))), float(np.max(np.abs(matrix.imag))))
    scaled = matrix.real / scale + 1j * (matrix.imag / scale)
    residual = float(np.linalg.norm(scaled - scaled.conj().T, ord='fro'))
    bound = 1e-10 if density else 1e-10 * max(1.0 / scale, float(np.linalg.norm(scaled, ord='fro')))
    if not np.isfinite(matrix).all() or not math.isfinite(residual) or residual > bound:
        _error(pointer, 'matrix must be finite and Hermitian')


class LindbladRequest(_Base):
    model_config = ConfigDict(extra='forbid', strict=True, json_schema_extra=_operation_schema)
    engine: Literal['lindblad'] = 'lindblad'
    input_format: Literal['lindblad_model'] = 'lindblad_model'
    schema_version: int = Field(ge=1, le=1, json_schema_extra={'const': 1})
    name: str = Field(default='Untitled simulation', min_length=1, max_length=80)
    operation: Literal['evolve', 'propagator', 'spectrum', 'steadystate']
    model: _Model
    solver: Literal['se', 'me', 'mc'] | None = None
    times: Annotated[list[_Number], Field(min_length=2, max_length=65536)] | _Grid | None = None
    initial_state: _State | None = None
    observables: list[_Observable] = Field(default_factory=list, max_length=32)
    store_states: Literal['none', 'final', 'all'] = 'none'
    seed: int = Field(default=42, ge=0, le=2**63-1)
    trajectories: int = Field(default=1, ge=1, le=1000)
    evals_count: int = 6
    return_vectors: bool = False
    return_state: bool = True

    @model_validator(mode='after')
    def _operation_fields(self):
        allowed = {'evolve': {'solver', 'times', 'initial_state', 'observables', 'store_states'},
                   'propagator': {'solver', 'times'}, 'spectrum': {'evals_count', 'return_vectors'},
                   'steadystate': {'observables', 'return_state'}}[self.operation]
        if self.operation in ('evolve', 'propagator'):
            if self.times is None:
                raise ValueError('dynamic operation requires times')
            if self.solver is None and 'solver' in self.model_fields_set:
                raise ValueError('solver must not be null')
            self.solver = self.solver or 'se'
        if self.operation == 'evolve' and self.solver == 'mc':
            allowed |= {'seed', 'trajectories'}
        if self.operation == 'propagator' and self.solver == 'mc':
            raise ValueError('propagator accepts se or me only')
        controls = {'solver', 'times', 'initial_state', 'observables', 'store_states',
                    'seed', 'trajectories', 'evals_count', 'return_vectors', 'return_state'}
        if self.model_fields_set & (controls - allowed):
            raise ValueError('operation does not accept these controls')
        for key in self.model_fields_set & allowed:
            if getattr(self, key) is None:
                raise ValueError('operation controls must not be null')
        return self

    def canonical_envelope(self) -> dict:
        allowed = {'evolve': {'solver', 'times', 'initial_state', 'observables', 'store_states'},
                   'propagator': {'solver', 'times'}, 'spectrum': {'evals_count', 'return_vectors'},
                   'steadystate': {'observables', 'return_state'}}[self.operation]
        if self.operation == 'evolve' and self.solver == 'mc':
            allowed |= {'seed', 'trajectories'}
        controls = {'solver', 'times', 'initial_state', 'observables', 'store_states',
                    'seed', 'trajectories', 'evals_count', 'return_vectors', 'return_state'}
        result = self.model_dump(mode='json', exclude=controls - allowed, exclude_none=True)
        if isinstance(self.times, _Grid):
            start, stop, count = self.times.start, self.times.stop, self.times.points
            result['times'] = [(1 - index / (count - 1)) * start + (index / (count - 1)) * stop
                               for index in range(count)]
            result['times'][0], result['times'][-1] = start, stop
        if self.operation == 'evolve' and self.initial_state is None:
            result['initial_state'] = {'kind': 'product', 'modes': {}}
        for mode in result['model']['modes']:
            if mode['kind'] == 'matrix':
                mode.pop('frequency', None)
                mode.pop('kerr', None)
            elif mode['kind'] == 'oscillator':
                mode.pop('kerr', None)
        return json.loads(json.dumps(result, allow_nan=False, sort_keys=True))

    def _state_checks(self, dimension):
        state = self.initial_state
        mixed = False
        if state is None:
            return
        if state.kind == 'amplitudes':
            vector = _complex_array(state.values, (dimension,), '/initial_state/values')
            scale = max(float(np.max(np.abs(vector.real))), float(np.max(np.abs(vector.imag))))
            if scale == 0:
                _error('/initial_state/values', 'pure state must be nonzero')
            normalized = vector.real / scale + 1j * (vector.imag / scale)
            if not math.isfinite(float(np.linalg.norm(normalized))):
                _error('/initial_state/values', 'unrepresentable pure state norm')
        elif state.kind == 'density':
            density = _complex_array(state.values, (dimension, dimension), '/initial_state/values')
            _hermitian(density, '/initial_state/values', density=True)
            if abs(np.trace(density) - 1.0) > 1e-10:
                _error('/initial_state/values', 'density trace must be one')
            eigenvalues = np.linalg.eigvalsh(density)
            if eigenvalues[0] < -1e-10:
                _error('/initial_state/values', 'density must be positive semidefinite')
            mixed = float(np.sum(eigenvalues[:-1])) > 1e-10
        else:
            sizes = {mode.name: mode.dimension for mode in self.model.modes}
            for name, factor in state.modes.items():
                pointer = '/initial_state/modes/' + name.replace('~', '~0').replace('/', '~1')
                if name not in sizes:
                    _error(pointer, 'unknown state mode')
                if factor.kind == 'fock' and not 0 <= factor.index < sizes[name]:
                    _error(pointer + '/index', 'Fock index outside mode')
                if factor.kind == 'coherent':
                    amplitude = complex(factor.amplitude.re, factor.amplitude.im)
                    if math.hypot(amplitude.real, amplitude.imag) > math.sqrt(np.finfo(float).max):
                        _error(pointer + '/amplitude', 'unrepresentable coherent occupation')
                if factor.kind == 'thermal':
                    if factor.occupation < 0:
                        _error(pointer + '/occupation', 'thermal occupation must be nonnegative')
                    mixed |= factor.occupation > 0 and sizes[name] > 1
        if mixed and self.solver in ('se', 'mc'):
            _error('/initial_state', 'pure-state solver rejects mixed state')

    def _physical_checks(self) -> int:
        modes = self.model.modes
        names = [mode.name for mode in modes]
        if len(set(names)) != len(names):
            _error('/model/modes', 'mode names must be unique')
        dimension = 1
        for index, mode in enumerate(modes):
            if mode.dimension < 1 or mode.dimension > 256 // dimension:
                _error(f'/model/modes/{index}/dimension', 'Hilbert dimension exceeds 256', 'resource_limit')
            dimension *= mode.dimension
        if (self.solver == 'me' or self.operation == 'steadystate') and dimension > 32:
            _error('/model/modes', 'dense density/Liouville dimension exceeds 32', 'resource_limit')
        matrices = {name: _complex_array(values, (dimension, dimension),
                    '/model/matrices/' + name.replace('~', '~0').replace('/', '~1'))
                    for name, values in self.model.matrices.items()}
        hamiltonian = np.zeros((dimension, dimension), dtype=np.complex128)
        with np.errstate(over='ignore', invalid='ignore'):
            for index, mode in enumerate(modes):
                if mode.kind == 'matrix':
                    local = _complex_array(mode.hamiltonian, (mode.dimension, mode.dimension),
                                           f'/model/modes/{index}/hamiltonian')
                    _hermitian(local, f'/model/modes/{index}/hamiltonian')
                    before = math.prod(item.dimension for item in modes[:index])
                    after = math.prod(item.dimension for item in modes[index+1:])
                    hamiltonian += np.kron(np.kron(np.eye(before), local), np.eye(after))
                else:
                    n = _operator_matrix(_ProductOperator(kind='product', factors={mode.name: 'n'}),
                                         modes, matrices, f'/model/modes/{index}')
                    hamiltonian += mode.frequency * n + 0.5 * mode.kerr * (n @ n - n)
            for index, term in enumerate(self.model.hamiltonian):
                matrix = _operator_matrix(term.operator, modes, matrices, f'/model/hamiltonian/{index}/operator')
                weighted = complex(term.coefficient.re, term.coefficient.im) * matrix
                hamiltonian += weighted + weighted.conj().T if term.hc else weighted
        _hermitian(hamiltonian, '/model/hamiltonian')
        with np.errstate(over='ignore', invalid='ignore'):
            angular_finite = np.isfinite(hamiltonian * (2 * math.pi)).all()
        if not angular_finite:
            _error('/model/hamiltonian', 'angular Hamiltonian is unrepresentable')
        if self.operation == 'spectrum' and not 1 <= self.evals_count <= dimension:
            _error('/evals_count', 'requested levels exceed dimension')
        if self.operation in ('spectrum', 'steadystate') and self.model.drives:
            _error('/model/drives', 'static operation rejects drives')
        if self.operation == 'spectrum' and self.model.collapse:
            _error('/model/collapse', 'spectrum rejects dissipation')
        if self.solver == 'se' and self.model.collapse:
            _error('/model/collapse', 'se rejects dissipation')
        channels = 0
        nonzero = False
        for index, channel in enumerate(self.model.collapse):
            pointer = f'/model/collapse/{index}'
            if channel.kind == 'operator':
                matrix = _operator_matrix(channel.operator, modes, matrices, pointer + '/operator')
                rate = channel.rate
                strength = float(np.max(np.abs(matrix)))
            else:
                if channel.mode not in names:
                    _error(pointer + '/mode', 'unknown collapse mode')
                mode = modes[names.index(channel.mode)]
                strength = max(0, mode.dimension - 1)
                if channel.kind in ('relaxation', 'dephasing'):
                    time = channel.T1 if channel.kind == 'relaxation' else channel.Tphi
                    if time <= 0:
                        _error(pointer, 'decay time must be positive')
                    rate = (1 if channel.kind == 'relaxation' else 2) / time
                else:
                    if channel.occupation < 0:
                        _error(pointer + '/occupation', 'thermal occupation must be nonnegative')
                    rate = channel.rate * (channel.occupation + 1)
            if rate < 0 or not math.isfinite(rate) or not math.isfinite(math.sqrt(rate) * strength):
                _error(pointer, 'collapse strength must be finite and nonnegative')
            channels += 2 if channel.kind == 'thermal' else 1
            nonzero |= rate > 0 and strength > 0
        if channels > 16:
            _error('/model/collapse', 'more than 16 effective collapse channels', 'resource_limit')
        if self.solver == 'mc' and not nonzero:
            _error('/model/collapse', 'mc requires a nonzero collapse channel; use se or me')
        observable_names = [item.name for item in self.observables]
        if len(set(observable_names)) != len(observable_names):
            _error('/observables', 'observable names must be unique')
        for index, observable in enumerate(self.observables):
            _operator_matrix(observable.operator, modes, matrices, f'/observables/{index}/operator')
        if self.times is not None:
            times = self.canonical_envelope()['times']
            if any(not right > left for left, right in zip(times, times[1:])) or not math.isfinite(times[-1] - times[0]):
                _error('/times', 'times must be strictly increasing with a representable span')
        knots = set(self.canonical_envelope().get('times', []))
        scalar_channels = sum(2 if drive.hc else 1 for drive in self.model.drives)
        if scalar_channels > 16:
            _error('/model/drives', 'more than 16 scalar coefficient channels', 'resource_limit')
        for index, drive in enumerate(self.model.drives):
            pointer = f'/model/drives/{index}'
            matrix = _operator_matrix(drive.operator, modes, matrices, pointer + '/operator')
            amplitude = complex(drive.amplitude.re, drive.amplitude.im)
            envelope = drive.envelope
            if envelope.shape != 'constant' and envelope.shape != 'sampled':
                span = envelope.stop - envelope.start
                if not math.isfinite(span) or span <= 0:
                    _error(pointer + '/envelope', 'support must have positive finite width')
                knots.update((envelope.start, envelope.stop))
                if envelope.shape == 'gaussian' and envelope.sigma <= 0:
                    _error(pointer + '/envelope/sigma', 'sigma must be positive')
                if envelope.shape == 'flattop_cosine' and not 0 < envelope.rise <= span / 2:
                    _error(pointer + '/envelope/rise', 'rise must be in (0, support/2]')
            if envelope.shape == 'sampled':
                if len(envelope.values) != len(envelope.times) or any(
                    not right > left for left, right in zip(envelope.times, envelope.times[1:])):
                    _error(pointer + '/envelope', 'sample values need matching strictly increasing times')
                if not math.isfinite(envelope.times[-1] - envelope.times[0]):
                    _error(pointer + '/envelope/times', 'unrepresentable sample span')
                knots.update(envelope.times)
                complex_envelope = any(value.im != 0 for value in envelope.values)
            else:
                complex_envelope = False
            if len(knots) > 65536:
                _error(pointer + '/envelope', 'combined coefficient grid exceeds 65536 knots', 'resource_limit')
            time_bound = max((abs(value) for value in knots), default=0.0)
            if not math.isfinite((2 * math.pi * drive.detuning) * time_bound + drive.phase):
                _error(pointer, 'carrier phase is unrepresentable')
            envelope_bound = max((math.hypot(value.re, value.im) for value in envelope.values), default=1.0) if envelope.values else 1.0
            matrix_bound = max((math.hypot(value.real, value.imag) for value in matrix.flat), default=0.0)
            strength_bound = 2 * math.pi * math.hypot(amplitude.real, amplitude.imag) * matrix_bound
            strength_bound *= envelope_bound * (2 if drive.hc else 1)
            if not math.isfinite(strength_bound):
                _error(pointer, 'angular drive strength is unrepresentable')
            if not drive.hc:
                _hermitian(amplitude * matrix, pointer + '/operator')
                if drive.detuning != 0 or complex_envelope or drive.phase != 0:
                    _error(pointer, 'complex modulation requires hc=true')
        self._state_checks(dimension)
        return dimension

    def _result_plan(self, dimension):
        from .physics_results import ArraySpec, plan_result
        arrays = []
        if self.operation in ('evolve', 'propagator'):
            count = len(self.canonical_envelope()['times'])
            arrays.append(ArraySpec('times', 'float64', (count,), 'ns'))
        if self.operation == 'evolve':
            if self.observables:
                arrays.append(ArraySpec('expectations', 'complex128', (count, len(self.observables)), None))
            if self.store_states != 'none':
                retained = 1 if self.store_states == 'final' else count
                shape = (retained, dimension) if self.solver == 'se' else (retained, dimension, dimension)
                arrays.extend((ArraySpec('state_times', 'float64', (retained,), 'ns'),
                               ArraySpec('states', 'complex128', shape, None)))
        elif self.operation == 'propagator':
            size = dimension if self.solver == 'se' else dimension**2
            arrays.append(ArraySpec('propagators', 'complex128', (count, size, size), None))
        elif self.operation == 'spectrum':
            arrays.append(ArraySpec('eigenvalues', 'float64', (self.evals_count,), 'GHz'))
            if self.return_vectors:
                arrays.append(ArraySpec('eigenvectors', 'complex128', (dimension, self.evals_count), None))
        else:
            if self.observables:
                arrays.append(ArraySpec('expectations', 'complex128', (len(self.observables),), None))
            if self.return_state:
                arrays.append(ArraySpec('steady_state', 'complex128', (dimension, dimension), None))
        return plan_result(arrays, metadata_characters=65536, summary_bytes=65536)

    def admission_plan(self) -> 'AllocationPlan':
        dimension = self._physical_checks()
        self._result_plan(dimension)
        _error('/model', 'frozen Lindblad native allocation certificate is unavailable', 'resource_limit')
