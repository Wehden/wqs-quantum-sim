"""Strict request and fail-closed admission coverage for the physics extension."""
import copy
import json
import os
import subprocess
import sys

import jsonschema
import pytest
from pydantic import ValidationError

from qsim.scqubits_models import ScqubitsRequest


PARAMETERS = {
    'Transmon': {'EJ': 15., 'EC': .3, 'ng': 0., 'ncut': 30},
    'TunableTransmon': {'EJmax': 20., 'EC': .3, 'd': .01, 'flux': .2, 'ng': 0., 'ncut': 30},
    'Fluxonium': {'EJ': 8.9, 'EC': 2.5, 'EL': .5, 'flux': .3, 'cutoff': 110},
    'FluxQubit': {'EJ1': 1., 'EJ2': 1., 'EJ3': .8, 'ECJ1': .016, 'ECJ2': .016,
                   'ECJ3': .021, 'ECg1': .83, 'ECg2': .83, 'ng1': 0., 'ng2': 0.,
                   'flux': .4, 'ncut': 5},
}
OPERATORS = {
    'Transmon': ['n_operator', 'cos_phi_operator', 'sin_phi_operator'],
    'TunableTransmon': ['n_operator', 'cos_phi_operator', 'sin_phi_operator'],
    'Fluxonium': ['n_operator', 'phi_operator', 'cos_phi_operator', 'sin_phi_operator'],
    'FluxQubit': ['n_1_operator', 'n_2_operator', 'cos_phi_1_operator', 'cos_phi_2_operator',
                  'sin_phi_1_operator', 'sin_phi_2_operator'],
}


def payload(qubit='Transmon', operation='spectrum'):
    body = {'schema_version': 1, 'operation': operation,
            'model': {'qubit': qubit, 'params': copy.deepcopy(PARAMETERS[qubit])}}
    if operation == 'matrix_elements':
        body['operator'] = OPERATORS[qubit][0]
    if operation == 'export_for_lindblad':
        body['operators'] = [OPERATORS[qubit][0]]
    return body


@pytest.mark.parametrize('qubit', PARAMETERS)
@pytest.mark.parametrize('operation', ['spectrum', 'matrix_elements', 'export_for_lindblad'])
def test_all_class_operation_schemas_and_defaults(qubit, operation):
    body = payload(qubit, operation)
    request = ScqubitsRequest(**body)
    assert request._physical_checks() == {'Transmon': 61, 'TunableTransmon': 61,
                                          'Fluxonium': 110, 'FluxQubit': 121}[qubit]
    canonical = request.canonical_envelope()
    assert canonical['engine'] == 'scqubits'
    assert canonical['input_format'] == 'scqubits_model'
    assert canonical['name'] == 'Untitled simulation'
    assert canonical['evals_count'] == 6
    assert (canonical.get('return_vectors') is False) == (operation == 'spectrum')
    assert ScqubitsRequest(**canonical).canonical_envelope() == canonical
    jsonschema.validate(body, ScqubitsRequest.model_json_schema())
    jsonschema.validate(canonical, ScqubitsRequest.model_json_schema())
    with pytest.raises(ValueError, match='resource_limit: frozen scqubits'):
        request.admission_plan()


@pytest.mark.parametrize('qubit,operator', [(q, o) for q, names in OPERATORS.items() for o in names])
def test_all_allowlisted_operators(qubit, operator):
    body = payload(qubit, 'matrix_elements')
    body['operator'] = operator
    assert ScqubitsRequest(**body).operator == operator


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_unsupported_class_operator_pairs(qubit):
    body = payload(qubit, 'matrix_elements')
    body['operator'] = 'phi_operator' if qubit != 'Fluxonium' else 'n_1_operator'
    with pytest.raises(ValidationError, match='not supported'):
        ScqubitsRequest(**body)


@pytest.mark.parametrize('field,value', [('schema_version', True), ('schema_version', 1.),
    ('schema_version', '1'), ('schema_version', 2), ('evals_count', True),
    ('evals_count', 6.), ('return_vectors', 0), ('engine', 'qsim'), ('input_format', 'qasm2'),
    ('name', ''), ('name', 'x' * 81), ('operation', 'evolve')])
def test_strict_envelope_types(field, value):
    body = payload()
    body[field] = value
    with pytest.raises(ValidationError):
        ScqubitsRequest(**body)


@pytest.mark.parametrize('qubit', PARAMETERS)
@pytest.mark.parametrize('value', [True, '0.3', None, float('nan'), float('inf'), -float('inf'), 10**1000])
def test_finite_numbers_no_coercion(qubit, value):
    body = payload(qubit)
    field = next(iter(body['model']['params']))
    body['model']['params'][field] = value
    with pytest.raises(ValidationError):
        ScqubitsRequest(**body)


@pytest.mark.parametrize('path,field,value', [([], 'shots', 1), ([], 'seed', 42),
    ([], 'backend', 'cupy'), ([], 'resources', {}), ([], 'options', {}),
    (['model'], 'yaml', 'x'), (['model', 'params'], 'id_str', 'x'),
    (['model', 'params'], 'truncated_dim', 1), (['model', 'params'], 'evals_method', 'eval'),
    (['model', 'params'], 'esys_method_options', {}), (['model', 'params'], 'cutoff', 2)])
def test_unknown_fields_at_every_boundary(path, field, value):
    body = payload()
    target = body
    for component in path:
        target = target[component]
    target[field] = value
    with pytest.raises(ValidationError, match='Extra inputs'):
        ScqubitsRequest(**body)


@pytest.mark.parametrize('operation,field,value', [
    ('spectrum', 'operator', None), ('spectrum', 'operators', []),
    ('matrix_elements', 'return_vectors', False), ('matrix_elements', 'operators', []),
    ('export_for_lindblad', 'return_vectors', False), ('export_for_lindblad', 'operator', None)])
def test_inapplicable_controls_rejected_even_when_default(operation, field, value):
    body = payload(operation=operation)
    body[field] = value
    with pytest.raises(ValidationError):
        ScqubitsRequest(**body)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, ScqubitsRequest.model_json_schema())


@pytest.mark.parametrize('operation,field', [('matrix_elements', 'operator'), ('export_for_lindblad', 'operators')])
def test_required_operation_controls(operation, field):
    body = payload(operation=operation)
    del body[field]
    with pytest.raises(ValidationError):
        ScqubitsRequest(**body)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, ScqubitsRequest.model_json_schema())


def test_export_operator_order_and_duplicates():
    body = payload(operation='export_for_lindblad')
    body['operators'] = ['sin_phi_operator', 'n_operator']
    assert ScqubitsRequest(**body).canonical_envelope()['operators'] == body['operators']
    body['operators'] *= 2
    with pytest.raises(ValidationError, match='distinct'):
        ScqubitsRequest(**body)
    body['operators'] = []
    assert ScqubitsRequest(**body).operators == []


@pytest.mark.parametrize('qubit,boundary,rejected', [('Transmon', 255, 256),
    ('TunableTransmon', 255, 256), ('Fluxonium', 512, 513), ('FluxQubit', 10, 11)])
def test_full_basis_limit_cannot_be_hidden_by_small_output(qubit, boundary, rejected):
    body = payload(qubit)
    body['evals_count'] = 1
    field = 'cutoff' if qubit == 'Fluxonium' else 'ncut'
    body['model']['params'][field] = boundary
    assert ScqubitsRequest(**body)._physical_checks() <= 512
    body['model']['params'][field] = rejected
    request = ScqubitsRequest(**body)
    assert request.canonical_envelope()['model']['params'][field] == rejected
    with pytest.raises(ValueError, match='raw basis'):
        request.admission_plan()


@pytest.mark.parametrize('value', [0, -1, 33])
def test_requested_level_bounds_are_admission_only(value):
    request = ScqubitsRequest(**{**payload(), 'evals_count': value})
    with pytest.raises(ValueError, match='requested levels'):
        request.admission_plan()


@pytest.mark.parametrize('value', [True, 1., '1'])
def test_cutoff_integer_is_strict(value):
    body = payload()
    body['model']['params']['ncut'] = value
    with pytest.raises(ValidationError):
        ScqubitsRequest(**body)


@pytest.mark.parametrize('qubit,field,value,match', [
    ('Transmon', 'EC', 0., 'positive'), ('Transmon', 'EJ', -1., 'nonnegative'),
    ('Transmon', 'EC', 5e-324, 'reciprocal'), ('Transmon', 'ng', 1e308, 'derived'),
    ('Transmon', 'EJ', 1e308, 'derived'), ('TunableTransmon', 'd', 1.1, 'asymmetry'),
    ('Fluxonium', 'EL', 0., 'positive'), ('Fluxonium', 'EC', 1e308, 'derived'),
    ('Fluxonium', 'flux', 1e154, 'derived'),
    ('FluxQubit', 'ECg1', 0., 'positive'), ('FluxQubit', 'ECJ3', 1e-12, 'conditioned'),
    ('FluxQubit', 'ECJ1', 5e-324, 'capacitance'), ('FluxQubit', 'flux', 1e308, 'derived')])
def test_physical_and_derived_quantities_checked_after_normalization(qubit, field, value, match):
    body = payload(qubit)
    body['model']['params'][field] = value
    request = ScqubitsRequest(**body)
    assert request.canonical_envelope()['model']['params'][field] == value
    with pytest.raises(ValueError, match=match):
        request.admission_plan()


def test_signed_offsets_zero_josephson_and_no_arbitrary_ghz_ceiling():
    body = payload()
    body['model']['params'].update(EJ=0., ng=-.5)
    assert ScqubitsRequest(**body)._physical_checks() == 61
    body['model']['params'].update(EJ=1e12, EC=1e12)
    assert ScqubitsRequest(**body)._physical_checks() == 61
    body['model']['params']['ng'] = -0.
    canonical = ScqubitsRequest(**body).canonical_envelope()
    assert '"ng": -0.0' not in json.dumps(canonical)


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_result_shapes_and_uncertified_native_refusal(qubit):
    request = ScqubitsRequest(**{**payload(qubit), 'return_vectors': True})
    dimension = request._physical_checks()
    plan = request._result_plan(dimension)
    assert [(a.name, a.shape) for a in plan.arrays] == [('eigenvalues', (6,)), ('eigenvectors', (dimension, 6))]
    assert plan.numeric_bytes == 8 * 6 + 16 * dimension * 6
    assert plan.callback_upper_bytes < 12 * 1024**2
    with pytest.raises(ValueError, match='certificate is unavailable'):
        request.admission_plan()


def test_export_metadata_preflight_refuses_predictable_overflow():
    body = payload('FluxQubit', 'export_for_lindblad')
    body['operators'] = OPERATORS['FluxQubit']
    body['evals_count'] = 32
    request = ScqubitsRequest(**body)
    with pytest.raises(ValueError, match='inline export'):
        request.admission_plan()
    body['evals_count'] = 6
    request = ScqubitsRequest(**body)
    plan = request._result_plan(request._physical_checks())
    assert len(plan.arrays) == 8
    assert plan.numeric_bytes == 8 * 6 + 7 * 16 * 36


def test_module_import_remains_solver_and_lifecycle_free():
    code = '''
import sys
import qsim.scqubits_models
for prefix in ('scipy', 'qutip', 'scqubits', 'cupy', 'jax', 'sky', 'qsim.api', 'qsim.store', 'qsim.controller', 'qsim.worker'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
'''
    subprocess.run([sys.executable, '-c', code], check=True, env={**os.environ, 'PYTHONPATH': '.'})


@pytest.mark.parametrize('value,valid', [(1e-8, False), (1e-6, True)])
def test_capacitance_conditioning_boundary(value, valid):
    body = payload('FluxQubit')
    body['model']['params'].update(ECJ1=1., ECJ2=1., ECg1=1., ECg2=1., ECJ3=value)
    request = ScqubitsRequest(**body)
    if valid:
        assert request._physical_checks() == 121
    else:
        with pytest.raises(ValueError, match='ill-conditioned'):
            request._physical_checks()


def test_explicit_default_and_integer_float_parameters_share_canonical_identity():
    original = ScqubitsRequest(**payload()).canonical_envelope()
    body = payload()
    body.update(engine='scqubits', input_format='scqubits_model', name='Untitled simulation',
                evals_count=6, return_vectors=False)
    body['model']['params'].update(EJ=15, ng=0)
    assert ScqubitsRequest(**body).canonical_envelope() == original


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_every_native_constructor_parameter_is_explicit(qubit):
    for name in PARAMETERS[qubit]:
        body = payload(qubit)
        del body['model']['params'][name]
        with pytest.raises(ValidationError, match='Field required'):
            ScqubitsRequest(**body)
