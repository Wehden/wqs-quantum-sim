"""Solver-free physics normalization, canonical identity and admission."""
from collections.abc import Mapping
from dataclasses import dataclass
import json

from pydantic import ValidationError
from . import settings
from .lindblad_models import LindbladRequest
from .scqubits_models import ScqubitsRequest
from .josim_models import JoSIMRequest
from .physics_results import AllocationPlan, make_execution_context

__all__ = ('NormalizedPhysicsRequest', 'ValidatedPhysicsRequest', 'normalize_physics_submission',
           'admit_physics_request', 'canonical_physics_request', 'physics_schema', 'physics_capabilities')

_MODELS = {'lindblad': LindbladRequest, 'scqubits': ScqubitsRequest, 'josim': JoSIMRequest}
_FORMATS = {'lindblad': 'lindblad_model', 'scqubits': 'scqubits_model', 'josim': 'josim_netlist'}


@dataclass(frozen=True)
class NormalizedPhysicsRequest:
    request: LindbladRequest | ScqubitsRequest | JoSIMRequest
    canonical_envelope: Mapping


@dataclass(frozen=True)
class ValidatedPhysicsRequest:
    normalized: NormalizedPhysicsRequest
    allocation_plan: AllocationPlan
    dispatch_context: Mapping


def _physics_validation_error(error, payload=None):
    """Retain bounded validation fields and RFC 6901 paths, never input/tracebacks."""
    details = []
    items = error.errors(include_url=False, include_input=False) if isinstance(error, ValidationError) else [
        {'type': 'value_error', 'loc': (), 'msg': str(error)}]
    for item in items:
        loc = list(item['loc'])
        current = payload
        path = []
        for index, part in enumerate(loc):
            # Discriminated-union tags are Pydantic locations, not JSON keys.
            if (isinstance(current, dict) and part not in current and index+1 < len(loc)
                    and loc[index+1] in current):
                continue
            path.append(str(part).replace('~', '~0').replace('/', '~1'))
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
                current = current[part]
            else:
                current = None
        pointer = getattr(item.get('ctx',{}).get('error'),'pointer',
                          getattr(error,'pointer','/'+'/'.join(path) if path else ''))
        details.append({'type': item['type'], 'loc': ['body', *loc],
                        'msg': item['msg'], 'pointer': pointer})
    result = ValueError('invalid physics request')
    result.physics_errors = details
    return result


def normalize_physics_submission(payload: Mapping, *, explicit_engine: str | None = None) -> NormalizedPhysicsRequest:
    if not isinstance(payload, Mapping):
        raise _physics_validation_error(ValueError('physics request must be a JSON object'))
    body = dict(payload)
    engine = explicit_engine or body.get('engine')
    if engine is None:
        engine = next((name for name, fmt in _FORMATS.items() if fmt == body.get('input_format')), None)
    if not isinstance(engine, str) or engine not in _MODELS:
        raise _physics_validation_error(ValueError('unsupported physics engine'), body)
    body.setdefault('engine', engine)
    body.setdefault('input_format', _FORMATS[engine])
    try:
        request = _MODELS[engine].model_validate(body)
        envelope = request.canonical_envelope()
        # JSON identity never accepts nonfinite values, including nested inputs.
        json.dumps(envelope, allow_nan=False)
    except (ValidationError, ValueError) as error:
        raise _physics_validation_error(error, body) from None
    return NormalizedPhysicsRequest(request, envelope)


def canonical_physics_request(normalized: NormalizedPhysicsRequest) -> str:
    return json.dumps(normalized.canonical_envelope, sort_keys=True, separators=(',', ':'), allow_nan=False)


def admit_physics_request(normalized: NormalizedPhysicsRequest, *, policy: Mapping) -> ValidatedPhysicsRequest:
    try:
        request = normalized.request
        plan = request.admission_plan()
        if policy.get(request.engine, {}).get(request.operation) != plan.policy_revision:
            raise ValueError('resource_limit: physics operation is not enabled by server policy')
        profile = settings.physics_execution_profile(request.engine, request.operation,
                                                      admission_policy_revision=plan.policy_revision)
    except ValueError as error:
        raise _physics_validation_error(error, normalized.canonical_envelope) from None
    context = make_execution_context(plan,profile)
    return ValidatedPhysicsRequest(normalized, plan, context)


def physics_schema(engine: str, schema_version: int) -> dict:
    if engine not in _MODELS or type(schema_version) is not int or schema_version != 1:
        raise ValueError('unknown physics schema')
    schema = _MODELS[engine].model_json_schema()
    schema['$id'] = '/api/schemas/'+engine+'/1.json'
    schema['description'] = ('Structural schema v1. Semantic physical, allocation and native-certificate '
                             'validation also applies; schema validity does not imply admission. '
                             + schema.get('description', ''))
    return schema


def physics_capabilities(enabled_certificates: Mapping) -> Mapping[str, dict]:
    result = {}
    for engine, operations in enabled_certificates.items():
        enabled = []
        for operation, revision in operations.items():
            try:
                settings.physics_execution_profile(engine,operation,admission_policy_revision=revision)
            except ValueError:
                continue
            enabled.append(operation)
        if enabled:
            result[engine] = {'version': {'lindblad':'5.3.1','scqubits':'4.3.1','josim':'2.7'}[engine],
                             'default': False, 'input_formats': {_FORMATS[engine]: enabled},
                             'device': 'CPU', 'precision': 'float64/complex128',
                             'schema': '/api/schemas/'+engine+'/1.json'}
    return result
