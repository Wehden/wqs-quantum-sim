"""Strict scqubits schema and API-safe, side-effect-free admission checks."""
import math
from typing import Annotated, Literal, TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .physics_results import AllocationPlan


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
        dimension = self._physical_checks()
        self._result_plan(dimension)
        raise ValueError('resource_limit: frozen scqubits native allocation certificate is unavailable')
