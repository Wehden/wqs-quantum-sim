#!/bin/bash
set -euo pipefail
image=${1:-worker:local}
# -i feeds the test script to Python in the image; no host temporary markers.
docker run --rm -i --entrypoint /usr/local/bin/python3 "$image" - <<'PY'
from pathlib import Path
import subprocess,time,signal
for log,marker in [('apt-ssh-setup','apt_ssh_setup_complete'),('runtime-setup','ray_skypilot_installation_complete'),('env-setup','env_setup_complete')]:
 p=Path('/tmp')/(log+'.log');m=Path('/tmp')/marker;p.write_text('first\n')
 proc=subprocess.Popen(['/usr/local/bin/tail','-f','-n','+1',str(p)],stdout=subprocess.PIPE)
 time.sleep(.3);assert proc.poll() is None
 with p.open('a') as f:f.write('last\n')
 m.touch();out=proc.communicate(timeout=3)[0];assert out==b'first\nlast\n' and proc.returncode==0
 m.unlink();p.with_suffix('.failed').touch()
 assert subprocess.check_output(['/usr/local/bin/tail','-f','-n','+1',str(p)],timeout=3)==out
 assert subprocess.check_output(['/usr/local/bin/tail','-n','1',str(p)])==b'last\n'
print('All startup followers: wait, completion, failed marker, drain and GNU passthrough passed')
PY

# Verify the same package/module invocation used by controller and isolated child.
docker run --rm -i --entrypoint /opt/qsim-venv/bin/python "$image" - <<'PY'
import json, os, pathlib, subprocess, sys, tempfile
from qsim.qasm_adapter import SUPPORTED_COMBINATIONS
assert not ({'cirq', 'numpy', 'qsimcirq', 'clifft'} & sys.modules.keys())
assert pathlib.Path('/opt/qsim/qsim/qasm_adapter.py').is_file()
assert not pathlib.Path('/opt/qsim/qsim/store.py').exists()
env = {'PATH': os.environ['PATH'], 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
requests = [
    {'engine': 'clifft', 'input_format': 'stim', 'operation': 'sample',
     'circuit': 'X 0\nM 0', 'shots': 4, 'seed': 42},
    {'engine': 'qsim', 'input_format': 'qasm2', 'operation': 'basis_probabilities',
     'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; x q[0];',
     'bitstrings': ['0', '1'], 'bit_order': 'big'},
]
with tempfile.TemporaryDirectory() as directory:
    source, output = pathlib.Path(directory)/'input.json', pathlib.Path(directory)/'output.json'
    for request in requests:
        source.write_text(json.dumps(request))
        subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
                       cwd='/opt/qsim', env=env, check=True)
        result = json.loads(output.read_text())
        assert result['status'] == 'SUCCEEDED' and result['artifact']
        if request['engine'] == 'qsim':
            assert result['summary']['probabilities'][1] > .99999
        else:
            assert result['summary']['counts'] == {'1': 4}
subprocess.run([sys.executable, '-c', """
from qsim.qasm_adapter import prepare, final_memory
import qsimcirq
circuit, metadata = prepare(dict(engine='qsim',input_format='qasm2',operation='sample',shots=4,
    circuit='OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; creg c[1]; x q[0]; measure q -> c;'))
result = qsimcirq.QSimSimulator(seed=42).run(circuit,repetitions=4)
assert final_memory(result,metadata,4).tolist() == [[1]]*4
"""], cwd='/opt/qsim', env=env, check=True)
print('Worker package, isolated module entry point, both engines and adapter passed')
PY
