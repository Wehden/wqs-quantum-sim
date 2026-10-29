"""Strict declarative Lindblad request boundary and bounded admission tests."""
import copy
import json
import subprocess
import sys

import jsonschema
import numpy as np
import pytest
from pydantic import ValidationError

from qsim.lindblad_models import LindbladRequest


def payload(operation='evolve', solver='se'):
    body = {'schema_version': 1, 'operation': operation,
            'model': {'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': 2}]}}
    if operation in ('evolve', 'propagator'):
        body.update(solver=solver, times=[0, 1])
    if operation == 'spectrum':
        body['evals_count'] = 2
    if operation == 'evolve' and solver == 'mc':
        body['model']['collapse'] = [{'kind': 'operator', 'operator': product('a'), 'rate': 1}]
    return body


def product(primitive='n', name='q'):
    return {'kind': 'product', 'factors': {name: primitive}}


def test_canonical_grid_defaults_and_explicit_route_identity():
    body = payload()
    body['times'] = {'start': 0, 'stop': 1, 'points': 3}
    request = LindbladRequest(**body)
    canonical = request.canonical_envelope()
    assert canonical['times'] == [0., .5, 1.]
    assert canonical['engine'] == 'lindblad'
    assert canonical['input_format'] == 'lindblad_model'
    assert canonical['store_states'] == 'none'
    assert LindbladRequest(**canonical).canonical_envelope() == canonical
    jsonschema.validate(body, LindbladRequest.model_json_schema())
    jsonschema.validate(canonical, LindbladRequest.model_json_schema())


@pytest.mark.parametrize('operation,solver', [('evolve', 'se'), ('evolve', 'me'),
    ('evolve', 'mc'), ('propagator', 'se'), ('propagator', 'me'),
    ('spectrum', None), ('steadystate', None)])
def test_all_operation_cells_are_staged_fail_closed(operation, solver):
    request = LindbladRequest(**payload(operation, solver))
    assert request._physical_checks() == 2
    with pytest.raises(ValueError, match='frozen Lindblad native allocation certificate'):
        request.admission_plan()


@pytest.mark.parametrize('operation,solver', [('evolve', 'se'), ('evolve', 'me'), ('evolve', 'mc'),
    ('propagator', 'se'), ('propagator', 'me'), ('spectrum', None), ('steadystate', None)])
def test_structural_schema_matches_each_operation(operation, solver):
    body = payload(operation, solver)
    schema = LindbladRequest.model_json_schema()
    jsonschema.validate(body, schema)
    request = LindbladRequest(**body)
    canonical = request.canonical_envelope()
    jsonschema.validate(canonical, schema)
    assert LindbladRequest(**canonical).canonical_envelope() == canonical


@pytest.mark.parametrize('field,value', [('schema_version', True), ('schema_version', 1.),
    ('schema_version', '1'), ('schema_version', 2), ('name', ''), ('name', 'a'*81),
    ('engine', 'qsim'), ('input_format', 'qasm2'), ('times', [0, True]),
    ('solver', None), ('times', None), ('store_states', True), ('store_states', 'sometimes'),
    ('resources', {'cpus': 4}), ('options', {'method': 'vern7'}), ('shots', 1)])
def test_strict_envelope(field, value):
    body = payload()
    body[field] = value
    with pytest.raises(ValidationError):
        LindbladRequest(**body)


@pytest.mark.parametrize('value', [True, '1', None, float('nan'), float('inf'), -float('inf'), 10**1000])
def test_finite_numeric_fields_are_not_coerced(value):
    body = payload()
    body['model']['modes'][0]['frequency'] = value
    with pytest.raises(ValidationError):
        LindbladRequest(**body)
    body = payload()
    body['model']['hamiltonian'] = [{'operator': product(), 'coefficient': value}]
    with pytest.raises(ValidationError):
        LindbladRequest(**body)


@pytest.mark.parametrize('operation,field,value', [('spectrum', 'times', [0, 1]),
    ('spectrum', 'initial_state', {'kind': 'product'}), ('spectrum', 'observables', []),
    ('spectrum', 'solver', 'se'), ('spectrum', 'return_state', False),
    ('steadystate', 'store_states', 'none'), ('steadystate', 'return_vectors', False),
    ('propagator', 'solver', 'mc'), ('propagator', 'observables', []),
    ('propagator', 'initial_state', {'kind': 'product'}), ('propagator', 'trajectories', 2),
    ('evolve', 'return_vectors', False), ('evolve', 'return_state', True),
    ('evolve', 'evals_count', 1), ('evolve', 'seed', 42), ('evolve', 'trajectories', 1)])
def test_inapplicable_controls_rejected_by_schema_and_model(operation, field, value):
    body = payload(operation)
    body[field] = value
    with pytest.raises(ValidationError):
        LindbladRequest(**body)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, LindbladRequest.model_json_schema())


def test_complex_defaults_and_generic_fields_canonicalize_identically():
    body = payload()
    body['model']['hamiltonian'] = [{'coefficient': 2, 'operator': product()}]
    first = LindbladRequest(**body).canonical_envelope()
    body.update(engine='lindblad', input_format='lindblad_model', name='Untitled simulation')
    body['model']['hamiltonian'][0].update(coefficient={'re': 2., 'im': -0.}, hc=False)
    assert LindbladRequest(**body).canonical_envelope() == first
    assert first['model']['hamiltonian'][0]['coefficient'] == {'re': 2., 'im': 0.}
    assert 'seed' not in first and 'trajectories' not in first


@pytest.mark.parametrize('solver', ['se', 'me', 'mc'])
@pytest.mark.parametrize('retention,retained', [('none', 0), ('final', 1), ('all', 3)])
def test_result_state_axes(solver, retention, retained):
    body = payload(solver=solver)
    body.update(times=[0, 1, 2], store_states=retention,
                observables=[{'name': 'occupation', 'operator': product()}])
    request = LindbladRequest(**body)
    result = request._result_plan(request._physical_checks())
    arrays = {item.name: item.shape for item in result.arrays}
    assert arrays['times'] == (3,)
    assert arrays['expectations'] == (3, 1)
    if retained:
        assert arrays['state_times'] == (retained,)
        assert arrays['states'] == ((retained, 2) if solver == 'se' else (retained, 2, 2))
    else:
        assert 'states' not in arrays and 'state_times' not in arrays


def test_stationary_observables_and_internal_density_independent_of_output():
    body = payload('steadystate')
    body.update(return_state=False, observables=[{'name': 'n', 'operator': product()}])
    request = LindbladRequest(**body)
    assert [(item.name, item.shape) for item in request._result_plan(2).arrays] == [('expectations', (1,))]
    with pytest.raises(ValueError, match='certificate'):
        request.admission_plan()


@pytest.mark.parametrize('solver,size', [('se', 2), ('me', 4)])
def test_propagator_explicit_representation(solver, size):
    request = LindbladRequest(**payload('propagator', solver))
    assert request._result_plan(2).arrays[1].shape == (2, size, size)


@pytest.mark.parametrize('value', [-1, 0, 257, 10**100])
def test_dimension_checked_before_numpy_allocation(monkeypatch, value):
    body = payload()
    body['model']['modes'][0]['dimension'] = value
    request = LindbladRequest(**body)
    monkeypatch.setattr(np, 'zeros', lambda *a, **k: pytest.fail('allocated before dimension refusal'))
    with pytest.raises(ValueError, match='resource_limit'):
        request._physical_checks()


def test_dimension_product_checked_before_arrays(monkeypatch):
    body = payload()
    body['model']['modes'] = [{'name': name, 'kind': 'oscillator', 'dimension': 17} for name in ['a', 'b']]
    request = LindbladRequest(**body)
    monkeypatch.setattr(np, 'zeros', lambda *a, **k: pytest.fail('allocated before dimension refusal'))
    with pytest.raises(ValueError, match='resource_limit'):
        request._physical_checks()


@pytest.mark.parametrize('solver', ['se', 'me', 'mc'])
def test_density_state_checks_and_solver_purity(solver):
    body = payload(solver=solver)
    body['initial_state'] = {'kind': 'density', 'values': [[.5, 0], [0, .5]]}
    request = LindbladRequest(**body)
    assert request.canonical_envelope()['initial_state']['values'][0][0] == {'re': .5, 'im': 0.}
    if solver == 'me':
        assert request._physical_checks() == 2
    else:
        with pytest.raises(ValueError, match='mixed state'):
            request._physical_checks()


@pytest.mark.parametrize('values,reason', [([[1.1, 0], [0, -.1]], 'positive semidefinite'),
    ([[.4, 0], [0, .4]], 'trace'), ([[1, 1], [0, 0]], 'Hermitian'),
    ([[1, 0]], 'shape')])
def test_invalid_density_states(values, reason):
    body = payload(solver='me')
    body['initial_state'] = {'kind': 'density', 'values': values}
    request = LindbladRequest(**body)
    with pytest.raises(ValueError, match=reason):
        request._physical_checks()


def test_density_boundary_is_validated_without_repair():
    body = payload(solver='me')
    body['initial_state'] = {'kind': 'density', 'values': [[1 + 5e-11, 0], [0, -5e-11]]}
    request = LindbladRequest(**body)
    before = copy.deepcopy(request.canonical_envelope())
    request._physical_checks()
    assert request.canonical_envelope() == before


@pytest.mark.parametrize('values', [[0, 0], [1], [0, 1, 0]])
def test_zero_or_wrong_amplitude_states(values):
    body = payload()
    body['initial_state'] = {'kind': 'amplitudes', 'values': values}
    with pytest.raises(ValueError, match='nonzero|shape'):
        LindbladRequest(**body)._physical_checks()


@pytest.mark.parametrize('values', [[2, 0], [{'re': 1e308, 'im': 1e308}, 0], [1e-320, 0]])
def test_pure_norm_checks_are_scale_safe(values):
    body = payload()
    body['initial_state'] = {'kind': 'amplitudes', 'values': values}
    assert LindbladRequest(**body)._physical_checks() == 2


@pytest.mark.parametrize('kind,factor', [('fock', {'index': 1}), ('coherent', {'amplitude': {'re': .3, 'im': .2}}),
    ('thermal', {'occupation': .1})])
def test_product_state_kinds(kind, factor):
    body = payload(solver='me')
    body['initial_state'] = {'kind': 'product', 'modes': {'q': {'kind': kind, **factor}}}
    assert LindbladRequest(**body)._physical_checks() == 2


@pytest.mark.parametrize('state,reason', [({'kind': 'fock', 'index': 2}, 'Fock index'),
    ({'kind': 'fock', 'index': -1}, 'Fock index'), ({'kind': 'thermal', 'occupation': -1}, 'occupation'),
    ({'kind': 'coherent', 'amplitude': 1e308}, 'coherent occupation')])
def test_invalid_product_factors(state, reason):
    body = payload(solver='me')
    body['initial_state'] = {'kind': 'product', 'modes': {'q': state}}
    with pytest.raises(ValueError, match=reason):
        LindbladRequest(**body)._physical_checks()


def test_escaped_pointer_for_mode_dictionary_key():
    body = payload()
    body['observables'] = [{'name': 'n', 'operator': product('n', 'bad~/mode')}]
    request = LindbladRequest(**body)
    with pytest.raises(ValueError) as caught:
        request._physical_checks()
    assert caught.value.pointer == '/observables/0/operator/factors/bad~0~1mode'


@pytest.mark.parametrize('primitive', ['a', 'adag', 'n', 'x', 'y', 'identity', 'proj:0,1'])
def test_operator_primitives_and_full_hc(primitive):
    body = payload()
    body['model']['hamiltonian'] = [{'operator': product(primitive), 'coefficient': {'re': 1, 'im': .2}, 'hc': True}]
    assert LindbladRequest(**body)._physical_checks() == 2


@pytest.mark.parametrize('primitive', ['a*a', 'qutip.destroy(2)', 'proj:2,0', 'proj:-1,0', 'proj:0,01', 'open(file)'])
def test_operator_allowlist_and_index_refusals(primitive):
    body = payload()
    body['model']['hamiltonian'] = [{'operator': product(primitive), 'hc': True}]
    with pytest.raises(ValueError, match='primitive|index'):
        LindbladRequest(**body)._physical_checks()


def test_nonhermitian_sum_and_conjugated_coefficient():
    body = payload()
    body['model']['hamiltonian'] = [{'operator': product('a'), 'coefficient': {'im': .25}}]
    request = LindbladRequest(**body)
    with pytest.raises(ValueError, match='Hermitian'):
        request._physical_checks()
    body['model']['hamiltonian'][0]['hc'] = True
    assert LindbladRequest(**body)._physical_checks() == 2


def test_explicit_matrix_mode_export_shape_and_named_matrix():
    body = payload('spectrum')
    body['model']['modes'] = [{'name': 'export', 'kind': 'matrix', 'dimension': 2, 'hamiltonian': [[0, 0], [0, 1]]}]
    body['model']['matrices'] = {'x': [[0, 1], [1, 0]]}
    body['model']['hamiltonian'] = [{'coefficient': .2, 'operator': {'kind': 'matrix', 'name': 'x'}}]
    request = LindbladRequest(**body)
    assert request._physical_checks() == 2
    canonical = request.canonical_envelope()
    assert 'frequency' not in canonical['model']['modes'][0]
    assert LindbladRequest(**canonical).canonical_envelope() == canonical
    jsonschema.validate(canonical, LindbladRequest.model_json_schema())


@pytest.mark.parametrize('changes', [{'frequency': 0}, {'kerr': 0}, {'hamiltonian': None}])
def test_matrix_mode_never_ignores_oscillator_fields(changes):
    body = payload()
    body['model']['modes'][0] = {'name': 'q', 'kind': 'matrix', 'dimension': 2,
                               'hamiltonian': [[0, 0], [0, 1]], **changes}
    with pytest.raises(ValidationError):
        LindbladRequest(**body)


@pytest.mark.parametrize('channel', [
    {'kind': 'relaxation', 'mode': 'q', 'T1': 10},
    {'kind': 'dephasing', 'mode': 'q', 'Tphi': 10},
    {'kind': 'thermal', 'mode': 'q', 'rate': .1, 'occupation': .2},
])
def test_decay_channels_and_canonical_roundtrip(channel):
    body = payload(solver='mc')
    body['model']['collapse'] = [channel]
    request = LindbladRequest(**body)
    assert request._physical_checks() == 2
    canonical = request.canonical_envelope()
    assert LindbladRequest(**canonical).canonical_envelope() == canonical
    jsonschema.validate(canonical, LindbladRequest.model_json_schema())


@pytest.mark.parametrize('channels', [[], [{'kind': 'operator', 'operator': product('a'), 'rate': 0}],
    [{'kind': 'operator', 'operator': {'kind': 'matrix', 'name': 'zero'}, 'rate': 1}]])
def test_mc_effective_zero_channels_rejected(channels):
    body = payload(solver='mc')
    body['model'].update(collapse=channels, matrices={'zero': [[0, 0], [0, 0]]})
    with pytest.raises(ValueError, match='nonzero collapse'):
        LindbladRequest(**body)._physical_checks()


@pytest.mark.parametrize('channel', [
    {'kind': 'operator', 'operator': product('a'), 'rate': -1},
    {'kind': 'relaxation', 'mode': 'q', 'T1': 0},
    {'kind': 'dephasing', 'mode': 'q', 'Tphi': -1},
    {'kind': 'relaxation', 'mode': 'q', 'T1': 1e-320},
    {'kind': 'thermal', 'mode': 'q', 'rate': 1, 'occupation': -1},
    {'kind': 'operator', 'operator': product('a'), 'rate': -1e300},
])
def test_invalid_decay_channels(channel):
    body = payload(solver='me')
    body['model']['collapse'] = [channel]
    with pytest.raises(ValueError, match='decay|collapse|occupation'):
        LindbladRequest(**body)._physical_checks()


@pytest.mark.parametrize('envelope', [
    {'shape': 'constant'}, {'shape': 'square', 'start': .2, 'stop': .3},
    {'shape': 'gaussian', 'start': .2, 'stop': .3, 'center': .25, 'sigma': .02},
    {'shape': 'flattop_cosine', 'start': .2, 'stop': .3, 'rise': .02},
    {'shape': 'sampled', 'times': [0, .25, 1], 'values': [0, {'re': .1, 'im': .2}, 0]},
])
def test_all_v1_envelopes_and_hidden_between_output_pulse(envelope):
    body = payload()
    body['model']['drives'] = [{'operator': product('a'), 'hc': True, 'amplitude': .2, 'envelope': envelope}]
    request = LindbladRequest(**body)
    assert request._physical_checks() == 2
    canonical = request.canonical_envelope()
    assert LindbladRequest(**canonical).canonical_envelope() == canonical
    jsonschema.validate(canonical, LindbladRequest.model_json_schema())


@pytest.mark.parametrize('envelope', [
    {'shape': 'drag', 'start': 0, 'stop': 1},
    {'shape': 'square', 'start': 0, 'stop': 1, 'rise': .1},
    {'shape': 'constant', 'sigma': 1}, {'shape': 'sampled', 'times': [0, 1], 'values': [0, 1], 'interpolation': 'cubic'},
    {'shape': 'gaussian', 'start': 0, 'stop': 1, 'sigma': .1},
])
def test_unsupported_pulse_schema_fields(envelope):
    body = payload()
    body['model']['drives'] = [{'operator': product('x'), 'envelope': envelope}]
    with pytest.raises(ValidationError):
        LindbladRequest(**body)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, LindbladRequest.model_json_schema())


@pytest.mark.parametrize('envelope', [
    {'shape': 'square', 'start': 1, 'stop': 0},
    {'shape': 'gaussian', 'start': 0, 'stop': 1, 'center': .5, 'sigma': 0},
    {'shape': 'flattop_cosine', 'start': 0, 'stop': 1, 'rise': .6},
    {'shape': 'sampled', 'times': [0, 0], 'values': [0, 1]},
    {'shape': 'sampled', 'times': [0, 1, 2], 'values': [0, 1]},
])
def test_invalid_pulse_semantics_are_separate_from_normalization(envelope):
    body = payload()
    body['model']['drives'] = [{'operator': product('x'), 'envelope': envelope}]
    request = LindbladRequest(**body)
    assert request.canonical_envelope()['model']['drives']
    with pytest.raises(ValueError, match='support|sigma|rise|sample'):
        request._physical_checks()


@pytest.mark.parametrize('times', [[1, 1], [1, 0], [0, 1, .5], {'start': -1e308, 'stop': 1e308, 'points': 3}])
def test_time_order_and_span_validation_after_canonicalization(times):
    body = payload()
    body['times'] = times
    request = LindbladRequest(**body)
    assert request.canonical_envelope()['times']
    with pytest.raises(ValueError, match='times'):
        request._physical_checks()


def test_grid_exact_endpoints_and_rounding_duplicate_refusal():
    body = payload()
    body['times'] = {'start': 1e16, 'stop': 1e16+2, 'points': 3}
    request = LindbladRequest(**body)
    assert request.canonical_envelope()['times'][0] == 1e16
    assert request.canonical_envelope()['times'][-1] == 1e16+2
    with pytest.raises(ValueError, match='increasing'):
        request._physical_checks()


def test_result_size_refusal_precedes_missing_native_certificate():
    body = payload('propagator', 'me')
    body['model']['modes'][0]['dimension'] = 32
    request = LindbladRequest(**body)
    with pytest.raises(ValueError, match='numeric result payload'):
        request.admission_plan()


def test_no_native_imports_during_validation_or_schema():
    script = '''
import sys
class RefuseNative:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'qutip','scqubits','scipy','cupy','sky','jax'}:
            raise AssertionError('native import: ' + fullname)
sys.meta_path.insert(0, RefuseNative())
from qsim.lindblad_models import LindbladRequest
LindbladRequest.model_json_schema()
r = LindbladRequest(schema_version=1, operation='evolve', times=[0,1], model={'modes':[{'name':'q','kind':'oscillator','dimension':2}]})
r.canonical_envelope()
r._physical_checks()
'''
    completed = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize('solver', ['se', 'me'])
def test_mc_controls_only_on_mc(solver):
    for key, value in [('seed', 0), ('trajectories', 1)]:
        body = payload(solver=solver)
        body[key] = value
        with pytest.raises(ValidationError):
            LindbladRequest(**body)


@pytest.mark.parametrize('key,value', [('seed', -1), ('seed', 2**63), ('seed', True),
    ('seed', 1.), ('trajectories', 0), ('trajectories', 1001), ('trajectories', True)])
def test_mc_boundaries(key, value):
    body = payload(solver='mc')
    body[key] = value
    with pytest.raises(ValidationError):
        LindbladRequest(**body)


def test_mc_default_and_high_seed_bits_preserved():
    body = payload(solver='mc')
    default = LindbladRequest(**body).canonical_envelope()
    assert default['seed'] == 42 and default['trajectories'] == 1
    body.update(seed=2**63-1, trajectories=1000)
    assert LindbladRequest(**body).canonical_envelope()['seed'] == 2**63-1


@pytest.mark.parametrize('change,pointer', [
    ('duplicate_mode', '/model/modes'), ('duplicate_observable', '/observables'),
    ('missing_matrix', '/observables/0/operator/name'), ('bad_matrix_shape', '/model/matrices/bad~0~1matrix'),
    ('unknown_decay_mode', '/model/collapse/0/mode'),
])
def test_semantic_reference_errors_have_precise_pointers(change, pointer):
    body = payload(solver='me')
    if change == 'duplicate_mode':
        body['model']['modes'].append(copy.deepcopy(body['model']['modes'][0]))
    elif change == 'duplicate_observable':
        body['observables'] = [{'name': 'same', 'operator': product()}] * 2
    elif change == 'missing_matrix':
        body['observables'] = [{'name': 'n', 'operator': {'kind': 'matrix', 'name': 'missing'}}]
    elif change == 'bad_matrix_shape':
        body['model']['matrices'] = {'bad~/matrix': [[0]]}
    elif change == 'unknown_decay_mode':
        body['model']['collapse'] = [{'kind': 'relaxation', 'mode': 'absent', 'T1': 1}]
    with pytest.raises(ValueError) as caught:
        LindbladRequest(**body)._physical_checks()
    assert caught.value.pointer == pointer


@pytest.mark.parametrize('operation', ['spectrum', 'steadystate'])
def test_static_operations_reject_drives(operation):
    body = payload(operation)
    body['model']['drives'] = [{'operator': product('x'), 'envelope': {'shape': 'constant'}}]
    with pytest.raises(ValueError, match='static operation rejects drives'):
        LindbladRequest(**body)._physical_checks()


@pytest.mark.parametrize('operation,solver', [('spectrum', None), ('evolve', 'se'), ('propagator', 'se')])
def test_operation_dissipation_refusals(operation, solver):
    body = payload(operation, solver)
    body['model']['collapse'] = [{'kind': 'relaxation', 'mode': 'q', 'T1': 10}]
    with pytest.raises(ValueError, match='rejects dissipation'):
        LindbladRequest(**body)._physical_checks()


def test_thermal_channels_count_effective_up_and_down_channels():
    body = payload(solver='me')
    body['model']['collapse'] = [{'kind': 'thermal', 'mode': 'q', 'rate': 1, 'occupation': .2}] * 9
    with pytest.raises(ValueError, match='16 effective collapse'):
        LindbladRequest(**body)._physical_checks()


def test_coefficient_channels_count_conjugates():
    body = payload()
    body['model']['drives'] = [{'operator': product('a'), 'hc': True, 'envelope': {'shape': 'constant'}}] * 9
    with pytest.raises(ValueError, match='16 scalar coefficient'):
        LindbladRequest(**body)._physical_checks()


@pytest.mark.parametrize('changes', [{'amplitude': 1e308}, {'detuning': 1e308},
    {'operator': product('a'), 'hc': False}, {'detuning': 1, 'hc': False}])
def test_drive_nonhermitian_and_derived_overflow_refusal(changes):
    body = payload()
    body['model']['drives'] = [{'operator': product('x'), 'envelope': {'shape': 'constant'}, 'hc': True, **changes}]
    with pytest.raises(ValueError, match='drive|carrier|Hermitian|modulation'):
        LindbladRequest(**body)._physical_checks()


def test_duffing_kerr_normalization_and_derived_overflow():
    body = payload()
    body['model']['modes'][0].update(kind='duffing', kerr=-.3, frequency=5.)
    request = LindbladRequest(**body)
    assert request._physical_checks() == 2
    assert request.canonical_envelope()['model']['modes'][0]['kerr'] == -.3
    body['model']['modes'][0]['frequency'] = 1e308
    with pytest.raises(ValueError, match='unrepresentable'):
        LindbladRequest(**body)._physical_checks()


def test_density_dimension_limit_precedes_eigensolve(monkeypatch):
    body = payload(solver='me')
    body['model']['modes'][0]['dimension'] = 33
    monkeypatch.setattr(np.linalg, 'eigvalsh', lambda *a, **k: pytest.fail('unexpected eigenvalue solve'))
    with pytest.raises(ValueError, match='exceeds 32'):
        LindbladRequest(**body)._physical_checks()


@pytest.mark.parametrize('extra_location', ['mode', 'model', 'term', 'operator', 'complex', 'drive', 'envelope', 'state'])
def test_nested_unknown_fields_rejected(extra_location):
    body = payload()
    term = {'operator': product(), 'coefficient': {'re': 1}}
    drive = {'operator': product('x'), 'envelope': {'shape': 'constant'}}
    state = {'kind': 'product', 'modes': {}}
    body['model'].update(hamiltonian=[term], drives=[drive])
    body['initial_state'] = state
    node = {'mode': body['model']['modes'][0], 'model': body['model'], 'term': term,
            'operator': term['operator'], 'complex': term['coefficient'], 'drive': drive,
            'envelope': drive['envelope'], 'state': state}[extra_location]
    node['python'] = '__import__("os")'
    with pytest.raises(ValidationError):
        LindbladRequest(**body)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, LindbladRequest.model_json_schema())


def test_large_explicit_state_checked_without_hidden_native_library():
    body = payload()
    body['model']['modes'][0]['dimension'] = 256
    body['initial_state'] = {'kind': 'density', 'values': [[int(i == 0 and j == 0) for j in range(256)] for i in range(256)]}
    assert LindbladRequest(**body)._physical_checks() == 256


@pytest.mark.parametrize('sample,amplitude', [({'re': 1.5, 'im': 1.5}, 1e307),
    ({'re': 1.5e308, 'im': 1.5e308}, 1e-308),
    ({'re': 1.0, 'im': 1.0}, {'re': 1e307, 'im': 1e307})])
def test_complex_sample_magnitude_and_hc_overflow_bounds(sample, amplitude):
    body = payload()
    body['model']['drives'] = [{'operator': product('x'), 'hc': True, 'amplitude': amplitude,
                              'envelope': {'shape': 'sampled', 'times': [0, 1], 'values': [sample, sample]}}]
    with pytest.raises(ValueError, match='drive strength'):
        LindbladRequest(**body)._physical_checks()


def test_coherent_component_magnitude_overflow_has_semantic_pointer():
    body = payload()
    body['initial_state'] = {'kind': 'product', 'modes': {
        'q': {'kind': 'coherent', 'amplitude': {'re': 1.7e308, 'im': 1.7e308}}}}
    request = LindbladRequest(**body)
    with pytest.raises(ValueError, match='coherent occupation') as caught:
        request.admission_plan()
    assert caught.value.pointer == '/initial_state/modes/q/amplitude'
