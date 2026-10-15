import numpy as np
import pytest
from qsim.aer_adapter import sample
from test_aer_import import request


@pytest.mark.parametrize('initial,expected', [(0,[0,1,1]), (1,[1,0,0])])
def test_unwritten_partial_register_and_broadcast(initial, expected):
    body = 'qreg q[2]; creg flag[1]; creg out[2]; '
    if initial:
        body += 'x q[0]; measure q[0]->flag[0]; reset q[0]; '
    body += 'if(flag==0) x q; measure q->out;'
    memory, _ = sample(request(body), cpu_threads=1)
    assert np.all(memory == expected)


def test_conditional_reset_and_skipped_measurement_preserve_memory():
    body = 'qubit[2] q; bit flag; bit out; x q[1]; out=measure q[1]; h q[0]; flag=measure q[0]; if(flag==true) { reset q[1]; out=measure q[1]; }'
    memory, _ = sample(request(body, 'qasm3', shots=4096), cpu_threads=1)
    assert np.all(memory[:,0] + memory[:,1] == 1)
    assert memory[:,0].mean() == pytest.approx(.5, abs=.05)


@pytest.mark.parametrize('prep,readout', [('x q[0];',''), ('h q[0];','h q[2];'), ('h q[0]; s q[0];','sdg q[2]; h q[2];')])
def test_teleportation_correction_in_three_bases(prep, readout):
    body = ('qreg q[3]; creg m[1]; creg a[1]; creg out[1]; ' + prep +
            'h q[1]; cx q[1],q[2]; cx q[0],q[1]; h q[0]; measure q[0]->m[0]; '
            'measure q[1]->a[0]; if(a==1) x q[2]; if(m==1) z q[2]; ' + readout +
            'measure q[2]->out[0];')
    memory, _ = sample(request(body, shots=4096), cpu_threads=1)
    assert np.all(memory[:,2] == (1 if prep == 'x q[0];' else 0))
    assert memory[:,0].mean() == pytest.approx(.5, abs=.05)
    assert memory[:,1].mean() == pytest.approx(.5, abs=.05)


def test_latest_write_controls_branch():
    body = 'qreg q[2]; creg c[1]; creg out[1]; x q[0]; measure q[0]->c[0]; reset q[0]; measure q[0]->c[0]; if(c==0) x q[1]; measure q[1]->out[0];'
    memory, _ = sample(request(body), cpu_threads=1)
    assert np.all(memory == [0,1])
