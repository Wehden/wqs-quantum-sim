"""Same-eigensystem numeric export; private tests do not certify admission."""
import base64
import io
import json

import numpy as np
import pytest

from qsim import scqubits_adapter as adapter
from qsim import lindblad_adapter
from qsim.lindblad_model import _build_model
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import AllocationPlan, serialize_physics_result
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
    'FluxQubit': ['n_1_operator', 'n_2_operator', 'cos_phi_1_operator',
                  'cos_phi_2_operator', 'sin_phi_1_operator', 'sin_phi_2_operator'],
}


def request(qubit='Transmon', *, levels=6, operators=None):
    return ScqubitsRequest(schema_version=1, operation='export_for_lindblad', evals_count=levels,
        model={'qubit': qubit, 'params': PARAMETERS[qubit]},
        operators=OPERATORS[qubit] if operators is None else operators)


def staged(req):
    result = adapter._run_export(req)
    summary, artifact = serialize_physics_result(result, req._result_plan(req._physical_checks()))
    return result, summary, artifact


def lindblad_spectrum(body):
    req = LindbladRequest.model_validate(body)
    dimension = req._physical_checks()
    plan = AllocationPlan('synthetic-test-only', 'export-roundtrip-private-numerics',
        {'hilbert': dimension}, (), 0, {}, req._result_plan(dimension))
    return lindblad_adapter._run_static(req, _build_model(req, plan))


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_all_classes_roundtrip_ground_referenced_spectrum(qubit):
    req = request(qubit)
    result, summary, artifact = staged(req)
    native = adapter._native_constructor(req)
    energies, _ = adapter._eigensystem(native, req)
    shifted = energies-energies[0]
    assert list(result.arrays) == ['eigenvalues', 'hamiltonian', *OPERATORS[qubit]]
    np.testing.assert_allclose(result.arrays['eigenvalues'], shifted, atol=1e-9, rtol=0)
    np.testing.assert_allclose(result.arrays['hamiltonian'], np.diag(shifted), atol=1e-9, rtol=0)
    assert result.metadata['energy_reference'] == 'ground'
    assert abs(result.metadata['removed_ground_energy']-energies[0]) < 1e-9
    body = json.loads(json.dumps(result.metadata['lindblad_request'], allow_nan=False))
    assert body['schema_version'] == 1 and body['engine'] == 'lindblad'
    roundtrip = lindblad_spectrum(body)
    np.testing.assert_allclose(roundtrip.arrays['eigenvalues'], shifted, atol=1e-9, rtol=0)
    assert result.work['eigensystem_calls'] == '1'
    assert result.work['operator_transforms'] == str(len(OPERATORS[qubit]))
    with np.load(io.BytesIO(base64.b64decode(artifact)), allow_pickle=False) as archive:
        metadata = json.loads(str(archive['metadata_json']))
        assert metadata['lindblad_request'] == body
        np.testing.assert_array_equal(archive['hamiltonian'], result.arrays['hamiltonian'])


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_every_exported_operator_uses_exactly_one_shared_eigensystem(qubit, monkeypatch):
    req = request(qubit, operators=list(reversed(OPERATORS[qubit])))
    native = adapter._native_constructor(req)
    original = native.eigensys
    calls = []
    def eigensystem(**kwargs):
        values, vectors = original(**kwargs)
        calls.append((values, vectors))
        return values, vectors
    monkeypatch.setattr(native, 'eigensys', eigensystem)
    monkeypatch.setattr(adapter, '_native_constructor', lambda _: native)
    result, _, _ = staged(req)
    assert len(calls) == 1
    vectors = calls[0][1]
    body = result.metadata['lindblad_request']
    parsed = LindbladRequest.model_validate(body)
    parsed._physical_checks()
    assert list(body['model']['matrices']) == req.operators
    assert result.metadata['operators'] == req.operators
    from qsim.lindblad_models import _complex_array
    for operator in req.operators:
        expected = vectors.conj().T@np.asarray(getattr(native, operator)())@vectors
        actual = result.arrays[operator]
        np.testing.assert_allclose(actual, expected, atol=1e-12)
        np.testing.assert_allclose(actual, actual.conj().T, atol=1e-12)
        decoded = _complex_array(parsed.model.matrices[operator], actual.shape, '/matrix')
        np.testing.assert_array_equal(decoded, actual)
    if 'phi_operator' in req.operators:
        assert result.metadata['units']['phi_operator'] == 'radians'


def test_empty_operator_list_has_a_valid_independent_numeric_handoff():
    result, _, _ = staged(request(operators=[]))
    assert list(result.arrays) == ['eigenvalues', 'hamiltonian']
    assert result.metadata['lindblad_request']['model']['matrices'] == {}
    assert result.work['operator_transforms'] == '0'
    lindblad_spectrum(result.metadata['lindblad_request'])


def test_single_level_export_preserves_ground_offset():
    result, _, _ = staged(request(levels=1))
    np.testing.assert_array_equal(result.arrays['eigenvalues'], [0.])
    np.testing.assert_array_equal(result.arrays['hamiltonian'], [[0.]])
    assert result.metadata['removed_ground_energy'] != 0
    roundtrip = lindblad_spectrum(result.metadata['lindblad_request'])
    np.testing.assert_array_equal(roundtrip.arrays['eigenvalues'], [0.])


def test_native_phase_freedom_rotates_all_operators_together(monkeypatch):
    req = request()
    native = adapter._native_constructor(req)
    original = native.eigensys
    phases = np.exp(1j*np.arange(req.evals_count)*.3)
    baseline, _, _ = staged(req)
    def rotated(**kwargs):
        energies, vectors = original(**kwargs)
        return energies, vectors*phases
    monkeypatch.setattr(native, 'eigensys', rotated)
    monkeypatch.setattr(adapter, '_native_constructor', lambda _: native)
    result, _, _ = staged(req)
    np.testing.assert_array_equal(result.arrays['hamiltonian'], baseline.arrays['hamiltonian'])
    for operator in req.operators:
        expected = phases.conj()[:, None]*baseline.arrays[operator]*phases
        np.testing.assert_allclose(result.arrays[operator], expected, atol=1e-12)
        np.testing.assert_allclose(abs(result.arrays[operator])**2,
                                   abs(baseline.arrays[operator])**2, atol=1e-12)


def test_degenerate_subspace_rotation_keeps_shared_basis_consistent(monkeypatch):
    req = request(levels=5)
    req.model.params.EJ = 0.
    native = adapter._native_constructor(req)
    original = native.eigensys
    baseline, _, _ = staged(req)
    energies = baseline.arrays['eigenvalues']
    assert energies[1] == energies[2]
    rotation = np.eye(5, dtype=complex)
    rotation[1:3, 1:3] = np.array([[1., 1j], [1j, 1.]])/np.sqrt(2)
    def rotated(**kwargs):
        values, vectors = original(**kwargs)
        return values, vectors@rotation
    monkeypatch.setattr(native, 'eigensys', rotated)
    monkeypatch.setattr(adapter, '_native_constructor', lambda _: native)
    result, _, _ = staged(req)
    for operator in req.operators:
        np.testing.assert_allclose(result.arrays[operator],
            rotation.conj().T@baseline.arrays[operator]@rotation, atol=1e-12)
        np.testing.assert_allclose(np.linalg.norm(result.arrays[operator][1:3, 1:3]),
                                   np.linalg.norm(baseline.arrays[operator][1:3, 1:3]), atol=1e-12)
    np.testing.assert_array_equal(result.arrays['hamiltonian'], baseline.arrays['hamiltonian'])


def test_json_duplication_limit_refuses_before_native_construction(monkeypatch):
    req = request('FluxQubit', levels=11)
    monkeypatch.setattr(adapter, '_native_constructor', lambda _: pytest.fail('allocation before JSON bound'))
    with pytest.raises(ValueError, match='inline export exceeds the metadata envelope'):
        adapter._run_export(req)


def test_largest_all_operator_export_fits_finite_json_and_exact_numeric_plan():
    req = request('FluxQubit', levels=10)
    result, _, artifact = staged(req)
    plan = req._result_plan(req._physical_checks())
    assert plan.numeric_bytes == 10*8+7*10*10*16
    metadata = {**result.metadata, 'work': result.work}
    assert len(json.dumps(metadata, allow_nan=False, separators=(',', ':')).encode()) <= 65536
    assert len(base64.b64decode(artifact)) <= plan.npz_upper_bytes
    lindblad_spectrum(result.metadata['lindblad_request'])


def test_export_rejects_a_missing_or_changed_allocation_plan_before_runtime(monkeypatch):
    monkeypatch.setattr(adapter, '_native_constructor', lambda _: pytest.fail('export before certificate'))
    monkeypatch.setattr(adapter, '_verify_runtime_identity', lambda: pytest.fail('runtime before admission'))
    with pytest.raises(ValueError, match='allocation plan'):
        adapter.run_scqubits(request(), None, execution_context={})


@pytest.mark.parametrize('problem', ['shape', 'nonfinite', 'nonhermitian'])
def test_bad_native_operators_cannot_create_an_export(problem, monkeypatch):
    req = request()
    native = adapter._native_constructor(req)
    raw = np.asarray(native.n_operator()).astype(complex)
    if problem == 'shape':
        raw = raw[:-1]
    elif problem == 'nonfinite':
        raw[0, 0] = np.nan
    else:
        raw[0, 1] += 1j
    monkeypatch.setattr(native, 'n_operator', lambda: raw)
    monkeypatch.setattr(adapter, '_native_constructor', lambda _: native)
    with pytest.raises(ValueError, match='invalid_result'):
        adapter._run_export(req)


def test_unrepresentable_energy_offset_cannot_create_export(monkeypatch):
    req = request()
    energies = np.array([-np.finfo(float).max, *([np.finfo(float).max]*5)])
    monkeypatch.setattr(adapter, '_eigensystem', lambda *a: (energies, np.eye(6)))
    with pytest.raises(ValueError, match='ground-referenced energies are unrepresentable'):
        adapter._run_export(req)


@pytest.mark.parametrize('problem', ['shape', 'nonhermitian', 'nonfinite', 'version'])
def test_malformed_handoff_is_rejected_by_lindblad_schema_or_physical_validation(problem):
    result, _, _ = staged(request())
    body = json.loads(json.dumps(result.metadata['lindblad_request']))
    if problem == 'shape':
        body['model']['modes'][0]['hamiltonian'].pop()
    elif problem == 'nonhermitian':
        body['model']['modes'][0]['hamiltonian'][0][1] = 1.
    elif problem == 'nonfinite':
        body['model']['modes'][0]['hamiltonian'][0][0] = float('nan')
    else:
        body['schema_version'] = 2
    with pytest.raises(ValueError):
        lindblad_spectrum(body)


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_exported_operators_drive_real_lindblad_expectations_in_the_same_basis(qubit):
    req = request(qubit)
    exported, _, _ = staged(req)
    body = json.loads(json.dumps(exported.metadata['lindblad_request']))
    body.pop('evals_count')
    body.update(operation='evolve', solver='se', times=[0., .013],
        observables=[{'name': name, 'operator': {'kind': 'matrix', 'name': name}}
                     for name in req.operators],
        initial_state={'kind': 'amplitudes', 'values':
                       [1/np.sqrt(2), {'re': 0., 'im': 1/np.sqrt(2)}, 0., 0., 0., 0.]})
    target = LindbladRequest.model_validate(json.loads(json.dumps(body)))
    size = target._physical_checks()
    plan = AllocationPlan('synthetic-test-only', 'export-evolve-private-numerics',
        {'hilbert': size}, (), 0, {}, target._result_plan(size))
    evolved = lindblad_adapter._run_evolve(target, _build_model(target, plan))
    state = np.array([1/np.sqrt(2), 1j/np.sqrt(2), 0., 0., 0., 0.])
    for index, time in enumerate(body['times']):
        psi = state*np.exp(-1j*2*np.pi*exported.arrays['eigenvalues']*time)
        expected = [np.vdot(psi, exported.arrays[name]@psi) for name in req.operators]
        np.testing.assert_allclose(evolved.arrays['expectations'][index], expected, atol=1e-7)
    assert exported.metadata['units']['removed_ground_energy'] == 'GHz'


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_export_admission_charges_shared_eigensystem_and_retained_json(qubit):
    req = request(qubit)
    plan = req.admission_plan()
    charges = dict(plan.allocation_items)
    assert charges['operator_constructor'] > 0
    assert charges['operator_validation_arrays'] > 0
    assert charges['operator_transform_arrays'] > 0
    assert charges['export_json_matrix_objects'] > 0
    assert charges['export_energy_shift_and_diagonal_arrays'] > 0
    assert plan.peak_bytes == sum(charges.values()) <= 2*1024**3
    assert plan.coefficient_plan['export_certificate']
    assert plan.result == req._result_plan(req._physical_checks())


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_certified_export_executes_without_an_admission_bypass(qubit):
    req = request(qubit)
    plan = req.admission_plan()
    result = adapter.run_scqubits(req, plan, execution_context={'backend':'cpu'})
    _, artifact = serialize_physics_result(result, plan.result)
    assert result.metadata['policy_revision'] == plan.policy_revision
    assert result.work['eigensystem_calls'] == '1'
    assert result.work['operator_transforms'] == str(len(req.operators))
    with np.load(io.BytesIO(base64.b64decode(artifact)), allow_pickle=False) as arrays:
        assert list(arrays.files) == [*result.arrays, 'metadata_json']
        assert not any(arrays[name].dtype.hasobject for name in arrays.files)


def test_export_certificate_binds_source_and_python_container_layouts():
    import hashlib
    from pathlib import Path
    import struct
    import sys
    from qsim.scqubits_models import _CPU_CERTIFICATE, _EXPORT_CERTIFICATE
    manifest = {key:value for key,value in _EXPORT_CERTIFICATE.items() if key!='digest'}
    assert hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest() == _EXPORT_CERTIFICATE['digest']
    assert _EXPORT_CERTIFICATE['native_certificate'] == _CPU_CERTIFICATE['digest']
    assert hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest() == _EXPORT_CERTIFICATE['adapter_sha256']
    assert sys.implementation.name == 'cpython' and sys.version_info[:2] == (3,12)
    assert struct.calcsize('P') == 8 and sys.getsizeof(0.) == 24
    assert sys.getsizeof({'re':0.,'im':0.}) <= 184 and sys.getsizeof([]) == 56
    for count in range(1,33):
        value=[float(n) for n in range(count)]
        assert sys.getsizeof(value) <= 56+8*(count+(count>>3)+6)


@pytest.mark.parametrize('qubit', PARAMETERS)
def test_every_export_operator_count_has_exact_maximum_metadata_boundary(qubit):
    from qsim.scqubits_models import _OPERATORS
    maximum_params={**PARAMETERS[qubit]}
    maximum_params.update({'cutoff':512} if qubit=='Fluxonium' else
                          {'ncut':10 if qubit=='FluxQubit' else 255})
    for count in range(len(_OPERATORS[qubit])+1):
        accepted=[]
        for levels in range(1,33):
            req=ScqubitsRequest(schema_version=1,operation='export_for_lindblad',evals_count=levels,
                model={'qubit':qubit,'params':maximum_params},operators=list(_OPERATORS[qubit][:count]))
            try:plan=req.admission_plan()
            except ValueError as error:
                assert 'inline export exceeds the metadata envelope' in str(error)
            else:accepted.append((levels,req,plan))
        levels,req,plan=accepted[-1]
        assert levels < 32 and plan.peak_bytes <= 2*1024**3
        assert plan.dimensions['raw_basis'] in (511,512,441)
        result=adapter.run_scqubits(req,plan,execution_context={'backend':'cpu'})
        _,artifact=serialize_physics_result(result,plan.result)
        with np.load(io.BytesIO(base64.b64decode(artifact)),allow_pickle=False) as arrays:
            assert arrays['hamiltonian'].shape == (levels,levels)
            assert np.isfinite(arrays['hamiltonian']).all()
            assert len(base64.b64decode(artifact)) <= plan.result.npz_upper_bytes
        assert result.work['eigensystem_calls']=='1'
        assert result.work['operator_transforms']==str(count)
