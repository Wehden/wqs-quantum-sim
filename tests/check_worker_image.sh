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
assert not ({'cirq', 'numpy', 'qsimcirq', 'clifft', 'qiskit', 'qiskit_aer'} & sys.modules.keys())
assert pathlib.Path('/opt/qsim/qsim/qasm_adapter.py').is_file()
assert pathlib.Path('/opt/qsim/qsim/aer_adapter.py').is_file()
assert not pathlib.Path('/opt/qsim/qsim/store.py').exists()
env = {'PATH': os.environ['PATH'], 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
requests = [
    {'engine': 'clifft', 'input_format': 'stim', 'operation': 'sample',
     'circuit': 'X 0\nM 0', 'shots': 4, 'seed': 42},
    {'engine': 'qsim', 'input_format': 'qasm2', 'operation': 'basis_probabilities',
     'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; x q[0];',
     'bitstrings': ['0', '1'], 'bit_order': 'big'},
    {'engine': 'qsim', 'input_format': 'qasm2', 'operation': 'sample', 'shots': 4, 'seed': 2**63-1,
     'circuit': 'OPENQASM 2.0; include "qelib1.inc"; gate turn(a) t { rx(a) t; } qreg q[1]; creg c[1]; turn(pi) q[0]; measure q -> c;'},
    {'engine': 'qsim', 'input_format': 'qasm3', 'operation': 'sample', 'shots': 4, 'seed': 42,
     'bindings': {'theta': 3.141592653589793},
     'circuit': 'OPENQASM 3.0; include "stdgates.inc"; input angle[64] theta; qubit q; bit c; rx(theta) q; c=measure q;'},
    {'engine': 'qsim', 'input_format': 'qasm3', 'operation': 'basis_probabilities',
     'bindings': {'theta': 3.141592653589793}, 'bitstrings': ['0', '1'], 'bit_order': 'big',
     'circuit': 'OPENQASM 3.0; include "stdgates.inc"; input float[64] theta; qubit q; rx(theta) q;'},
    {'engine': 'aer', 'input_format': 'qasm2', 'operation': 'sample', 'shots': 4, 'seed': 2**63-1,
     'circuit': 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; creg c[1]; if(c==0) x q; measure q->c;'},
    {'engine': 'aer', 'input_format': 'qasm3', 'operation': 'sample', 'shots': 4, 'seed': 42,
     'circuit': 'OPENQASM 3.0; include "stdgates.inc"; qubit q; bit c; if(c==false) { x q; } else { reset q; } c=measure q;'},
]
with tempfile.TemporaryDirectory() as directory:
    source, output = pathlib.Path(directory)/'input.json', pathlib.Path(directory)/'output.json'
    for request in requests:
        source.write_text(json.dumps(request))
        subprocess.run([sys.executable, '-m', 'qsim.worker', 'simulate', str(source), str(output)],
                       cwd='/opt/qsim', env=env, check=True)
        result = json.loads(output.read_text())
        assert result['status'] == 'SUCCEEDED' and result['artifact']
        if request['operation'] == 'basis_probabilities':
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
print('Worker package, isolated module entry point, all three engines and adapters passed')
PY

# Source-matched diagnostics and offline source rebuild are test-only artifacts.
audit_image="wqs-physics-audit:${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$"
audit_dir=$(mktemp -d)
trap 'docker image rm "$audit_image" >/dev/null 2>&1 || true; rm -rf "$audit_dir"' EXIT
docker build --target josim-build --tag "$audit_image" --file deploy/Dockerfile .
docker run --rm --network none --read-only --entrypoint cat "$audit_image" /build/audit/josim-cli > "$audit_dir/audit"
chmod 755 "$audit_dir/audit"
docker run --rm -i --user 1000:1000 --cpus 2 --memory 4g --memory-swap 4g \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777 \
    --tmpfs /compile:rw,exec,nosuid,nodev,size=128m,mode=1777 \
    --env WQS_IMAGE_TEST=1 --env PYTHONDONTWRITEBYTECODE=1 \
    --entrypoint /usr/local/bin/python "$audit_image" - \
    PhysicsWorkerImage.test_corresponding_source_rebuild_and_compiler_representation < tests/test_physics_image.py

# Physics checks use the actual package, fresh nonroot children and CPU limits.
# Test code enters over stdin and is never copied into the production image.
docker run --rm -i --user 1000:1000 --cpus 2 --memory 4g --memory-swap 4g \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 \
    --tmpfs /home/qsim:rw,nosuid,nodev,size=16m,uid=1000,gid=1000,mode=700 \
    --mount "type=bind,src=$audit_dir/audit,dst=/audit,readonly" \
    --mount "type=bind,src=$PWD/spec/physics-engines/probe_josim_history.py,dst=/proof/spec/physics-engines/probe.py,readonly" \
    --mount "type=bind,src=$PWD/tests/fixtures/physics/josim,dst=/proof/tests/fixtures/physics/josim,readonly" \
    --env HOME=/tmp --env WQS_IMAGE_TEST=1 --env PYTHONDONTWRITEBYTECODE=1 \
    --entrypoint /opt/qsim-venv/bin/python "$image" - PhysicsWorkerImage.test_package_boundary_and_distribution_notices \
    PhysicsWorkerImage.test_real_certified_scqubits_child_and_closed_admission \
    PhysicsWorkerImage.test_josim_provenance_and_corresponding_source \
    PhysicsWorkerImage.test_maximum_certified_scqubits_shapes \
    PhysicsWorkerImage.test_frozen_native_equivalence_and_bounded_capacity \
    PhysicsWorkerImage.test_real_private_lindblad_and_export_families < tests/test_physics_image.py
