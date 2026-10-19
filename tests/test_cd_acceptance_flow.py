import io
import json
from pathlib import Path

import numpy as np
import pytest

from test_cd_acceptance import hook
from test_cd_release import SHA
from tools.cd import acceptance


@pytest.mark.parametrize('failure', [None, 'failed-job', 'wrong-owner', 'lost-hold', 'timeout'])
def test_live_flow_evidence_and_failures(tmp_path, monkeypatch, failure):
    verifier = hook(tmp_path)
    state_dir = Path(verifier.config['state_dir'])
    ledger = {'sha': SHA, 'status': 'deploying', 'images': {}}
    (state_dir / 'deployment.json').write_text(json.dumps(ledger))
    monkeypatch.setattr(verifier, 'roles', lambda expected: None)
    probes = []
    def probe(native=False):
        probes.append(native)
        if failure == 'lost-hold':
            raise ValueError('hold lost')
        return {'cleaned': True, 'heartbeat': len(probes), 'native_succeeded': native}
    monkeypatch.setattr(verifier, 'probe', probe)
    submitted = {}
    def request(path, body=None, key=None, external=False):
        if failure == 'timeout':
            raise TimeoutError()
        if path == '/api/me':
            return json.dumps({'id': 'wrong' if failure == 'wrong-owner' else 'verifier'}).encode()
        if external:
            return b'{"status":"ok"}'
        if path == '/readyz':
            return b'{"status":"ready"}'
        if body is not None:
            assert key.startswith('release:' + SHA + ':')
            intention = json.loads(verifier.receipt_path.read_text())
            assert intention['status'] == 'submitting' and intention['next_case']
            job = f'{len(submitted)+1:032x}'
            submitted[job] = body
            return json.dumps({'id': job}).encode()
        job = path.split('/')[3]
        body = submitted[job]
        probability = body['operation'] == 'basis_probabilities'
        if path.endswith('/result'):
            buffer = io.BytesIO()
            arrays = ({'bitstrings': np.asarray(['0', '1']), 'probabilities': np.asarray([0., 1.])}
                      if probability else
                      {'measurements' if body['input_format'] == 'stim' else 'classical_memory': np.ones((16,1), dtype=np.uint8)})
            np.savez_compressed(buffer, **arrays)
            return buffer.getvalue()
        return json.dumps({'id': job, 'status': 'FAILED' if failure == 'failed-job' else 'SUCCEEDED',
            'summary': {'engine': body['engine'] + ' test', 'operation': body['operation'],
                        'shots': 16, 'counts': {'1': 16}, 'bitstrings': ['0','1'], 'probabilities': [0,1]}}).encode()
    monkeypatch.setattr(verifier, 'request', request)
    if failure:
        with pytest.raises((ValueError, TimeoutError)):
            verifier.execute()
        if verifier.receipt_path.exists():
            assert json.loads(verifier.receipt_path.read_text())['status'] != 'verified'
    else:
        result = verifier.execute()
        assert set(result['cases']) == set(acceptance.cases())
        assert len(submitted) == 8 and probes[-1] is True
        assert json.loads(verifier.receipt_path.read_text())['status'] == 'verified'
    # This hook never edits the parent's deployment ledger, admission or jobs.
    assert json.loads((state_dir / 'deployment.json').read_text()) == ledger
