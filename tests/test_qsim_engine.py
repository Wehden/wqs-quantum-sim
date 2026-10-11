import json
import os
import subprocess
import sys
import pytest
from qsim import controller, settings, store
from test_transactions import client, headers
from test_dispatch import Sky, dispatch


def run_worker(tmp_path, circuit, bitstrings, engine='qsim', bit_order='big', success=True):
    source=tmp_path/'request.json'; output=tmp_path/'result.json'
    source.write_text(json.dumps(dict(engine=engine,input_format='qasm2',operation='basis_probabilities',circuit=circuit,bitstrings=bitstrings,bit_order=bit_order)))
    result=subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(output)],env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'},capture_output=True,timeout=30)
    if not success:
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stderr.decode()
    return json.loads(output.read_text())['summary']['probabilities']


@pytest.mark.parametrize('order',['big','little'])
def test_numeric_register_order_idle_qubits_and_engine_agreement(tmp_path,order):
    circuit='OPENQASM 2.0; include "qelib1.inc"; qreg a[12]; qreg b[1]; ry(pi/3) a[10]; cx a[10],b[0]; x a[0];'
    # Numeric order across two registers, with nine idle qubits before a[10].
    bits=['1000000000000','1000000000101','0000000000000']
    if order=='little':bits=[b[::-1] for b in bits]
    qsim=run_worker(tmp_path,circuit,bits,bit_order=order)
    clifft=run_worker(tmp_path,circuit,bits,engine='clifft',bit_order=order)
    assert qsim == pytest.approx([.75,.25,0],abs=1e-6)
    assert qsim == pytest.approx(clifft,abs=1e-6)


def test_qsim_rejects_nonunitary_and_oversized_inputs(tmp_path):
    prefix='OPENQASM 2.0; include "qelib1.inc"; '
    for suffix in ['qreg q[27];','qreg q[2]; reset q[0];','qreg q[2]; creg c[2]; measure q -> c;','qreg q[2]; gate foo a { x a; } foo q[0];']:
        run_worker(tmp_path,prefix+suffix,['00'],success=False)


def test_engine_selection_is_idempotent_and_validated(client):
    body={'circuit':'OPENQASM 2.0; qreg q[1];','bitstrings':['0'],'engine':'qsim'}
    first=client.post('/api/jobs/qasm2',headers=headers(),json=body)
    assert first.status_code==202
    assert client.post('/api/jobs/qasm2',headers=headers(),json=body).json()['id']==first.json()['id']
    assert client.post('/api/jobs/qasm2',headers=headers(),json={**body,'engine':'clifft'}).status_code==409
    assert client.post('/api/jobs/stim',headers=headers(),json={'circuit':'M 0','engine':'qsim'}).status_code==422
    assert client.post('/api/jobs',headers=headers(),json={'circuit':'M 0','engine':'qsim'}).status_code==422
    # An explicit default is identical to an omitted default, including old hashes.
    default={k:v for k,v in body.items() if k!='engine'}
    old=client.post('/api/jobs/qasm2',headers=headers('default'),json=default)
    assert client.post('/api/jobs/qasm2',headers=headers('default'),json={**default,'engine':'clifft'}).json()['id']==old.json()['id']


def test_workers_from_previous_image_are_not_reused(client,monkeypatch):
    fake=Sky();monkeypatch.setattr(controller,'sky_client',lambda:fake)
    def submit(key):return client.post('/api/jobs/stim',headers=headers(key),json={'circuit':'M 0'}).json()['id']
    first=submit('first');controller.tick();fake.finish(dispatch(first)['launch_id'])
    with store.connect() as db:db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?",(first,))
    monkeypatch.setattr(settings,'WORKER_IMAGE','new-image-digest')
    submit('second');controller.tick()
    assert [c[0] for c in fake.calls]==['launch','launch']
