"""Strict declarative Lindblad requests; no native solver or scheduling authority."""
import json
import math
import sys
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


# Pinned wheel/source owners used by the numerical allocation certificate.
_LINDBLAD_RUNTIME_FILES = [{'distribution': 'numpy',
  'path': 'numpy.libs/libgfortran-83c28eba-468e71e5.so.5.0.0',
  'sha256': 'a6a368328d02a5d8a149746f13f6268f8cc9192df5c18777b25c3c9b5ef52160'},
 {'distribution': 'numpy',
  'path': 'numpy.libs/libquadmath-2284e583-a9307bba.so.0.0.0',
  'sha256': '6bc3069003caec1f075be3c51ae8355332f081fa9465910c4b5724e7fe7d8ad4'},
 {'distribution': 'numpy',
  'path': 'numpy.libs/libscipy_openblas64_-f48b354e.so',
  'sha256': '432e1c1739731468ba305771af96773939e43016643e2d630f58cb45b6670e96'},
 {'distribution': 'numpy',
  'path': 'numpy/_core/_multiarray_umath.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'b185dfe64b07180bedc7c15fb2735b0d0f4f4e09eb9c15bd8e67f74568aebcbf'},
 {'distribution': 'numpy',
  'path': 'numpy/linalg/_linalg.py',
  'sha256': '99ec3bec6f80194741a62407fbcea521a0690577105fe9f3096f156842de76a9'},
 {'distribution': 'numpy',
  'path': 'numpy/linalg/_umath_linalg.cpython-312-x86_64-linux-gnu.so',
  'sha256': '2e075f582c3a25a21fe97a55931a0fdb22ceb84f4fce8203a5040d405ccf65ce'},
 {'distribution': 'qutip',
  'path': 'qutip/core/_brtensor.cpython-312-x86_64-linux-gnu.so',
  'sha256': '331c8816989cbecb5ad2be790a4d38d5a30f36bc5771927000b0b8d7bc5f583a'},
 {'distribution': 'qutip',
  'path': 'qutip/core/_brtools.cpython-312-x86_64-linux-gnu.so',
  'sha256': '7b082067ff9db838644a66b3e4b9a2a7138c05eb4a3c54e5690b53c9366b86bd'},
 {'distribution': 'qutip',
  'path': 'qutip/core/cy/_element.cpython-312-x86_64-linux-gnu.so',
  'sha256': '0b6171526bc9dfeb11740125ace6ba07b0f9fdc065e173f917e4e980b476813f'},
 {'distribution': 'qutip',
  'path': 'qutip/core/cy/_element.pyx',
  'sha256': 'd8ba45292be8660d6cdddcc183a3134924691b5a98e0e0ef1e082e228cfb311e'},
 {'distribution': 'qutip',
  'path': 'qutip/core/cy/coefficient.cpython-312-x86_64-linux-gnu.so',
  'sha256': '83e6bd4b11d464b1eb88c765442af7a408ae2574110cbe44f7093f516363e43d'},
 {'distribution': 'qutip',
  'path': 'qutip/core/cy/coefficient.pyx',
  'sha256': '5b5f4df2e9c5dbc92f54a9ca22dd313143fbab353cd2ca3f1871a406a640a695'},
 {'distribution': 'qutip',
  'path': 'qutip/core/cy/lindblad_matrix_form.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'ec90c9ff77ee7ae1f28c6fab5d36b0afd990e237cfec405c87ae7ba36650b640'},
 {'distribution': 'qutip',
  'path': 'qutip/core/cy/math.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'e81f00cfdbcf4e9b7a4b0ee9aa3828517327d7b4b6c41a426d825c05107ebd67'},
 {'distribution': 'qutip',
  'path': 'qutip/core/cy/qobjevo.cpython-312-x86_64-linux-gnu.so',
  'sha256': '1dcfbc8c7a9165ea00d9fcf4f3c758fcbb1cd43a12e7c0aea4f86b9e2e516a76'},
 {'distribution': 'qutip',
  'path': 'qutip/core/cy/qobjevo.pyx',
  'sha256': '8d939f48a7fcaeb899d22f1ce6f841c672ff574875b0e34073e7be429c41f464'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/_local_matmul.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'd686a4ef1a04dae62b884125ac38f342e6bdb42d19e5637fd6a1cf17f5109904'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/add.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'f4e5bbf8ece164bf88d6b32915be80a10dabed8360eec302f5d5a080d27fc124'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/add.pyx',
  'sha256': 'a9256c0eeff15d0ec2859f88098d0d1524a727381e772beeb34f4f3833fa409f'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/adjoint.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'fa311a9483f9cae3d5e37f8c1af4bc23d5b8a36ef9538c7313b81c98fa334fe6'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/base.cpython-312-x86_64-linux-gnu.so',
  'sha256': '084801704343bf1516eca53d3ffea95f6c723c18fcd6365c4616d4bdb5b530d7'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/block_operations.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'f6121415cd72f90ac6091ab9c2fa1ad539060cae92925a1f8775c665e43efb39'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/convert.cpython-312-x86_64-linux-gnu.so',
  'sha256': '44873f7f21a43ec44bdf32beb8ce5a5267d3689dac1028a20772a3de3a2e3c93'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/csr.cpython-312-x86_64-linux-gnu.so',
  'sha256': '61e4f6279092cf807979df252b05db614721baebab3be9d1f690448f50cf284f'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/data_iterator.cpython-312-x86_64-linux-gnu.so',
  'sha256': '9c1fdcb140d9d46c3532a2ed5f9d489bf5168a406769bb6dfe0b0d9e12da127a'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/dense.cpython-312-x86_64-linux-gnu.so',
  'sha256': '4e6fb1f00962b3d7ded70925b893735dbe0092b95993021dfbb799da05763530'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/dia.cpython-312-x86_64-linux-gnu.so',
  'sha256': '96c6612c8ee0094661049d4323869b9491e8850ccc658b577b6d94a25e8244fc'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/dispatch.cpython-312-x86_64-linux-gnu.so',
  'sha256': '76493160c017ab87e366053ec374f7b54a316d9c96be7517e570c15c4b589cd6'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/expect.cpython-312-x86_64-linux-gnu.so',
  'sha256': '78604ba1187e1d86eef255377c80222d1c412201a7ec450c6ad2e5ed64d163c2'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/inner.cpython-312-x86_64-linux-gnu.so',
  'sha256': '42dccb15afc2c36e102de84c1d516aeb8b42807339aa1b7cb03eace03963cabd'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/kron.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'e3b7c767ba2aeae59dae33e0fb40908bab3d73c797d4297a5c8eaaf87a5fe719'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/kron.pyx',
  'sha256': 'e296988bf756a2a5fe5f1cb77f21711133b5aa12bd92c944ed8c032ad9929387'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/matmul.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'e8ade1001a215e2fb5d35c0447c72d7c045b315be867cc70233cbcb4b694625a'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/matmul.pyx',
  'sha256': '98934614492b1ee984d32cd3d3656ab854b9df31528298e6508629d53c857262'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/mean.cpython-312-x86_64-linux-gnu.so',
  'sha256': '144d4a3b407c9afe643ed3ac8fca6b6571bcd0102aad0cd48dfa703f9687442d'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/mul.cpython-312-x86_64-linux-gnu.so',
  'sha256': '06ee1ad15e576919f3de392cbb30d7328df95e483016c1f11b1060414b0864a1'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/norm.cpython-312-x86_64-linux-gnu.so',
  'sha256': '9dd06dced2ff8dc4263fd020cc50c13dea2b01004afa25aa27f3af2575fafa5e'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/ode.cpython-312-x86_64-linux-gnu.so',
  'sha256': '42543bdc2ca609d0d96a3f3ec5c0b8330a54b2b95d826dcd4692d887f8ef57ff'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/permute.cpython-312-x86_64-linux-gnu.so',
  'sha256': '3a53a9307c25afff0ceb877497eb75190c575fdb15372ad049aa63a7e95638ab'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/pow.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'e86d34dcd6fe95313eb1ebd51712708bec5dd78899dc389e02756157ebbacb77'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/project.cpython-312-x86_64-linux-gnu.so',
  'sha256': '22560c34b078da0622db9f81f71f7f20da4ed787ce5b212aad3b70858fb80531'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/properties.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'bb1d89574df0c93188ccb5f35614a721f2d84b791af75b7b1410334f4dd265ff'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/ptrace.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'd6c34e1ddf768f04a6dbff7e3a0eaa68a1d7d596d28b509b9228e19fe0af78a4'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/reshape.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'f75ab832053f357521430aa1826139751a65a1ca973f5ee235572b979a4f572d'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/solve.py',
  'sha256': '42d9052b085ec22d15368371f8258e889c46a8b8d2bf192cc526d5ae4056b962'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/tidyup.cpython-312-x86_64-linux-gnu.so',
  'sha256': '7997a47bdc542cfa4a5d95af1c84622db899cf9dee0ce4e0cdc97ca387225f0b'},
 {'distribution': 'qutip',
  'path': 'qutip/core/data/trace.cpython-312-x86_64-linux-gnu.so',
  'sha256': '74f08f141b37db3b30e8aab147e2f9cf6975cdb469eb588995aa40cc77d121cb'},
 {'distribution': 'qutip',
  'path': 'qutip/core/operators.py',
  'sha256': 'e0a63214ae579f07eeda55ce922b488cffe43b8e9e84f10311ee63e1821d31de'},
 {'distribution': 'qutip',
  'path': 'qutip/core/qobj.py',
  'sha256': '1b8734acb26614e633afe969b07058517e6c878208b1d7eac3e061ebee8aaca7'},
 {'distribution': 'qutip',
  'path': 'qutip/core/states.py',
  'sha256': 'ec7ed019f7aab36a4af34944fc62c33d2f72043e5d307f0799550d041309c0ab'},
 {'distribution': 'qutip',
  'path': 'qutip/core/superoperator.py',
  'sha256': '3ed25770c270406b4928f865e7522b9f1ba5f9d58b714034469752295b0be39f'},
 {'distribution': 'qutip',
  'path': 'qutip/core/tensor.py',
  'sha256': '848594c412fae9bee6e225a52385eca56b7d5dc9189e6a6b10d658b3cd57bfff'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/cy/dysolve.cpython-312-x86_64-linux-gnu.so',
  'sha256': '75ecf3a598b016963af23b4466aa94b608f55da64d0f756a8175b5a5685479c8'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/cy/nm_mcsolve.cpython-312-x86_64-linux-gnu.so',
  'sha256': '992defaca69846c996befe1197674b320fb7c114705e9670fc3389d71b8a3e32'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/integrator/explicit_rk.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'bbb5b5680c106a2df22f74782f0fb8f3426262abf764574f3ad29c4516be9038'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/integrator/explicit_rk.pyx',
  'sha256': 'a2b6f7d8e1c4cdce982d8d11f8a4bcfd353ee9bcd833c4c7fa5eace763c7b01e'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/integrator/qutip_integrator.py',
  'sha256': '1a67c489555e29d78cc572ade2ebd898f535d96c58b1372f0e5fc9807f49cbba'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/integrator/verner7efficient.py',
  'sha256': 'c9449bf1a0a152b0428cff140021d62d479ccb8df65a3fade4b29ae1ee3db1b2'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/mcsolve.py',
  'sha256': '68c1810dc3f8d48e5edf6a7b5eaea47c40442be0ee5e431469e607cfb217b7bf'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/mesolve.py',
  'sha256': 'da584d41aad3eb78c1483a75f73d99e81d24005aef40b20b16f1581f2eddae40'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/multitraj.py',
  'sha256': 'a2f2f2e77b9d95e7653042dceff27d9b5d01184cf1c293cd679f8842e07ec8f6'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/sesolve.py',
  'sha256': '262b288a9fa30b71d2683a31aa2c9f3d1fee61ea493806bdf1b40acaadef2d0e'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/sode/_sode.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'f156c9da052a08435e3e1abd5809b078f33dc4aa111fb9811183892543d57619'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/sode/ssystem.cpython-312-x86_64-linux-gnu.so',
  'sha256': '410c9a74d323939fe102954b92a878e97e55fea1df1e91129a55991997188685'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/solver_base.py',
  'sha256': 'c9c2f19069c549a1f04da5565727dc7ccf1a2522113c5c1e547e52381c5b8d58'},
 {'distribution': 'qutip',
  'path': 'qutip/solver/steadystate.py',
  'sha256': '907a818a90cf789567cb2f7111d865985b88caf79bdaf13c6d9a65e3b9746b2e'},
 {'distribution': 'scipy',
  'path': 'scipy.libs/libgfortran-040039e1-0352e75f.so.5.0.0',
  'sha256': 'c6090048eccc763522c12ef016f81da6b627cb3a044f55cf0479a839c41c0980'},
 {'distribution': 'scipy',
  'path': 'scipy.libs/libgfortran-83c28eba.so.5.0.0',
  'sha256': '468e71e59e1d784821a76702e5ecd1e0dbcfc024499f74a552d1bea4b26f27ad'},
 {'distribution': 'scipy',
  'path': 'scipy.libs/libquadmath-2284e583.so.0.0.0',
  'sha256': 'a9307bbaa353623fa014e8b682d50c4629f5e5c7d61daa9a8652f7d165cd421a'},
 {'distribution': 'scipy',
  'path': 'scipy.libs/libquadmath-96973f99-934c22de.so.0.0.0',
  'sha256': '6ed5137f412781ad7863439fb543613f620b43c32b63292a0029246162f5bbc6'},
 {'distribution': 'scipy',
  'path': 'scipy.libs/libscipy_openblas-5f890258.so',
  'sha256': '2a5b4c1a6311873c59132b2ceaaf3c26258a17350b7cd27ea8782a47e3322981'},
 {'distribution': 'scipy',
  'path': 'scipy/linalg/_decomp.py',
  'sha256': '4dbf81305777e4bfd7e6df834e411aa8751b715a7d957c6f42b70808cb0b6a2f'},
 {'distribution': 'scipy',
  'path': 'scipy/linalg/_decomp_svd.py',
  'sha256': '7bdb48fe1ded88a717e41f0d6b0d3247e20cc129c0686eb89e8de88720ff916a'},
 {'distribution': 'scipy',
  'path': 'scipy/linalg/_flapack.cpython-312-x86_64-linux-gnu.so',
  'sha256': '00fc3e3414d7f42f023d5ba1f1b616d3d81da8da0f5cc6b17279af70be963b9c'},
 {'distribution': 'scipy',
  'path': 'scipy/linalg/_internal_matfuncs.cpython-312-x86_64-linux-gnu.so',
  'sha256': 'e98f03e852b7acf316c6a6d2294360ed2fb98ee4b1e9c8eccffd123f9c1dc74f'},
 {'distribution': 'scipy',
  'path': 'scipy/linalg/_matfuncs.py',
  'sha256': '72da5780b458ecf9f78fd75662fa28c95cb14b66c4faf1327891be009830a1b6'},
 {'distribution': 'scipy',
  'path': 'scipy/linalg/lapack.py',
  'sha256': '8601f185072fd4cd98fb516c2035a50f49841e265353afb466f5988ef2042827'}]


def _request_storage_bytes(value):
    """Bound canonical JSON and its Pydantic owners without identity-dependent sizes."""
    pending, total, nodes = [value], 0, 0
    while pending:
        item = pending.pop()
        nodes += 1
        if isinstance(item, dict):
            # CPython 3.12 dict, fields-set and BaseModel header together fit
            # this bound for the fixed schema's <=32 named entries/fields.
            total += 512+256*len(item)
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            total += 64+16*len(item)
            pending.extend(item)
        elif isinstance(item, str):
            total += 80+4*len(item)
        else:
            total += sys.getsizeof(item)
    # Worst-case traversal stack references, including list growth overlap.
    return total+16*nodes



def _operator_storage_bytes(operator, modes, *, superoperator=False):
    """Bound copied drive data from the frozen tensor/operator constructors."""
    dimension = math.prod(mode.dimension for mode in modes)
    size = dimension**2 if superoperator else dimension
    if operator.kind == 'matrix' or any(value.startswith('proj:') for value in operator.factors.values()):
        # Named matrices and basis outer products are Dense; spre/spost tensor
        # them with a same-type identity, retaining exactly size² complex cells.
        return 16*size*size+16384
    # a/adag/n/I have one DIA diagonal, x/y two; kron reserves their
    # product, including duplicate offsets, not merely the used diagonals.
    diagonals = math.prod(2 if operator.factors.get(mode.name) in ('x', 'y') else 1 for mode in modes)
    return 16*size*diagonals+8*diagonals+16384


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

    @model_validator(mode='before')
    @classmethod
    def _input_owner_preflight(cls, value):
        """Refuse a proven input-owner overrun before expanding complex models."""
        if not isinstance(value, dict):
            return value
        matrices, complex_cells = [], 0
        model = value.get('model')
        if isinstance(model, dict):
            named = model.get('matrices')
            if isinstance(named, dict):
                matrices.extend(matrix for matrix in named.values() if isinstance(matrix, list))
            modes = model.get('modes')
            if isinstance(modes, list):
                matrices.extend(mode['hamiltonian'] for mode in modes
                    if isinstance(mode, dict) and mode.get('kind') == 'matrix'
                    and isinstance(mode.get('hamiltonian'), list))
            drives = model.get('drives')
            if isinstance(drives, list):
                for drive in drives:
                    envelope = drive.get('envelope') if isinstance(drive, dict) else None
                    if (isinstance(envelope, dict) and envelope.get('shape') == 'sampled'
                            and isinstance(envelope.get('values'), list)):
                        complex_cells += len(envelope['values'])
        state = value.get('initial_state')
        if isinstance(state, dict) and isinstance(state.get('values'), list):
            if state.get('kind') == 'amplitudes':
                complex_cells += len(state['values'])
            elif state.get('kind') == 'density':
                matrices.append(state['values'])
        complex_cells += sum(len(row) for matrix in matrices for row in matrix if isinstance(row, list))
        # Every valid normalized complex cell has both finite float re/im
        # fields. This exact canonical-structure charge is already included
        # four times in admission, even before other request/output owners.
        per_cell = _request_storage_bytes({'re': 0., 'im': 0.})
        if 4*complex_cells*per_cell > 2*1024**3:
            _error('/model', 'certified Lindblad input owners exceed the CPU budget', 'resource_limit')
        return value

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
        from .physics_results import AllocationPlan
        dimension = self._physical_checks()
        result = self._result_plan(dimension)
        canonical = self.canonical_envelope()
        drives, segments = self.model.drives, 1
        channels = sum(1 + int(drive.hc) for drive in drives)
        if drives:
            start, stop = canonical['times'][0], canonical['times'][-1]
            edges = {start, stop}
            for drive in drives:
                envelope = drive.envelope
                if envelope.shape == 'sampled':
                    points = envelope.times
                elif envelope.shape == 'constant':
                    points = ()
                elif envelope.shape == 'flattop_cosine':
                    points = (envelope.start, envelope.start+envelope.rise,
                              envelope.stop-envelope.rise, envelope.stop)
                else:
                    points = (envelope.start, envelope.stop)
                edges.update(value for value in points if start < value < stop)
            segments = len(edges)-1
            # Every smooth piece has its two endpoint knots in the native grid.
            if 2*segments > 65536:
                _error('/model/drives', 'coefficient breakpoint grid exceeds 65536 knots', 'resource_limit')
        collapse = sum(2 if item.kind == 'thermal' else 1 for item in self.model.collapse)
        observable = len(self.observables)
        # CSR add reserves nnz(left)+nnz(right), up to 2n² cells.
        # DIA add reserves both diagonal counts, up to 4n-2 diagonals;
        # account capacity rather than only used nnz/diagonals.
        hilbert = max(16*dimension**2, 48*dimension**2+8*(dimension+1),
                      16*dimension*(4*dimension-2)+8*(4*dimension-2)) + 16384
        liouville_size = dimension**2
        liouville = max(16*liouville_size**2, 48*liouville_size**2+8*(liouville_size+1),
                       16*liouville_size*(4*liouville_size-2)+8*(4*liouville_size-2)) + 16384
        # _coefficient_segments uses QobjEvo(copy=True) for every retained piece.
        # HC terms copy the adjoint too; this is not just coefficient storage.
        drive_owners = sum((1+int(drive.hc))*_operator_storage_bytes(drive.operator, self.model.modes) for drive in drives)
        retained_terms = segments*(hilbert+drive_owners) if drives else hilbert
        state_elements = (dimension**2 if self.solver == 'me' else dimension)
        if self.operation == 'propagator':
            state_elements = dimension**4 if self.solver == 'me' else dimension**2
        state = 16*state_elements
        solver_generators = 0
        rk_workspace = 0
        static_workspace = 0
        if self.operation in ('evolve', 'propagator'):
            # explicit_rk.set_initial_value: k[16], y, temp/front/prev, optional
            # FSAL; solver copies/returned state and validation scratch coexist.
            rk_workspace = (16+1+3+1+3+4)*state
            generator = liouville if self.solver == 'me' else hilbert
            # The generator owns its terms and tensor/add conversion scratch.
            # QobjEvo liouvillian retains spre(H) and spost(H.dag()) terms
            # plus the summed dissipator; four conversion/add operands overlap.
            generator_terms = (2*sum((1+int(drive.hc))*_operator_storage_bytes(
                drive.operator, self.model.modes, superoperator=True) for drive in drives)
                if self.solver == 'me' else drive_owners)
            solver_generators = generator_terms+5*generator
            if self.solver == 'mc':
                # MCSolver c_ops, n_ops and effective-Hamiltonian products.
                solver_generators += (2*collapse+4)*hilbert
            if self.operation == 'evolve' and self.solver in ('se', 'me'):
                # The existing options feedback cycle can retain prior solvers;
                # do not assume Python cyclic GC releases a native workspace.
                rk_workspace *= segments
                solver_generators *= segments
        elif self.operation == 'spectrum':
            n = dimension
            # _static_spectrum/verification/Fortran input and HEEVD outputs.
            # Frozen HEEVD work-query bounds include vectors and eigenvalues-only.
            static_workspace = (5*16*n*n + 16*(n*n+33*n+1)
                                + 8*(2*n*n+5*n+1) + 8*(5*n+3))
        else:
            n = liouville_size
            # SVD values-only: dense input/copy, complex work<=66n, real 7n,
            # integer 8n; direct solve adds trace-row/dense copy and GESV LU.
            static_workspace = (4*liouville + 5*16*n*n + 16*66*n
                                + 8*7*n + 8*8*n + 16*5*n)
        encoded_bytes = len(json.dumps(canonical, allow_nan=False).encode())
        request_bytes = _request_storage_bytes(canonical)
        base64_bytes = 4*((result.npz_upper_bytes+2)//3)
        items = (
            ('native_import_reserve', 512*1024**2),
            ('request_models_canonical_and_child_handoff', 4*request_bytes+5*4*encoded_bytes),
            ('named_and_local_matrix_conversion', (len(self.model.matrices)+len(self.model.modes)+4)*hilbert),
            ('static_hamiltonian_build_temporaries', 10*hilbert),
            ('observables_collapse_and_drive_operators', (observable+collapse+channels+4)*hilbert),
            ('retained_segment_qobjevo_terms', retained_terms),
            # Each channel retains its complex values and order-one polynomial;
            # conjugation, knot/slope and carrier construction temporaries overlap.
            ('coefficient_values_polynomials_and_temporaries',
             (16*6*channels+8*6)*65536 if drives else 0),
            ('coefficient_segment_python_owners', segments*(1024+(channels+1)*512) if drives else 0),
            ('native_solver_generators_and_conversion', solver_generators),
            ('vern7_state_stage_and_validation_owners', rk_workspace),
            ('static_lapack_and_direct_solver_owners', static_workspace),
            # SciPy 1.18.1 matrix_exponential_z: Am[6*n*n+4*n], pivot[n],
            # input and output. Matrix representation slack covers pivots/Qobj.
            ('initial_and_validation_state_temporaries', 8*hilbert),
            ('numeric_result_arrays_and_copy_overlap', 2*result.numeric_bytes),
            ('metadata_unicode_utf8_and_scalar', 3*4*65536+65536),
            ('npz_serialization_buffers', 4*result.npz_upper_bytes),
            ('base64_bytes_and_ascii_string', 2*base64_bytes),
            ('callback_json_unicode_and_utf8', 5*result.callback_upper_bytes),
            ('parent_child_result_handoff', 2*result.callback_upper_bytes),
        )
        peak = sum(value for _, value in items)
        if peak > 2*1024**3:
            _error('/model', 'certified Lindblad allocation exceeds the CPU budget', 'resource_limit')
        algorithm = ({'spectrum': 'scipy-zheevd-spectrum',
                      'steadystate': 'svd-unique-qutip-dense-solve'}.get(self.operation)
                     or 'qutip-vern7-'+self.operation+'-'+self.solver)
        return AllocationPlan('lindblad-cpu-vern7-evd-svd-v1', algorithm,
            {'hilbert': dimension, 'coefficient_segments': segments,
             'coefficient_channels': channels, 'native_state_elements': state_elements}, items, peak,
            {'runtime_files': [dict(item) for item in _LINDBLAD_RUNTIME_FILES],
             'rk_stage_buffers': 16, 'rk_state_and_scratch_buffers': 12,
             'heevd_complex_work_bound': 'n*n+33*n+1',
             'heevd_real_work_bound': '2*n*n+5*n+1',
             'heevd_integer_work_bound': '5*n+3',
             'gesdd_values_only_complex_work_bound': '66*n',
             'owner_source_revision': 'qutip-5.3.1-vern7-segment-owners-v1'}, result)
