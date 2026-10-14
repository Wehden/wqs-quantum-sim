import numpy as np
import pytest
from qsim.aer_adapter import sample
from test_aer_import import request


def test_final_memory_order_and_overwrites():
    body = 'qubit[2] q; bit single; bit[2] mem; bit spare; x q[0]; single=measure q[0]; mem[1]=measure q[0]; reset q[0]; mem[1]=measure q[0]; mem[0]=measure q[1];'
    memory, metadata = sample(request(body, 'qasm3'), cpu_threads=1)
    assert memory.dtype == np.uint8 and memory.shape == (32, 4)
    assert np.all(memory == [1, 0, 0, 0])
    assert metadata['registers'] == [dict(name='single', size=1, offset=0),
                                    dict(name='mem', size=2, offset=1),
                                    dict(name='spare', size=1, offset=3)]



@pytest.mark.parametrize('seed', [0, 2**32, 2**63-1])
def test_full_seed_repeatable(seed):
    body = request('qreg q[3]; creg c[3]; h q; measure q->c;', shots=128)
    body['seed'] = seed
    first, _ = sample(body, cpu_threads=1)
    second, _ = sample(body, cpu_threads=1)
    assert np.array_equal(first, second)


def test_high_seed_bits_not_truncated():
    body = request('qreg q[3]; creg c[3]; h q; measure q->c;', shots=128)
    body['seed'] = 0
    first, _ = sample(body, cpu_threads=1)
    body['seed'] = 2**32
    second, _ = sample(body, cpu_threads=1)
    assert not np.array_equal(first, second)


def test_thread_budget_is_internal_fixed():
    with pytest.raises(ValueError, match='thread budget'):
        sample(request('qreg q[1]; creg c[1]; measure q->c;'), cpu_threads=0)
