"""Strict scqubits schema and API-safe, side-effect-free admission checks."""
import math
from typing import Annotated, Literal, TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .physics_results import AllocationPlan


# Frozen Linux CPython 3.12 x86_64 implementation; workers verify every file.
_CPU_CERTIFICATE = {'algorithm': 'scipy-dsyevr-zheevr-subset',
 'digest': '531974791bde44c9f6bb29e85fe042e785e1315326e8c0288c788ad38ab4991d',
 'lapack_workspace': {'dsyevr': [33, 10], 'zheevr': [33, 24, 10]},
 'native_hashes': {'numpy.libs/libscipy_openblas64_-f48b354e.so': {'sha256': '432e1c1739731468ba305771af96773939e43016643e2d630f58cb45b6670e96',
                                                                   'size': 24325657},
                   'numpy/_core/_multiarray_umath.cpython-312-x86_64-linux-gnu.so': {'sha256': 'b185dfe64b07180bedc7c15fb2735b0d0f4f4e09eb9c15bd8e67f74568aebcbf',
                                                                                     'size': 10637297},
                   'numpy/linalg/_umath_linalg.cpython-312-x86_64-linux-gnu.so': {'sha256': '2e075f582c3a25a21fe97a55931a0fdb22ceb84f4fce8203a5040d405ccf65ce',
                                                                                  'size': 235897},
                   'scipy.libs/libscipy_openblas-5f890258.so': {'sha256': '2a5b4c1a6311873c59132b2ceaaf3c26258a17350b7cd27ea8782a47e3322981',
                                                                'size': 25094609},
                   'scipy/linalg/_flapack.cpython-312-x86_64-linux-gnu.so': {'sha256': '00fc3e3414d7f42f023d5ba1f1b616d3d81da8da0f5cc6b17279af70be963b9c',
                                                                             'size': 2572905},
                   'scipy/linalg/_internal_matfuncs.cpython-312-x86_64-linux-gnu.so': {'sha256': 'e98f03e852b7acf316c6a6d2294360ed2fb98ee4b1e9c8eccffd123f9c1dc74f',
                                                                                       'size': 182105}},
 'policy_revision': 'scqubits-cpu-evr-v1',
 'source_hashes': {'numpy/_core/numeric.py': {'sha256': 'feb150554b4879d4df7bd0a4ab7b1a6b818de7ba994ed0c73583fee177533417',
                                              'size': 80583},
                   'numpy/lib/_shape_base_impl.py': {'sha256': 'b550d0cc5e7efe46dc1c77f0286330fcb4100fcbbeb98f41aa9127c2c44e01ec',
                                                     'size': 37860},
                   'numpy/lib/_twodim_base_impl.py': {'sha256': 'a11d0bdecd5064dae7aca1ecbc3fdb9ff0eacae4cdfa8ed1a644ac0bb4a58fe6',
                                                      'size': 35242},
                   'numpy/linalg/_linalg.py': {'sha256': '99ec3bec6f80194741a62407fbcea521a0690577105fe9f3096f156842de76a9',
                                               'size': 115382},
                   'scipy/linalg/_decomp.py': {'sha256': '4dbf81305777e4bfd7e6df834e411aa8751b715a7d957c6f42b70808cb0b6a2f',
                                               'size': 65295},
                   'scipy/linalg/_matfuncs.py': {'sha256': '72da5780b458ecf9f78fd75662fa28c95cb14b66c4faf1327891be009830a1b6',
                                                 'size': 26540},
                   'scqubits/core/diag.py': {'sha256': '11d283c199721534032a9f0d1b8094471b849f0648a72d74b61fcb8c8dcee13b',
                                             'size': 23862},
                   'scqubits/core/flux_qubit.py': {'sha256': 'eaf48a76287c06fe59a739e378b974d58b9540d819c4c4b1fec82d723de25dfe',
                                                   'size': 39273},
                   'scqubits/core/fluxonium.py': {'sha256': 'cc3133e4c704aa11de3b112365958aecef669537d5d27d252daca7edb0b754d3',
                                                  'size': 19184},
                   'scqubits/core/operators.py': {'sha256': '46cfb9bd9d89c05131418d99fbd6ac3b91e7fac9d4b57687c1e814e5ca9148fe',
                                                  'size': 7916},
                   'scqubits/core/qubit_base.py': {'sha256': '30b28e50488080fdab59976e47ee3cf2005c3174dfd011a09e1cc85871d71c6f',
                                                   'size': 47458},
                   'scqubits/core/transmon.py': {'sha256': '5e6ecc5e9230734cb6a68c3b4edb67c8dcb76dd41e4b6e8957020c1459bbe30c',
                                                 'size': 32375}},
 'versions': {'numpy': '2.5.4', 'qutip': '5.3.1', 'scipy': '1.18.1', 'scqubits': '4.3.1'}}


def _finite(value):
    if type(value) not in (int, float):
        raise ValueError("expected a finite JSON number")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ValueError("expected a representable finite number") from None
    if not math.isfinite(value):
        raise ValueError("expected a finite number")
    return 0.0 if value == 0 else value


_Number = Annotated[float, BeforeValidator(_finite)]
_Operator = Literal['n_operator', 'phi_operator', 'cos_phi_operator', 'sin_phi_operator',
                    'n_1_operator', 'n_2_operator', 'cos_phi_1_operator', 'cos_phi_2_operator',
                    'sin_phi_1_operator', 'sin_phi_2_operator']
_OPERATORS = {
    'Transmon': ('n_operator', 'cos_phi_operator', 'sin_phi_operator'),
    'TunableTransmon': ('n_operator', 'cos_phi_operator', 'sin_phi_operator'),
    'Fluxonium': ('n_operator', 'phi_operator', 'cos_phi_operator', 'sin_phi_operator'),
    'FluxQubit': ('n_1_operator', 'n_2_operator', 'cos_phi_1_operator', 'cos_phi_2_operator',
                  'sin_phi_1_operator', 'sin_phi_2_operator'),
}


class _TransmonParams(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    EJ: _Number
    EC: _Number
    ng: _Number
    ncut: int


class _TunableParams(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    EJmax: _Number
    EC: _Number
    d: _Number
    flux: _Number
    ng: _Number
    ncut: int


class _FluxoniumParams(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    EJ: _Number
    EC: _Number
    EL: _Number
    flux: _Number
    cutoff: int


class _FluxQubitParams(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    EJ1: _Number
    EJ2: _Number
    EJ3: _Number
    ECJ1: _Number
    ECJ2: _Number
    ECJ3: _Number
    ECg1: _Number
    ECg2: _Number
    ng1: _Number
    ng2: _Number
    flux: _Number
    ncut: int


class _Transmon(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    qubit: Literal['Transmon']
    params: _TransmonParams


class _Tunable(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    qubit: Literal['TunableTransmon']
    params: _TunableParams


class _Fluxonium(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    qubit: Literal['Fluxonium']
    params: _FluxoniumParams


class _FluxQubit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    qubit: Literal['FluxQubit']
    params: _FluxQubitParams


def _operation_schema(schema):
    branches = []
    for operation, allowed, required in (
        ('spectrum', {'return_vectors'}, []),
        ('matrix_elements', {'operator'}, ['operator']),
        ('export_for_lindblad', {'operators'}, ['operators']),
    ):
        branch = {'properties': {'operation': {'const': operation}}, 'required': ['operation', *required]}
        forbidden = {'operator', 'operators', 'return_vectors'} - allowed
        branch['not'] = {'anyOf': [{'required': [field]} for field in sorted(forbidden)]}
        if operation == 'matrix_elements':
            branch['properties']['operator'] = {'type': 'string'}
        if operation == 'export_for_lindblad':
            branch['properties']['operators'] = {'type': 'array', 'uniqueItems': True}
        branches.append(branch)
    schema['oneOf'] = branches


class ScqubitsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, json_schema_extra=_operation_schema)
    engine: Literal['scqubits'] = 'scqubits'
    input_format: Literal['scqubits_model'] = 'scqubits_model'
    schema_version: Annotated[int, Field(ge=1, le=1, json_schema_extra={'const': 1})]
    name: str = Field(default='Untitled simulation', min_length=1, max_length=80)
    operation: Literal['spectrum', 'matrix_elements', 'export_for_lindblad']
    model: Annotated[_Transmon | _Tunable | _Fluxonium | _FluxQubit, Field(discriminator='qubit')]
    evals_count: int = 6
    return_vectors: bool = False
    operator: _Operator | None = None
    operators: list[_Operator] | None = Field(default=None, max_length=6)

    @model_validator(mode='after')
    def _operation_fields(self):
        allowed = {'spectrum': {'return_vectors'}, 'matrix_elements': {'operator'},
                   'export_for_lindblad': {'operators'}}[self.operation]
        if self.model_fields_set & ({'operator', 'operators', 'return_vectors'} - allowed):
            raise ValueError('operation does not accept these output controls')
        if self.operation == 'matrix_elements' and self.operator is None:
            raise ValueError('matrix_elements requires operator')
        if self.operation == 'export_for_lindblad' and self.operators is None:
            raise ValueError('export_for_lindblad requires operators')
        selected = [self.operator] if self.operator is not None else self.operators or []
        if len(selected) != len(set(selected)):
            raise ValueError('operators must be distinct and ordered')
        if any(operator not in _OPERATORS[self.model.qubit] for operator in selected):
            raise ValueError('operator is not supported for this qubit class')
        return self

    def canonical_envelope(self) -> dict:
        excluded = {'operator', 'operators', 'return_vectors'} - {
            'spectrum': {'return_vectors'}, 'matrix_elements': {'operator'},
            'export_for_lindblad': {'operators'},
        }[self.operation]
        return self.model_dump(mode='json', exclude=excluded)

    def _physical_checks(self) -> int:
        params = self.model.params.model_dump()
        cutoff = params.get('ncut', params.get('cutoff'))
        if cutoff < 1:
            raise ValueError('physical_validation: cutoff must be a positive integer')
        dimension = cutoff if self.model.qubit == 'Fluxonium' else 2 * cutoff + 1
        if self.model.qubit == 'FluxQubit':
            dimension *= dimension
        if dimension > 512 or not 1 <= self.evals_count <= min(32, dimension):
            raise ValueError('resource_limit: raw basis or requested levels exceed the CPU envelope')
        positive = [name for name in params if name.startswith('EC') or name == 'EL']
        if any(params[name] <= 0 for name in positive):
            raise ValueError('physical_validation: charging and inductive energies must be positive')
        if any(value < 0 for name, value in params.items() if name.startswith('EJ')):
            raise ValueError('physical_validation: Josephson energies must be nonnegative')
        if 'd' in params and not 0 <= params['d'] <= 1:
            raise ValueError('physical_validation: asymmetry must lie in [0, 1]')
        derived = []
        try:
            derived.extend(1 / params[name] for name in positive)
            if self.model.qubit == 'FluxQubit':
                derived.extend(2 * params[name] for name in positive)
                inv = {name: 1 / params[name] for name in positive}
                capacitance = np.array([[inv['ECJ1'] + inv['ECg1'] + inv['ECJ3'], -inv['ECJ3']],
                                        [-inv['ECJ3'], inv['ECJ2'] + inv['ECg2'] + inv['ECJ3']]])
                if not np.isfinite(capacitance).all():
                    raise ValueError('physical_validation: nonfinite capacitance')
                scale = float(np.max(np.abs(capacitance)))
                normalized = capacitance / scale
                eigenvalues = np.linalg.eigvalsh(normalized)
                if eigenvalues[0] <= 0 or eigenvalues[0] / eigenvalues[-1] < math.sqrt(np.finfo(float).eps):
                    raise ValueError('physical_validation: ill-conditioned capacitance')
                inverse = np.linalg.inv(normalized) / scale
                if not np.isfinite(inverse).all() or np.linalg.norm(capacitance @ inverse - np.eye(2)) > 1e-10:
                    raise ValueError('physical_validation: unstable capacitance inverse')
                extent = cutoff + max(abs(params['ng1']), abs(params['ng2']))
                derived.append(16 * float(np.max(np.abs(inverse))) * extent * extent)
            else:
                if 'ng' in params:
                    extent = cutoff + abs(params['ng'])
                    derived.append(4 * params['EC'] * extent * extent)
                if self.model.qubit == 'Fluxonium':
                    plasma_squared = 8 * params['EL'] * params['EC']
                    if plasma_squared == 0 or not math.isfinite(plasma_squared):
                        raise ValueError('physical_validation: unrepresentable derived plasma energy')
                    derived.extend((8 * params['EC'] / params['EL'], params['EL'] / params['EC'],
                                    math.sqrt(params['EC']) * math.sqrt(params['EL']) * cutoff,
                                    params['EL'] * (2 * math.pi * params['flux']) ** 2))
            if 'flux' in params:
                derived.append(2 * math.pi * params['flux'])
            josephson_bound = math.fsum(value for name, value in params.items() if name.startswith('EJ'))
            derived.append(2 * math.pi * josephson_bound)
        except (OverflowError, ZeroDivisionError, np.linalg.LinAlgError):
            raise ValueError('physical_validation: unrepresentable derived quantity') from None
        if any(not math.isfinite(value) or value == 0 for value in (1 / params[name] for name in positive)):
            raise ValueError('physical_validation: unrepresentable reciprocal energy')
        if any(not math.isfinite(value) for value in derived):
            raise ValueError('physical_validation: nonfinite derived quantity')
        return dimension

    def _result_plan(self, dimension):
        from .physics_results import ArraySpec, plan_result
        levels = self.evals_count
        arrays = [ArraySpec('eigenvalues', 'float64', (levels,), 'GHz')]
        if self.operation == 'spectrum' and self.return_vectors:
            arrays.append(ArraySpec('eigenvectors', 'complex128', (dimension, levels), None))
        if self.operation == 'matrix_elements':
            arrays.append(ArraySpec('matrix_elements', 'complex128', (levels, levels), None))
        if self.operation == 'export_for_lindblad':
            arrays.append(ArraySpec('hamiltonian', 'complex128', (levels, levels), 'GHz'))
            arrays.extend(ArraySpec(name, 'complex128', (levels, levels), None) for name in self.operators)
            # Each complex entry has two real components, each reserved at 40
            # ASCII characters plus comma/row framing. The fixed portion covers
            # schema tags, units, names, basis and the removed ground energy.
            matrix_bytes = 2 * (levels * (levels * 41 + 2) + 2)
            export_bound = 4096 + (1 + len(self.operators)) * (matrix_bytes + 128)
            if export_bound > 65536:
                raise ValueError('resource_limit: inline export exceeds the metadata envelope')
        return plan_result(arrays, metadata_characters=65536, summary_bytes=65536)

    def admission_plan(self) -> 'AllocationPlan':
        from .physics_results import AllocationPlan
        dimension = self._physical_checks()
        result = self._result_plan(dimension)
        if self.operation == 'export_for_lindblad':
            raise ValueError('resource_limit: frozen scqubits export allocation certificate is unavailable')
        levels = self.evals_count
        square = dimension * dimension
        raw_itemsize = 16 if self.model.qubit == 'FluxQubit' else 8
        # Sum the two native Hamiltonian constructions, including temporaries,
        # rather than assuming either Hamiltonian reuses the validation buffer.
        if self.model.qubit in ('Transmon', 'TunableTransmon'):
            constructor = 8*square + 32*dimension
            operator = 56*square + 8*dimension
        elif self.model.qubit == 'Fluxonium':
            # LC diagonal; five phi buffers; four argument buffers; two final
            # real buffers. Complex exponential: input, output and six powers.
            constructor = 12*8*square + 8*16*square + 196*dimension
            operator = 9*8*square + 8*16*square + 188*dimension
        else:
            # Three kinetic terms (six full matrices), four potential terms
            # (ten), and their sum. Small tensor factors/indices are separate.
            constructor = 17*16*square + 736*dimension + 96
            operator = 4*16*square + 40*dimension
        complex_solver = self.model.qubit == 'FluxQubit'
        if complex_solver:
            lwork, lrwork, liwork = _CPU_CERTIFICATE['lapack_workspace']['zheevr']
            workspace = (16*lwork + 8*lrwork + 4*liwork)*dimension
        else:
            lwork, liwork = _CPU_CERTIFICATE['lapack_workspace']['dsyevr']
            workspace = (8*lwork + 4*liwork)*dimension
        base64_bytes = 4*((result.npz_upper_bytes+2)//3)
        items = (
            ('import_native_headroom', 512*1024**2),
            ('two_hamiltonian_constructors', 2*constructor),
            ('operator_constructor', operator if self.operation == 'matrix_elements' else 0),
            ('hamiltonian_scaled_copy', raw_itemsize*square),
            ('hamiltonian_absolute_values', 8*square),
            ('hamiltonian_conjugate', raw_itemsize*square),
            ('hamiltonian_hermiticity_difference', raw_itemsize*square),
            ('hamiltonian_hermiticity_absolute_and_rhs', 2*8*square),
            ('hamiltonian_finite_and_comparison_masks', 10*square),
            ('evr_fortran_matrix_copy', raw_itemsize*square),
            ('evr_queried_workspace', workspace),
            ('evr_native_eigenpairs_and_support', raw_itemsize*dimension*levels + 16*dimension),
            ('ordered_eigenpair_copies', (2*raw_itemsize+16)*dimension*levels + 24*levels),
            ('residual_and_orthogonality_arrays',
             72*dimension*levels + 48*levels*levels + 16*levels + 8*dimension),
            ('operator_validation_arrays', 106*square if self.operation == 'matrix_elements' else 0),
            ('operator_transform_arrays',
             32*dimension*levels + 16*levels*levels if self.operation == 'matrix_elements' else 0),
            ('numeric_result_arrays', result.numeric_bytes),
            ('metadata_unicode_utf8_and_scalar', 3*4*65536 + 65536),
            ('npz_serialization_buffers', 4*result.npz_upper_bytes),
            ('base64_bytes_and_ascii_string', 2*base64_bytes),
            ('callback_json_unicode_and_utf8', 5*result.callback_upper_bytes),
            ('parent_child_handoff', 2*result.callback_upper_bytes),
        )
        peak = sum(size for _, size in items)
        if peak > 2*1024**3:
            raise ValueError('resource_limit: certified scqubits allocation exceeds the CPU budget')
        return AllocationPlan(_CPU_CERTIFICATE['policy_revision'], _CPU_CERTIFICATE['algorithm'],
                              {'raw_basis': dimension, 'levels': levels}, items, peak,
                              {'certificate': _CPU_CERTIFICATE['digest']}, result)
