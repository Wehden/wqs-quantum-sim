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
    if operation == 'export_for_lindblad':
        with pytest.raises(ValueError, match='resource_limit: frozen scqubits'):
            request.admission_plan()
    else:
        assert request.admission_plan().algorithm == 'scipy-dsyevr-zheevr-subset'


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
def test_result_shapes_and_certified_native_allocation(qubit):
    request = ScqubitsRequest(**{**payload(qubit), 'return_vectors': True})
    dimension = request._physical_checks()
    plan = request._result_plan(dimension)
    assert [(a.name, a.shape) for a in plan.arrays] == [('eigenvalues', (6,)), ('eigenvectors', (dimension, 6))]
    assert plan.numeric_bytes == 8 * 6 + 16 * dimension * 6
    assert plan.callback_upper_bytes < 12 * 1024**2
    allocation = request.admission_plan()
    assert allocation.result == plan
    assert allocation.dimensions == {'raw_basis': dimension, 'levels': 6}
    assert allocation.peak_bytes == sum(size for _, size in allocation.allocation_items)
    assert allocation.peak_bytes <= 2 * 1024**3


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


def test_pinned_dense_solver_and_constructor_sources():
    """Certificate provenance is invalidated by native implementation changes."""
    code = '''
import hashlib
import importlib
sources = {
    "scqubits.core.diag": "11d283c199721534032a9f0d1b8094471b849f0648a72d74b61fcb8c8dcee13b",
    "scqubits.core.qubit_base": "30b28e50488080fdab59976e47ee3cf2005c3174dfd011a09e1cc85871d71c6f",
    "scqubits.core.transmon": "5e6ecc5e9230734cb6a68c3b4edb67c8dcb76dd41e4b6e8957020c1459bbe30c",
    "scqubits.core.fluxonium": "cc3133e4c704aa11de3b112365958aecef669537d5d27d252daca7edb0b754d3",
    "scqubits.core.flux_qubit": "eaf48a76287c06fe59a739e378b974d58b9540d819c4c4b1fec82d723de25dfe",
    "scqubits.core.operators": "46cfb9bd9d89c05131418d99fbd6ac3b91e7fac9d4b57687c1e814e5ca9148fe",
    "scipy.linalg._decomp": "4dbf81305777e4bfd7e6df834e411aa8751b715a7d957c6f42b70808cb0b6a2f",
    "scipy.linalg._matfuncs": "72da5780b458ecf9f78fd75662fa28c95cb14b66c4faf1327891be009830a1b6",
    "scipy.linalg._flapack": "00fc3e3414d7f42f023d5ba1f1b616d3d81da8da0f5cc6b17279af70be963b9c",
    "scipy.linalg._internal_matfuncs": "e98f03e852b7acf316c6a6d2294360ed2fb98ee4b1e9c8eccffd123f9c1dc74f",
    "numpy._core._multiarray_umath": "b185dfe64b07180bedc7c15fb2735b0d0f4f4e09eb9c15bd8e67f74568aebcbf",
    "numpy.linalg._umath_linalg": "2e075f582c3a25a21fe97a55931a0fdb22ceb84f4fce8203a5040d405ccf65ce",
    'numpy.lib._twodim_base_impl': 'a11d0bdecd5064dae7aca1ecbc3fdb9ff0eacae4cdfa8ed1a644ac0bb4a58fe6',
    'numpy.lib._shape_base_impl': 'b550d0cc5e7efe46dc1c77f0286330fcb4100fcbbeb98f41aa9127c2c44e01ec',
    'numpy._core.numeric': 'feb150554b4879d4df7bd0a4ab7b1a6b818de7ba994ed0c73583fee177533417',
    'numpy.linalg._linalg': '99ec3bec6f80194741a62407fbcea521a0690577105fe9f3096f156842de76a9',
}
from pathlib import Path
for module, expected in sources.items():
    source = Path(importlib.import_module(module).__file__)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, module
import scipy
blas = Path(scipy.__file__).parent.parent / "scipy.libs" / "libscipy_openblas-5f890258.so"
assert hashlib.sha256(blas.read_bytes()).hexdigest() == "2a5b4c1a6311873c59132b2ceaaf3c26258a17350b7cd27ea8782a47e3322981"
numpy_blas = Path(scipy.__file__).parent.parent / "numpy.libs" / "libscipy_openblas64_-f48b354e.so"
assert hashlib.sha256(numpy_blas.read_bytes()).hexdigest() == "432e1c1739731468ba305771af96773939e43016643e2d630f58cb45b6670e96"
'''
    subprocess.run([sys.executable, '-c', code], check=True,
                   env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                        'MKL_NUM_THREADS': '1'})


def test_frozen_lapack_workspace_bounds_cover_every_admitted_dimension():
    """Queries run in test children only; request validation stays native-free."""
    code = '''
from scipy.linalg.lapack import dsyevr_lwork, zheevr_lwork
for dimension in range(1, 513):
    real_work, real_iwork, real_info = dsyevr_lwork(dimension, lower=1)
    complex_work, complex_rwork, complex_iwork, complex_info = zheevr_lwork(dimension, lower=1)
    assert real_info == complex_info == 0
    assert int(real_work) == int(complex_work.real) == 33 * dimension
    assert int(real_iwork) == int(complex_iwork) == (1 if dimension == 1 else 10 * dimension)
    assert int(complex_rwork) == (1 if dimension == 1 else 24 * dimension)
'''
    subprocess.run([sys.executable, '-c', code], check=True,
                   env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                        'MKL_NUM_THREADS': '1'})


@pytest.mark.parametrize('qubit,operator', [(q, o) for q, names in OPERATORS.items()
                                          for o in ['vectors', *names]])
def test_largest_native_basis_and_result_transport_under_cpu_limits(qubit, operator):
    body = payload(qubit, 'spectrum' if operator == 'vectors' else 'matrix_elements')
    body['evals_count'] = 32
    body['model']['params']['cutoff' if qubit == 'Fluxonium' else 'ncut'] = {
        'Transmon': 255, 'TunableTransmon': 255, 'Fluxonium': 512, 'FluxQubit': 10,
    }[qubit]
    if operator == 'vectors':
        body['return_vectors'] = True
    else:
        body['operator'] = operator
    code = '''
import resource, json, sys
resource.setrlimit(resource.RLIMIT_AS, (3*1024**3, 3*1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE, (12*1024**2, 12*1024**2))
from qsim.scqubits_models import ScqubitsRequest
from qsim.scqubits_adapter import _run_native
from qsim.physics_results import serialize_physics_result
r = ScqubitsRequest(**json.loads(sys.argv[1]))
result = _run_native(r)
plan = r._result_plan(r._physical_checks())
summary, artifact = serialize_physics_result(result, plan)
assert len(artifact) <= plan.callback_upper_bytes
assert result.arrays['eigenvalues'].shape == (32,)
assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 < 512*1024**2
'''
    subprocess.run([sys.executable, '-c', code, json.dumps(body)], check=True,
                   env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                        'MKL_NUM_THREADS': '1'})


@pytest.mark.parametrize('qubit', PARAMETERS)
@pytest.mark.parametrize('operation', ['spectrum', 'matrix_elements'])
def test_certificate_covers_complete_largest_shape_and_transport(qubit, operation):
    body = payload(qubit, operation)
    body['evals_count'] = 32
    body['model']['params']['cutoff' if qubit == 'Fluxonium' else 'ncut'] = {
        'Transmon': 255, 'TunableTransmon': 255, 'Fluxonium': 512, 'FluxQubit': 10,
    }[qubit]
    if operation == 'spectrum':
        body['return_vectors'] = True
    request = ScqubitsRequest(**body)
    plan = request.admission_plan()
    assert plan == ScqubitsRequest(**request.canonical_envelope()).admission_plan()
    assert plan.policy_revision == 'scqubits-cpu-evr-v1'
    charges = dict(plan.allocation_items)
    assert len(charges) == len(plan.allocation_items)
    assert charges['import_native_headroom'] == 512*1024**2
    assert charges['parent_child_handoff'] == 2*plan.result.callback_upper_bytes
    assert charges['npz_serialization_buffers'] == 4*plan.result.npz_upper_bytes
    assert plan.peak_bytes == sum(charges.values()) < 2*1024**3
    assert plan.coefficient_plan['certificate'] == '531974791bde44c9f6bb29e85fe042e785e1315326e8c0288c788ad38ab4991d'


def test_certificate_digest_binds_policy_versions_sources_and_workspace():
    import hashlib
    from qsim.scqubits_models import _CPU_CERTIFICATE
    manifest = {key: value for key, value in _CPU_CERTIFICATE.items() if key != 'digest'}
    encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    assert hashlib.sha256(encoded).hexdigest() == _CPU_CERTIFICATE['digest']
    assert _CPU_CERTIFICATE['versions'] == {
        'numpy': '2.5.4', 'scipy': '1.18.1', 'scqubits': '4.3.1', 'qutip': '5.3.1'}
    assert all('/' in name and len(item['sha256']) == 64 and item['size'] > 0
               for group in ('source_hashes', 'native_hashes')
               for name, item in _CPU_CERTIFICATE[group].items())


@pytest.mark.parametrize('qubit,changes', [
    ('Fluxonium', {'EC': 1e154, 'EL': 1e154}),
    ('Fluxonium', {'EC': 1e-200, 'EL': 1e-200}),
    ('FluxQubit', {'ECg1': 1e308}),
])
def test_native_scalar_intermediate_representability_before_admission(qubit, changes):
    body = payload(qubit)
    body['model']['params'].update(changes)
    request = ScqubitsRequest(**body)
    assert all(request.canonical_envelope()['model']['params'][name] == value
               for name, value in changes.items())
    with pytest.raises(ValueError, match='physical_validation:.*(derived|representable)'):
        request.admission_plan()
