"""Physics child dispatch and transport; admission proofs remain separate gates."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from qsim import worker


def scqubits(operation='spectrum'):
    return {'engine': 'scqubits', 'input_format': 'scqubits_model', 'schema_version': 1,
        'operation': operation, 'evals_count': 4,
        'model': {'qubit': 'Transmon', 'params': {'EJ': 15., 'EC': .3, 'ng': 0., 'ncut': 30}},
        **({'operator': 'n_operator'} if operation == 'matrix_elements' else {}),
        **({'operators': ['n_operator']} if operation == 'export_for_lindblad' else {})}


def child(tmp_path, request, *, code=None, extra_env=None):
    source, output = tmp_path/'input.json', tmp_path/'output.json'
    source.write_text(json.dumps(request))
    args = ([sys.executable, '-c', code, str(source), str(output)] if code else
            [sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)])
    env = {'PATH': '/usr/bin:/bin', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
           **(extra_env or {})}
    completed = subprocess.run(args, env=env, capture_output=True)
    assert completed.returncode == 0, completed.stderr.decode()
    return json.loads(output.read_text())


@pytest.mark.parametrize('operation', ['spectrum', 'matrix_elements'])
def test_real_certified_scqubits_child_result(tmp_path, operation):
    payload = child(tmp_path, scqubits(operation))
    assert payload['status'] == 'SUCCEEDED'
    assert payload['summary']['operation'] == operation
    with np.load(io.BytesIO(base64.b64decode(payload['artifact'])), allow_pickle=False) as result:
        assert result['eigenvalues'].shape == (4,)
        assert np.isfinite(result['eigenvalues']).all()
        metadata = json.loads(str(result['metadata_json']))
        assert metadata['policy_revision'] == 'scqubits-cpu-evr-v1'
        if operation == 'matrix_elements':
            assert result['matrix_elements'].shape == (4, 4)


@pytest.mark.parametrize('body', [
    scqubits('export_for_lindblad'),
    {'engine': 'lindblad', 'input_format': 'lindblad_model', 'schema_version': 1,
     'operation': 'spectrum', 'evals_count': 2,
     'model': {'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': 2, 'frequency': .1}]}},
    {'engine': 'josim', 'input_format': 'josim_netlist', 'schema_version': 1,
     'netlist': Path('tests/fixtures/physics/josim/sfq.cir').read_text()},
])
def test_missing_certificates_fail_closed_in_real_child(tmp_path, body):
    payload = child(tmp_path, body)
    assert payload['status'] == 'FAILED'
    assert payload['error'].startswith('resource_limit:')
    assert 'artifact' not in payload
    assert 'sfq' not in payload['error']


def test_malformed_physics_request_has_sanitized_failure(tmp_path):
    request = scqubits()
    request['model']['params']['EJ'] = 'PRIVATE_MODEL_OR_TOKEN'
    payload = child(tmp_path, request)
    assert payload['status'] == 'FAILED'
    assert 'PRIVATE_MODEL_OR_TOKEN' not in json.dumps(payload)
    assert 'ValidationError' not in payload['error']


# These test-only admission plans exercise real numerical dispatch, not admission proof.
_PRIVATE_LINDBLAD = '''
import sys
from pathlib import Path
from qsim import worker
from qsim.lindblad_models import LindbladRequest
from qsim.physics_results import AllocationPlan

def fixture_plan(self):
    dimension = self._physical_checks()
    return AllocationPlan('synthetic-test-only', 'worker-private-numerics',
        {'hilbert': dimension}, (), 0, {}, self._result_plan(dimension))
LindbladRequest.admission_plan = fixture_plan
worker.simulate(Path(sys.argv[1]), Path(sys.argv[2]))
'''


@pytest.mark.parametrize('operation,solver,expected', [
    ('evolve', 'se', ['times', 'expectations', 'state_times', 'states']),
    ('evolve', 'me', ['times', 'expectations', 'state_times', 'states']),
    ('evolve', 'mc', ['times', 'expectations', 'state_times', 'states']),
    ('propagator', 'se', ['times', 'propagators']),
    ('propagator', 'me', ['times', 'propagators']),
    ('spectrum', None, ['eigenvalues', 'eigenvectors']),
    ('steadystate', None, ['expectations', 'steady_state']),
])
def test_private_real_lindblad_child_result_families(tmp_path, operation, solver, expected):
    body = {'engine': 'lindblad', 'schema_version': 1, 'operation': operation,
        'model': {'modes': [{'name': 'q', 'kind': 'oscillator', 'dimension': 2, 'frequency': .017}]}}
    if operation in ('evolve', 'propagator'):
        body.update(solver=solver, times=[7., 8., 10.])
    if operation in ('evolve', 'steadystate'):
        body.update(observables=[{'name': 'number', 'operator': {'kind': 'product', 'factors': {'q': 'n'}}}])
    if operation == 'evolve':
        body.update(store_states='all', initial_state={'kind': 'product', 'modes': {'q': {'kind': 'fock', 'index': 1}}})
    if solver in ('me', 'mc') or operation == 'steadystate':
        body['model']['collapse'] = [{'kind': 'relaxation', 'mode': 'q', 'T1': 3.}]
    if solver == 'mc':
        body.update(seed=42, trajectories=8)
    if operation == 'spectrum':
        body.update(evals_count=2, return_vectors=True)
    payload = child(tmp_path, body, code=_PRIVATE_LINDBLAD)
    assert payload['status'] == 'SUCCEEDED', payload
    assert list(payload['summary']['arrays']) == expected
    with np.load(io.BytesIO(base64.b64decode(payload['artifact'])), allow_pickle=False) as result:
        metadata = json.loads(str(result['metadata_json']))
        assert metadata['policy_revision'] == 'synthetic-test-only'
        assert metadata['operation'] == operation
        assert all(np.isfinite(result[name]).all() for name in expected)
        if operation == 'spectrum':
            np.testing.assert_allclose(result['eigenvalues'], [0., .017], atol=1e-12)
        elif operation == 'steadystate':
            np.testing.assert_allclose(result['steady_state'], np.diag([1., 0.]), atol=1e-12)
        elif operation == 'propagator':
            np.testing.assert_allclose(result['propagators'][0], np.eye(2 if solver == 'se' else 4), atol=1e-12)
        elif solver in ('se', 'me'):
            expected_n = np.ones(3) if solver == 'se' else np.exp(-np.array([0., 1., 3.])/3)
            np.testing.assert_allclose(result['expectations'][:, 0], expected_n, atol=1e-7)


def test_private_scqubits_export_runs_real_child_without_enabling_admission(tmp_path):
    code = '''
import sys
from pathlib import Path
from qsim import worker
from qsim.scqubits_models import ScqubitsRequest
from qsim.physics_results import AllocationPlan

def fixture_plan(self):
    dimension = self._physical_checks()
    return AllocationPlan('synthetic-test-only', 'worker-private-export',
        {'hilbert': dimension}, (), 0, {}, self._result_plan(dimension))
ScqubitsRequest.admission_plan = fixture_plan
worker.simulate(Path(sys.argv[1]), Path(sys.argv[2]))
'''
    payload = child(tmp_path, scqubits('export_for_lindblad'), code=code)
    assert payload['status'] == 'SUCCEEDED', payload
    with np.load(io.BytesIO(base64.b64decode(payload['artifact'])), allow_pickle=False) as result:
        metadata = json.loads(str(result['metadata_json']))
        assert metadata['energy_reference'] == 'ground'
        assert metadata['lindblad_request']['schema_version'] == 1
        np.testing.assert_allclose(np.diag(result['hamiltonian']), result['eigenvalues'], atol=1e-12)


@pytest.mark.parametrize('exception,category', [
    (ValueError('invalid_model: PRIVATE_MODEL'), 'invalid_model'),
    (ValueError('nonunique_stationary_state: PRIVATE_MODEL'), 'nonunique_stationary_state'),
    (RuntimeError('native_nonconvergence: PRIVATE_PATH'), 'native_nonconvergence'),
    (RuntimeError('PRIVATE_TOKEN'), 'execution_failed'),
    (MemoryError('PRIVATE_PATH'), 'resource_limit'),
])
def test_failure_guidance_is_fixed_and_preserves_only_bounded_counts(exception, category):
    exception.work = {'integrate_calls': '123', 'rhs_evaluations_upper_bound': '999'}
    payload = worker._physics_failure(exception)
    assert payload['error'].startswith(category+':')
    assert 'PRIVATE' not in json.dumps(payload)
    assert payload['summary']['work'] == exception.work
    exception.work = {'PRIVATE_MODEL': 'secret'}
    assert 'summary' not in worker._physics_failure(exception)


def test_root_physics_execution_fails_closed_before_native_import(monkeypatch, tmp_path):
    monkeypatch.setattr(worker.os, 'geteuid', lambda: 0)
    output = tmp_path/'out.json'
    worker.simulate_physics(scqubits(), output)
    payload = json.loads(output.read_text())
    assert payload['status'] == 'FAILED'
    assert payload['error'].startswith('execution_policy_mismatch:')


@pytest.mark.parametrize('body', [
    {**scqubits(), 'engine': 'qsim'},
    {**scqubits(), 'input_format': 'stim'},
    {**scqubits(), 'resources': {'gpus': 1}},
    {**scqubits(), 'execution_context': {'backend': 'gpu'}},
])
def test_engine_format_and_client_policy_overrides_fail_closed_in_real_child(tmp_path, body):
    payload = child(tmp_path, body)
    assert payload['status'] == 'FAILED'
    assert 'artifact' not in payload


@pytest.mark.parametrize('problem', ['missing', 'oversize', 'malformed', 'status', 'extra', 'base64', 'summary'])
def test_parent_rejects_invalid_physics_completion(tmp_path, problem):
    output = tmp_path/'out.json'
    payload = {'status': 'SUCCEEDED', 'summary': {}, 'artifact': base64.b64encode(b'fixture').decode()}
    if problem == 'missing':
        pass
    elif problem == 'oversize':
        with output.open('wb') as stream:
            stream.truncate(12*1024**2+1)
    elif problem == 'malformed':
        output.write_text('PRIVATE_MODEL_NOT_JSON')
    else:
        if problem == 'status':
            payload['status'] = 'RUNNING'
        elif problem == 'extra':
            payload['private_token'] = 'PRIVATE_TOKEN'
        elif problem == 'base64':
            payload['artifact'] = 'not base64'
        else:
            payload['summary'] = {'oversized': 'x'*65536}
        output.write_text(json.dumps(payload))
    rejected = worker._read_physics_payload(output)
    assert rejected['status'] == 'FAILED'
    assert rejected['error'].startswith('invalid_result:')
    assert 'PRIVATE' not in json.dumps(rejected)


def test_parent_rejects_symlink_and_raw_failure_details(tmp_path):
    private = tmp_path/'private.json'
    private.write_text('{"status":"FAILED","error":"PRIVATE_TOKEN"}')
    output = tmp_path/'out.json'
    output.symlink_to(private)
    assert worker._read_physics_payload(output)['error'].startswith('invalid_result:')
    assert worker._read_physics_payload(private)['error'].startswith('invalid_result:')


def test_private_real_josim_child_preserves_shorter_native_rows_and_sfq(tmp_path):
    binary = os.environ.get('WQS_TEST_JOSIM_BINARY', '/opt/josim/bin/josim-cli')
    if not Path(binary).is_file():
        pytest.skip('Pinned JoSIM executable unavailable; native worker/image acceptance remains open')
    code = '''
import os,sys
from pathlib import Path
from qsim import worker,josim_adapter
from qsim.josim_models import JoSIMRequest
JoSIMRequest.admission_plan = JoSIMRequest._certified_allocation_plan
josim_adapter._BINARY = Path(os.environ['WQS_TEST_JOSIM_BINARY'])
worker.simulate(Path(sys.argv[1]), Path(sys.argv[2]))
'''
    payload = child(tmp_path, {'engine': 'josim', 'input_format': 'josim_netlist', 'schema_version': 1,
        'netlist': Path('tests/fixtures/physics/josim/sfq.cir').read_text()}, code=code,
        extra_env={'WQS_TEST_JOSIM_BINARY': binary})
    assert payload['status'] == 'SUCCEEDED', payload
    with np.load(io.BytesIO(base64.b64decode(payload['artifact'])), allow_pickle=False) as result:
        times, traces = result['times'], result['traces']
        assert traces.shape == (999, 5)
        assert payload['summary']['arrays']['times']['shape'] == [999]
        assert np.all(np.diff(times) > 0)
        window = (times >= 50e-12) & (times <= 200e-12)
        for column in (0, 2):
            assert abs(np.trapezoid(traces[window, column], times[window])/2.0678338484619295e-15-1) < .02
        metadata = json.loads(str(result['metadata_json']))
        assert metadata['time_units'] == 's'
        assert metadata['binary_sha256'] == '0bca2802d5e152a1b4325cc4a9f4e8e5b01331d705d45ea757c72e0cb1afe676'


@pytest.mark.parametrize('problem', ['numeric_limit', 'metadata_limit', 'wrong_shape', 'nonfinite'])
def test_bad_numeric_result_is_never_a_success_callback(problem, monkeypatch, tmp_path):
    from dataclasses import replace
    from qsim.scqubits_models import ScqubitsRequest
    from qsim import scqubits_adapter
    from qsim.physics_results import PhysicsExecutionResult
    req = ScqubitsRequest.model_validate(scqubits())
    original = req.admission_plan()
    if problem == 'numeric_limit':
        forged = replace(original, result=replace(original.result, numeric_bytes=1))
        monkeypatch.setattr(ScqubitsRequest, 'admission_plan', lambda self: forged)
    else:
        result = scqubits_adapter._run_native(req)
        if problem == 'metadata_limit':
            result = PhysicsExecutionResult(result.arrays, {'oversize': 'x'*65536}, result.work)
        elif problem == 'wrong_shape':
            result = PhysicsExecutionResult({'eigenvalues': np.zeros(5)}, result.metadata, result.work)
        else:
            result.arrays['eigenvalues'][0] = np.nan
        monkeypatch.setattr(scqubits_adapter, 'run_scqubits', lambda *a, **k: result)
    output = tmp_path/'out.json'
    worker.simulate_physics(scqubits(), output)
    payload = json.loads(output.read_text())
    assert payload['status'] == 'FAILED'
    assert 'artifact' not in payload
    assert payload['summary']['work']['eigensystem_calls'] == '1'


def test_native_work_is_preserved_in_failed_child_payload(monkeypatch, tmp_path):
    from qsim import scqubits_adapter
    def failed(*args, **kwargs):
        error = RuntimeError('native_nonconvergence: PRIVATE_PATH_AND_MODEL')
        error.work = {'integrate_calls': '9', 'rhs_evaluations_upper_bound': '12345'}
        raise error
    monkeypatch.setattr(scqubits_adapter, 'run_scqubits', failed)
    output = tmp_path/'out.json'
    worker.simulate_physics(scqubits(), output)
    payload = worker._read_physics_payload(output)
    assert payload['status'] == 'FAILED'
    assert payload['error'].startswith('native_nonconvergence:')
    assert payload['summary'] == {'work': {'integrate_calls': '9', 'rhs_evaluations_upper_bound': '12345'}}
    assert 'PRIVATE' not in json.dumps(payload)


def test_parent_checks_actual_callback_encoding_not_only_file_bytes(tmp_path):
    output = tmp_path/'out.json'
    payload = {'status': 'SUCCEEDED', 'summary': {'text': 'é'*1000},
               'artifact': 'A'*(12*1024**2-4096)}
    output.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    assert output.stat().st_size < 12*1024**2
    assert len(json.dumps(payload).encode()) > 12*1024**2
    assert worker._read_physics_payload(output)['status'] == 'FAILED'


@pytest.mark.parametrize('rows,columns', [(4097, 5), (10, 6), (0, 5)])
def test_josim_actual_shape_never_exceeds_reserved_ceiling(rows, columns, monkeypatch, tmp_path):
    from qsim.josim_models import JoSIMRequest
    from qsim import josim_adapter
    from qsim.physics_results import PhysicsExecutionResult
    monkeypatch.setattr(JoSIMRequest, 'admission_plan', JoSIMRequest._certified_allocation_plan)
    monkeypatch.setattr(josim_adapter, 'run_josim', lambda *a, **k:
        PhysicsExecutionResult({'times': np.arange(rows, dtype=float),
                                'traces': np.zeros((rows, columns))}, {}, {}))
    output = tmp_path/'out.json'
    worker.simulate_physics({'engine': 'josim', 'input_format': 'josim_netlist', 'schema_version': 1,
        'netlist': Path('tests/fixtures/physics/josim/sfq.cir').read_text()}, output)
    assert json.loads(output.read_text())['status'] == 'FAILED'


def test_malformed_engine_type_fails_closed_in_real_child(tmp_path):
    body = scqubits()
    body['engine'] = ['PRIVATE_MODEL']
    payload = child(tmp_path, body)
    assert payload['status'] == 'FAILED'
    assert 'PRIVATE_MODEL' not in json.dumps(payload)


def test_missing_certificate_does_not_import_native_solvers_in_child(tmp_path):
    code = '''
import sys
from pathlib import Path
from qsim import worker
assert not any(name in sys.modules for name in ('numpy','qutip','scqubits','scipy','qsim.auth','qsim.store','qsim.controller'))
worker.simulate(Path(sys.argv[1]), Path(sys.argv[2]))
assert not any(name in sys.modules for name in ('qutip','scqubits','scipy','clifft'))
'''
    payload = child(tmp_path, scqubits('export_for_lindblad'), code=code)
    assert payload['status'] == 'FAILED'
    assert payload['error'].startswith('resource_limit:')
