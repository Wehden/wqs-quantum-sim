"""Fixed qsim discovery. Only the disposable importer permits widths over 26."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import subprocess
import sys
import tempfile
import time

FIXTURES = Path(__file__).with_name('cases.json')
GIB = 1024**3


def request_for(index, trial):
    fixtures = json.loads(FIXTURES.read_text())
    fmt, width, depth, operation, shots, kind = fixtures['screen'][index]
    rng = random.Random(fixtures['circuit_seed'] + width + depth)
    if fmt == 'qasm2':
        header = f'OPENQASM 2.0; include "qelib1.inc"; qreg q[{width}];'
        if shots:
            header += f'creg c[{width}];'
    else:
        header = f'OPENQASM 3.0; include "stdgates.inc"; qubit[{width}] q;'
        if shots:
            header += f'bit[{width}] c;'
    if kind == 'control':
        gates = ['x q[0];', 'x q[3];']
    elif kind == 'ghz':
        gates = ['h q[0];', *(f'cx q[{i}],q[{i+1}];' for i in range(width-1))]
    else:
        gates = []
        for layer in range(depth):
            for q in range(width):
                theta = rng.uniform(-2.8, 2.8)
                name = ('rx', 'ry', 'rz')[(layer+q) % 3]
                gates.append(f'{name}({theta:.14g}) q[{q}];')
            for q in range(layer % 2, width-1, 2):
                gates.append(f'cx q[{q}],q[{q+1}];')
    if shots:
        gates.append('measure q -> c;' if fmt == 'qasm2' else 'c = measure q;')
    order = 'little' if fmt == 'qasm3' else 'big'
    indices = [0, (1 << width)-1, (1 << (width-1)) | (1 << (width-4))]
    while len(indices) < fixtures['bitstring_count']:
        value = rng.randrange(1 << width)
        if value not in indices:
            indices.append(value)
    bitstrings = [format(i, f'0{width}b') for i in indices]
    if order == 'little':
        bitstrings = [s[::-1] for s in bitstrings]
    return dict(engine='qsim', input_format=fmt, operation=operation, shots=shots if shots else 1,
                circuit=header+''.join(gates), seed=fixtures['measurement_seed']+trial,
                bit_order=order, bitstrings=bitstrings), indices


def child(args):
    start = time.monotonic()
    resource.setrlimit(resource.RLIMIT_AS, (args.as_gib*GIB,)*2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (12*1024**2,)*2)
    import numpy as np
    import qsimcirq
    import cirq
    from qsim.qasm_adapter import prepare, final_memory

    fmt, width, depth, operation, shots, kind = json.loads(FIXTURES.read_text())['screen'][args.child]
    state_bytes = 8 * 2**width
    assert 2*state_bytes + GIB <= args.ram_gib*GIB*.75
    gpu = args.arm != 'cpu'
    cp = None
    if gpu:
        import cupy as cp
        assert cp.cuda.runtime.getDeviceCount() == 1
        cp.cuda.runtime.free(0)
        free, total = cp.cuda.runtime.memGetInfo()
        # The worker verifies physical VRAM with NVML. CUDA can reserve a
        # larger fraction of the smaller card before reporting usable memory.
        usable_fraction = .95 if args.vram_gib == 12 else .98
        assert total >= args.vram_gib*GIB*usable_fraction and free >= total*.90, (
            f"CUDA reports {free / 2**20:.0f} MiB free of {total / 2**20:.0f} MiB usable"
        )
        assert 2*state_bytes + GIB <= total*.75
    imported = time.monotonic()
    request, indices = request_for(args.child, args.trial)
    circuit, metadata = prepare(request)
    simulator = qsimcirq.QSimSimulator(seed=request['seed'], qsim_options=qsimcirq.QSimOptions(
        cpu_threads=args.threads, use_gpu=gpu, gpu_mode=1 if args.arm == 'custatevec' else 0,
        max_fused_gate_size=4))
    native = simulator._sim_module
    if gpu:
        expected = 'qsimcirq.qsim_cuda' if args.arm == 'cuda' else 'qsimcirq.qsim_custatevec'
        assert native.__name__ == expected, native.__name__
        used_before = total - cp.cuda.runtime.memGetInfo()[0]
    prepared = time.monotonic()
    if shots:
        result = simulator.run(circuit, repetitions=shots)
    else:
        result = simulator.compute_amplitudes(circuit, indices, qubit_order=metadata['qubits'])
    if cp is not None:
        cp.cuda.runtime.deviceSynchronize()
    executed = time.monotonic()
    if shots:
        memory = final_memory(result, metadata, shots)
        assert memory.dtype == np.uint8 and memory.shape == (shots, width) and np.isin(memory, [0,1]).all()
        np.savez_compressed(args.output, classical_memory=memory)
    else:
        amplitude = np.asarray(result, dtype=np.complex128)
        probabilities = np.abs(amplitude)**2
        assert np.isfinite(probabilities).all() and probabilities.shape == (len(indices),)
        np.savez_compressed(args.output, bitstrings=request['bitstrings'], probabilities=probabilities, amplitudes=amplitude)
    finished = time.monotonic()
    if kind == 'control' and shots:
        target = np.zeros(width, dtype=np.uint8)
        target[[0,3]] = 1
        np.testing.assert_array_equal(memory, np.tile(target, (shots,1)))
    elif kind == 'control':
        reference = cirq.Simulator(dtype=np.complex128).compute_amplitudes(circuit, indices, qubit_order=metadata['qubits'])
        np.testing.assert_allclose(amplitude, reference, atol=2e-6, rtol=2e-4)
        assert probabilities[2] > .99999
    elif kind == 'ghz':
        assert np.all(memory == memory[:, :1])
        assert abs(float(memory[:,0].mean())-.5) <= .05
    print(json.dumps({'status':'ok','case':args.child,'width':width,'depth':depth,'shots':shots,'format':fmt,
                      'operation':operation,'arm':args.arm,'threads':args.threads,'fresh_s':finished-start,
                      'import_s':imported-start,'prepare_s':prepared-imported,'solve_s':executed-prepared,
                      'output_s':finished-executed,'native_module':native.__name__,
                      'native_sha256':hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
                      'gpu_base_bytes':used_before if gpu else None,
                      'gpu_usable_bytes':total if gpu else None,
                      'rss_peak_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      'bytes':args.output.stat().st_size}))


def run(args):
    import numpy as np
    fixtures = json.loads(FIXTURES.read_text())
    print(json.dumps({'fixture_sha256':hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),'lane':'qsim'}),flush=True)
    indices = range(len(fixtures['screen'])) if args.cases == 'all' else [int(i) for i in args.cases.split(',')]
    rng = random.Random(184)
    arms = [('cpu',2),('cpu',8)] + [(backend,1) for backend in args.gpu]
    with tempfile.TemporaryDirectory() as directory:
        for index in indices:
            pooled = {}
            _, _, _, _, shots, kind = fixtures['screen'][index]
            for trial in range(args.trials):
                records, results = {}, {}
                ordered = arms.copy()
                rng.shuffle(ordered)
                for arm, threads in ordered:
                    path = Path(directory) / 'result.npz'
                    path.unlink(missing_ok=True)
                    allowed = ('PATH','LD_LIBRARY_PATH','CUDA_VISIBLE_DEVICES','NVIDIA_VISIBLE_DEVICES','PYTHONPATH')
                    env = {k:os.environ[k] for k in allowed if k in os.environ}
                    env.update({k:str(threads) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')})
                    env.update(CUPY_CACHE_DIR=str(Path(directory)/'cupy'), HOME=directory)
                    cmd = [sys.executable,__file__,'--child',str(index),'--output',str(path),'--arm',arm,
                           '--trial',str(trial),'--threads',str(threads),'--as-gib',str(args.as_gib),
                           '--ram-gib',str(args.ram_gib),'--vram-gib',str(args.vram_gib)]
                    # Attribute utilization and peak memory to a single assigned device
                    # during this known child interval. The GPU arm runs alone.
                    telemetry = None
                    log = Path(directory) / 'telemetry.csv'
                    try:
                        if arm != 'cpu':
                            with log.open('w') as fd:
                                telemetry = subprocess.Popen(['nvidia-smi','--query-gpu=memory.used,utilization.gpu',
                                                              '--format=csv,noheader,nounits','--loop-ms=100'],stdout=fd,stderr=subprocess.DEVNULL)
                        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
                        if proc.returncode:
                            record = {'status':'failed','error':proc.stderr[-1500:]}
                        else:
                            record = json.loads(proc.stdout.splitlines()[-1])
                            with np.load(path, allow_pickle=False) as data:
                                results[arm,threads] = dict(data)
                    except subprocess.TimeoutExpired:
                        record = {'status':'timeout'}
                    finally:
                        if telemetry is not None:
                            telemetry.terminate()
                            telemetry.wait(timeout=5)
                            points = [line.split(',') for line in log.read_text().splitlines()]
                            points = [(int(m),int(u)) for m,u in points if m.strip().isdigit() and u.strip().isdigit()]
                            record.update(gpu_peak_sample_mib=max((m for m,u in points),default=None),
                                          max_gpu_sample_percent=max((u for m,u in points),default=None))
                    records[arm,threads] = record
                ref = results.get(('cpu',2))
                for key, record in records.items():
                    if record['status'] == 'ok':
                        try:
                            assert ref is not None
                            result = results[key]
                            if shots:
                                assert result['classical_memory'].shape == ref['classical_memory'].shape
                                pooled.setdefault(key,[]).append(result['classical_memory'])
                            else:
                                np.testing.assert_allclose(result['amplitudes'],ref['amplitudes'],atol=2e-6,rtol=2e-4)
                                np.testing.assert_allclose(result['probabilities'],ref['probabilities'],atol=2e-6,rtol=2e-4)
                                if kind == 'random':
                                    np.testing.assert_allclose(result['amplitudes']*2**(record['width']/2),
                                                               ref['amplitudes']*2**(record['width']/2),atol=3e-4,rtol=2e-3)
                            assert record['rss_peak_kib']*1024 <= args.ram_gib*GIB*.75
                            if key[0] != 'cpu':
                                assert record['gpu_peak_sample_mib'] is not None
                                assert record['gpu_peak_sample_mib'] <= args.vram_gib*1024*.75
                            record['correctness'] = 'shape/analytic control' if shots else 'pass'
                        except (AssertionError, ValueError) as error:
                            record.update(status='incorrect',reason=str(error)[:1000])
                    print(json.dumps({**record,'case':index,'case_spec':fixtures['screen'][index],
                                      'arm':key[0],'threads':key[1],'trial':trial}),flush=True)
            if shots and args.trials >= 5:
                ref = np.concatenate(pooled[('cpu',2)]) if ('cpu',2) in pooled else None
                if ref is None:
                    continue
                for key, rows in pooled.items():
                    if key == ('cpu',2):
                        continue
                    arr = np.concatenate(rows)
                    # Declared marginals and nearest-neighbor parity, with a
                    # conservative simultaneous Hoeffding interval for differences.
                    def metrics(a):
                        return np.concatenate([a.mean(axis=0),(a[:,:-1]^a[:,1:]).mean(axis=0)])
                    diffs = abs(metrics(arr)-metrics(ref))
                    radius = np.sqrt(.5*np.log(4*len(diffs)/.01))*(1/np.sqrt(len(arr))+1/np.sqrt(len(ref)))
                    max_delta = float(np.max(diffs))
                    print(json.dumps({'case':index,'arm':key[0],'threads':key[1],'sample_trials':len(rows),
                                      'shots_per_arm':len(arr),'max_abs_delta':max_delta,'ci_radius':float(radius),
                                      'distribution_equivalent':bool(max_delta+radius <= .05 and len(rows)==args.trials)}),flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--child', type=int)
    p.add_argument('--output', type=Path)
    p.add_argument('--arm', choices=('cpu','cuda','custatevec'), default='cpu')
    p.add_argument('--trial', type=int, default=0)
    p.add_argument('--threads', type=int, choices=(1,2,8),default=2)
    p.add_argument('--as-gib', type=int, choices=(12,48),default=12)
    p.add_argument('--ram-gib', type=int, choices=(16,64),default=16)
    p.add_argument('--vram-gib', type=int, choices=(12,16,24,80),default=16)
    p.add_argument('--cases',default='all')
    p.add_argument('--trials',type=int,choices=(1,5,20),default=5)
    p.add_argument('--gpu',choices=('cuda','custatevec'),action='append',default=[])
    args=p.parse_args()
    child(args) if args.child is not None else run(args)
