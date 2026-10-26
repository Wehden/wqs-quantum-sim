"""Synthetic pre-physics contracts captured from main 83ffac2 (missing coverage)."""
import base64
import io
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from qsim import release_control, store
from qsim.api import canonical_request
from qsim.auth import digest
from qsim.models import Submission
from test_service import auth, client

# Literal UTF-8 requests and SHA-256 values are independent of canonical_request.
GOLDENS = [
    ({'engine': 'clifft', 'input_format': 'stim', 'operation': 'sample', 'circuit': 'X 0\nM 0'}, '{"bit_order":"big","bitstrings":null,"circuit":"X 0\\nM 0","input_format":"stim","name":"Untitled circuit","operation":"sample","seed":42,"shots":1000}', '54a864ad0985a7f592e657e0a35837d21eeb7f3f6d2fbfcd48ae896694da5e64'),
    ({'engine': 'clifft', 'input_format': 'stim', 'operation': 'basis_probabilities', 'circuit': 'X 0', 'bitstrings': ['0', '1']}, '{"bit_order":"big","bitstrings":["0","1"],"circuit":"X 0","input_format":"stim","name":"Untitled circuit","operation":"basis_probabilities","seed":42,"shots":1000}', '7c76de0f732ad31f2b17481db7a2285131b6a9f11e363cba9142a011fbce66b6'),
    ({'engine': 'clifft', 'input_format': 'qasm2', 'operation': 'basis_probabilities', 'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; x q[0];', 'bitstrings': ['0', '1']}, '{"bit_order":"big","bitstrings":["0","1"],"circuit":"OPENQASM 2.0; include \\"qelib1.inc\\"; qreg q[1]; x q[0];","input_format":"qasm2","name":"Untitled circuit","operation":"basis_probabilities","seed":42,"shots":1000}', '2b868455ef802bb4daf565b12c26b2584c6d72f2220e5f5927cd77c490e83419'),
    ({'engine': 'qsim', 'input_format': 'qasm2', 'operation': 'sample', 'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; creg c[1]; x q[0]; measure q -> c;'}, '{"bit_order":"big","bitstrings":null,"circuit":"OPENQASM 2.0; include \\"qelib1.inc\\"; qreg q[1]; creg c[1]; x q[0]; measure q -> c;","engine":"qsim","input_format":"qasm2","name":"Untitled circuit","operation":"sample","seed":42,"shots":1000}', 'e0eb4d6f44d1da2ed8a8865eb1c9d026eeaa7b79db865df2f68c6673f6b8afa6'),
    ({'engine': 'qsim', 'input_format': 'qasm2', 'operation': 'basis_probabilities', 'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; x q[0];', 'bitstrings': ['0', '1']}, '{"bit_order":"big","bitstrings":["0","1"],"circuit":"OPENQASM 2.0; include \\"qelib1.inc\\"; qreg q[1]; x q[0];","engine":"qsim","input_format":"qasm2","name":"Untitled circuit","operation":"basis_probabilities","seed":42,"shots":1000}', 'd032d8c4ebf44b14fa2aa2533045f743794812e8bc7897b917641b476fbc9dde'),
    ({'engine': 'qsim', 'input_format': 'qasm3', 'operation': 'sample', 'circuit': 'OPENQASM 3.0; include "stdgates.inc"; qubit q; bit c; x q; c = measure q;'}, '{"bindings":{},"bit_order":"big","bitstrings":null,"circuit":"OPENQASM 3.0; include \\"stdgates.inc\\"; qubit q; bit c; x q; c = measure q;","engine":"qsim","input_format":"qasm3","name":"Untitled circuit","operation":"sample","seed":42,"shots":1000}', '47cbd58c786e7b137245f0544e49be8b201583df6dfa6cf44edbc9bc015ae33f'),
    ({'engine': 'qsim', 'input_format': 'qasm3', 'operation': 'basis_probabilities', 'circuit': 'OPENQASM 3.0; include "stdgates.inc"; qubit q; x q;', 'bitstrings': ['0', '1']}, '{"bindings":{},"bit_order":"big","bitstrings":["0","1"],"circuit":"OPENQASM 3.0; include \\"stdgates.inc\\"; qubit q; x q;","engine":"qsim","input_format":"qasm3","name":"Untitled circuit","operation":"basis_probabilities","seed":42,"shots":1000}', '903d0cdfada3d1c228b249a67c6b1445820784d2c711a5c6a12c101de516520f'),
    ({'engine': 'aer', 'input_format': 'qasm2', 'operation': 'sample', 'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; creg c[1]; x q[0]; measure q -> c;'}, '{"bit_order":"big","bitstrings":null,"circuit":"OPENQASM 2.0; include \\"qelib1.inc\\"; qreg q[1]; creg c[1]; x q[0]; measure q -> c;","engine":"aer","input_format":"qasm2","name":"Untitled circuit","operation":"sample","seed":42,"shots":1000}', '293d4dcb2ec5f3ca0895eea507bb47a78d3f4bfe8c3b9775dcfd6e9cfafa2974'),
    ({'engine': 'aer', 'input_format': 'qasm3', 'operation': 'sample', 'circuit': 'OPENQASM 3.0; include "stdgates.inc"; qubit q; bit c; x q; c = measure q;'}, '{"bindings":{},"bit_order":"big","bitstrings":null,"circuit":"OPENQASM 3.0; include \\"stdgates.inc\\"; qubit q; bit c; x q; c = measure q;","engine":"aer","input_format":"qasm3","name":"Untitled circuit","operation":"sample","seed":42,"shots":1000}', '888114ece37bf784b510283124efa4d3ab55d7764d5f1043c6355b91f27666d9'),
]


@pytest.mark.parametrize('body,raw,sha256', GOLDENS,
                         ids=[f"{body['engine']}-{body['input_format']}-{body['operation']}" for body, _, _ in GOLDENS])
def test_frozen_canonical_request_and_native_result(client, tmp_path, body, raw, sha256):
    minimal = Submission(**body)
    assert canonical_request(minimal).encode('utf-8') == raw.encode('utf-8')
    assert digest(raw) == sha256
    explicit = {**body, 'name': 'Untitled circuit'}
    if body['operation'] == 'sample':
        explicit.update(shots=1000, seed=42)
    elif body['input_format'] != 'qasm3':
        explicit.update(shots=1000, seed=42, bit_order='big')
    if body['input_format'] == 'qasm3':
        explicit['bindings'] = {}
    assert canonical_request(Submission(**explicit)) == raw
    headers = {**auth(), 'Idempotency-Key': 'golden-request'}
    first = client.post('/api/jobs', headers=headers, json=body)
    assert first.status_code == 202, first.text
    assert set(first.json()) == {'id', 'name', 'status', 'created', 'updated', 'error', 'summary'}
    job = first.json()['id']
    assert first.headers['Location'] == '/api/jobs/' + job
    assert first.headers['Idempotency-Replayed'] == 'false'
    with store.connect() as db:
        assert db.execute('SELECT request_hash FROM job_idempotency').fetchone()[0] == sha256
    store.init()
    retry = client.post('/api/jobs', headers=headers, json=explicit)
    assert retry.status_code == 202 and retry.json()['id'] == job
    assert retry.headers['Idempotency-Replayed'] == 'true'

    source, output = tmp_path / 'native-request.json', tmp_path / 'native-result.json'
    source.write_text(minimal.model_dump_json())
    subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
                   env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}, check=True)
    completion = json.loads(output.read_text())
    assert set(completion) == {'status', 'summary', 'artifact'}
    assert completion['status'] == 'SUCCEEDED'
    summary = completion['summary']
    assert summary['operation'] == body['operation']
    assert summary['qubits'] == 1
    assert 'elapsed_seconds' in summary
    with np.load(io.BytesIO(base64.b64decode(completion['artifact'])), allow_pickle=False) as arrays:
        if body['operation'] == 'basis_probabilities':
            assert set(arrays.files) == {'bitstrings', 'probabilities'}
            assert arrays['bitstrings'].tolist() == ['0', '1']
            assert arrays['probabilities'] == pytest.approx([0, 1], abs=1e-6)
            assert summary['probabilities'] == pytest.approx([0, 1], abs=1e-6)
        elif body['engine'] == 'clifft':
            assert set(arrays.files) == {'measurements', 'detectors', 'observables', 'exp_vals'}
            assert arrays['measurements'].shape == (1000, 1)
            assert np.all(arrays['measurements'] == 1)
            assert summary['counts'] == {'1': 1000}
        else:
            assert set(arrays.files) == {'classical_memory'}
            assert arrays['classical_memory'].dtype == np.uint8
            assert arrays['classical_memory'].shape == (1000, 1)
            assert np.all(arrays['classical_memory'] == 1)
            assert summary['result_schema'] == 'qasm-samples/1'
            assert summary['counts'] == {'1': 1000}


@pytest.mark.parametrize('engine', ['clifft', 'qsim', 'aer'])
@pytest.mark.parametrize('input_format', ['stim', 'qasm2', 'qasm3'])
@pytest.mark.parametrize('operation', ['sample', 'basis_probabilities'])
def test_frozen_admission_matrix(client, engine, input_format, operation):
    key = (engine, input_format, operation)
    accepted = {(body['engine'], body['input_format'], body['operation']) for body, _, _ in GOLDENS}
    body = {'engine': engine, 'input_format': input_format, 'operation': operation, 'circuit': 'synthetic'}
    if operation == 'basis_probabilities':
        body['bitstrings'] = ['0']
    response = client.post('/api/jobs', headers={**auth(), 'Idempotency-Key': 'matrix'}, json=body)
    assert response.status_code == (202 if key in accepted else 422), response.text
    if key not in accepted:
        assert set(response.json()) == {'detail'}
        assert all(set(error) == {'type', 'loc', 'msg'} for error in response.json()['detail'])
        with store.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM job_idempotency').fetchone()[0] == 0


def test_frozen_maintenance_retry_owner_and_tombstone_errors(client):
    body = {'circuit': 'M 0'}
    headers = {**auth(), 'Idempotency-Key': 'historical'}
    first = client.post('/api/jobs/stim', headers=headers, json=body)
    job = first.json()['id']
    release_control.control('hold', 'a' * 40)
    store.init()
    replay = client.post('/api/jobs/stim', headers=headers, json=body)
    assert replay.json()['id'] == job and replay.headers['Idempotency-Replayed'] == 'true'
    conflict = client.post('/api/jobs/stim', headers=headers, json={**body, 'shots': 1})
    assert conflict.status_code == 409
    assert conflict.json() == {'detail': 'Idempotency-Key was already used with a different request'}
    held = client.post('/api/jobs/stim', headers={**auth('bob'), 'Idempotency-Key': 'historical'}, json=body)
    assert held.status_code == 503 and held.headers['Retry-After'] == '60'
    assert held.json() == {'detail': 'Release maintenance: retry new submissions later'}
    with store.connect() as db:
        db.execute('DELETE FROM jobs WHERE id=?', (job,))
    missing = client.post('/api/jobs/stim', headers=headers, json=body)
    assert missing.status_code == 410
    assert missing.json() == {'detail': 'The idempotent job was deleted or expired; use a new key for new work'}
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM job_idempotency').fetchone()[0] == 1


def test_frozen_capability_mapping_and_dependency_versions(client):
    from importlib.metadata import version
    capabilities = client.get('/api/capabilities', headers=auth()).json()
    assert isinstance(capabilities['engines'], dict)
    assert set(capabilities['engines']) == {'clifft', 'qsim', 'aer'}
    assert capabilities['engines']['clifft'] == {'version': '0.12.0', 'default': True}
    assert version('clifft') == '0.12.0'
    assert version('qiskit') == capabilities['engines']['aer']['importer']['version'] == '2.6.0'
    assert capabilities['engines']['qsim']['input_formats'] == {
        'qasm2': ['basis_probabilities', 'sample'], 'qasm3': ['sample', 'basis_probabilities']}
    assert capabilities['engines']['aer']['input_formats'] == {'qasm2': ['sample'], 'qasm3': ['sample']}
    assert capabilities['client_manages'] == ['call sequences', 'batches', 'quantum state']
    assert capabilities['streaming'] == {'protocol': 'SSE', 'progress': True, 'final_result': True,
        'resume': 'Last-Event-ID', 'incremental_samples': False,
        'reason': 'Workers return complete sample arrays; incremental sample streaming is not enabled'}
