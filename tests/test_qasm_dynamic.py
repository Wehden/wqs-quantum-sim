"""Dynamic-circuit semantics through the isolated worker and complete artifacts."""
import base64
import io

import numpy as np
import pytest

from test_qasm_sampling import run_sample


def memory(result):
    assert result['status'] == 'SUCCEEDED'
    assert result['summary']['engine'].startswith('qsim ')
    with np.load(io.BytesIO(base64.b64decode(result['artifact']))) as artifact:
        return artifact['classical_memory'].copy()


def test_worker_resets_entangled_qubit_before_any_measurement(tmp_path):
    shots = 4096
    # Criterion fixed before execution: the untouched partner has P(1)=0.5 +/-0.05.
    result = run_sample(tmp_path, 'qreg q[2]; creg c[2]; h q[0]; cx q[0],q[1]; '
        'reset q[0]; measure q -> c;', shots=shots)
    samples = memory(result)
    assert samples.shape == (shots, 2)
    assert np.all(samples[:, 0] == 0)
    assert np.mean(samples[:, 1]) == pytest.approx(0.5, abs=0.05)
    assert set(result['summary']['counts']) == {'00', '01'}
    assert sum(result['summary']['counts'].values()) == shots


def test_worker_mid_measurement_collapse_preserves_correlation(tmp_path):
    result = run_sample(tmp_path, 'qreg q[2]; creg c[2]; h q[0]; '
        'measure q[0] -> c[0]; cx q[0],q[1]; measure q[1] -> c[1];', shots=256)
    samples = memory(result)
    assert samples.shape == (256, 2)
    assert np.array_equal(samples[:, 0], samples[:, 1])
    assert set(result['summary']['counts']) == {'00', '11'}
    assert sum(result['summary']['counts'].values()) == 256


def test_worker_repeated_writes_return_final_values_and_preserve_unwritten_bits(tmp_path):
    result = run_sample(tmp_path, 'qreg q[1]; creg c[3]; x q[0]; '
        'measure q[0] -> c[0]; measure q[0] -> c[2]; '
        'reset q[0]; measure q[0] -> c[0];', shots=128)
    samples = memory(result)
    assert samples.shape == (128, 3)
    assert np.all(samples == [0, 0, 1])
    assert result['summary']['counts'] == {'001': 128}
    assert result['summary']['preview'] == [[0, 0, 1]] * 20


def test_worker_initializes_each_shot_and_each_job_independently(tmp_path):
    first = run_sample(tmp_path, 'qreg q[1]; creg c[1]; x q[0]; measure q -> c;', shots=128)
    assert np.all(memory(first) == 1)
    assert first['summary']['counts'] == {'1': 128}
    second = run_sample(tmp_path, 'qreg q[1]; creg c[1]; measure q -> c;', shots=128)
    assert np.all(memory(second) == 0)
    assert second['summary']['counts'] == {'0': 128}


@pytest.mark.parametrize('condition', [0, 1])
def test_worker_rejects_true_and_false_classical_conditions_without_fallback(tmp_path, condition):
    result = run_sample(tmp_path, 'qreg q[2]; creg c[1]; x q[0]; '
        f'measure q[0] -> c[0]; if(c=={condition}) x q[1];')
    assert result['status'] == 'FAILED'
    assert result['error'].startswith('unsupported_feature:')
    assert 'artifact' not in result
    assert 'summary' not in result
