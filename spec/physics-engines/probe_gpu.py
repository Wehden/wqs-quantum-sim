"""Fixed synthetic discovery cases; never a production GPU-policy override.

The suite starts three fresh sanitized children per cell. Each reports its first
cold operation and three warm repeats. Limits precede every native import.
"""
import argparse
import importlib.metadata
import io
import json
import os
import resource
import re
from pathlib import Path
import subprocess
import sys
import time
import traceback

CASES = ('transmon61', 'transmon511', 'rabi2', 'spectrum256')
INIT_PROFILES_GIB = (3, 8, 16, 64)


def process_snapshot():
    status = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in ('VmPeak', 'VmSize', 'VmRSS', 'VmHWM', 'RssAnon'):
            status[key] = value.strip()
    status['cpu_affinity'] = sorted(os.sched_getaffinity(0))
    for name in ('memory.max', 'memory.current', 'cpu.max', 'cpuset.cpus.effective'):
        path = Path('/sys/fs/cgroup') / name
        status[name] = path.read_text().strip() if path.is_file() else 'unavailable'
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', choices=('cpu', 'gpu'), required=True)
    parser.add_argument('--case', choices=CASES + ('cuda_init',))
    parser.add_argument('--init-profile', type=int, choices=INIT_PROFILES_GIB)
    parser.add_argument('--suite', action='store_true')
    parser.add_argument('--gpu-discovery-8g', action='store_true',
                        help='owner-approved fixed numerical discovery profile; never production')
    args = parser.parse_args()
    if args.gpu_discovery_8g and (args.backend != 'gpu' or args.case == 'cuda_init'):
        parser.error('8 GiB discovery profile requires a GPU numerical case or suite')
    if args.init_profile is not None and args.case != 'cuda_init':
        parser.error('initialization profiles are diagnostic-only')
    if args.case == 'cuda_init' and args.backend != 'gpu':
        parser.error('cuda_init requires backend gpu')
    if args.suite and (args.case is not None or args.init_profile is not None):
        parser.error('suite excludes case and initialization profile')
    if args.suite or (args.case == 'cuda_init' and args.init_profile is None):
        env = {key: os.environ[key] for key in
               ('PATH', 'CUDA_VISIBLE_DEVICES', 'NVIDIA_VISIBLE_DEVICES',
                'NVIDIA_DRIVER_CAPABILITIES') if key in os.environ}
        env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                   MKL_NUM_THREADS='1', CUPY_CACHE_DIR='/tmp/cupy-cache')
        failures = 0
        cells = [(case, trial, None) for case in CASES for trial in range(3)] if args.suite else [('cuda_init', 0, profile) for profile in INIT_PROFILES_GIB]
        for case, trial, profile in cells:
            started = time.perf_counter()
            command = [sys.executable, __file__, '--backend', args.backend, '--case', case]
            if args.gpu_discovery_8g:
                command += ['--gpu-discovery-8g']
            if profile is not None:
                command += ['--init-profile', str(profile)]
            child = subprocess.run(command,
                                   env=env, capture_output=True, text=True)
            receipt = {'case': case, 'backend': args.backend, 'cold_trial': trial,
                       'fresh_process_seconds': time.perf_counter() - started,
                       'diagnostic_init_profile_gib': profile,
                       'returncode': child.returncode, 'stdout': child.stdout,
                       'stderr': child.stderr[-6000:]}
            print(json.dumps(receipt), flush=True)
            failures += child.returncode != 0
        return int(bool(failures))
    if args.case is None:
        parser.error('--case is required without --suite')
    numerical_gib = 8 if args.gpu_discovery_8g else 3
    address_limit = (args.init_profile if args.case == 'cuda_init' else numerical_gib) * 1024**3
    resource.setrlimit(resource.RLIMIT_AS, (address_limit, address_limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2, 12 * 1024**2))
    started = time.perf_counter()
    stage = 'native_import'
    receipt = {'case': args.case, 'backend': args.backend,
               'address_space_bytes': address_limit, 'file_limit_bytes': 12 * 1024**2,
               'diagnostic_only': args.case == 'cuda_init',
               'gpu_discovery_8g': args.gpu_discovery_8g,
               'process_before_import': process_snapshot()}
    try:
        import numpy as np
        import scipy.linalg
        import qutip as qt
        import scqubits as scq
        pins = {'numpy': '2.5.4', 'scipy': '1.18.1', 'qutip': '5.3.1', 'scqubits': '4.3.1'}
        receipt['versions'] = {name: importlib.metadata.version(name) for name in pins}
        assert receipt['versions'] == pins
        cp = None
        if args.backend == 'gpu':
            if args.case == 'cuda_init':
                selector = os.environ.get('NVIDIA_VISIBLE_DEVICES', '').split(',')[0]
                if not re.fullmatch(r'(?:GPU-[0-9a-fA-F-]+|[0-9]+)', selector):
                    selector = '0'
                stage = 'allocated_device_diagnostic'
                try:
                    diagnostic = subprocess.run(
                        ['nvidia-smi', '--id=' + selector,
                         '--query-gpu=name,driver_version,memory.total,memory.used',
                         '--format=csv,nounits'], capture_output=True, text=True, check=False)
                    receipt['allocated_device_query'] = {
                        'returncode': diagnostic.returncode, 'stdout': diagnostic.stdout[-4000:],
                        'stderr': diagnostic.stderr[-2000:]}
                except FileNotFoundError:
                    receipt['allocated_device_query'] = {'status': 'nvidia-smi unavailable'}
            stage = 'cupy_import'
            import cupy as cp
            receipt['cupy_version'] = cp.__version__
            receipt['process_before_cuda_init'] = process_snapshot()
            stage = 'cuda_initialization'
            cp.cuda.runtime.free(0)
            cp.cuda.Stream.null.synchronize()
            props = cp.cuda.runtime.getDeviceProperties(0)
            receipt['device'] = {'name': props['name'].decode(),
                                 'compute_capability': [props['major'], props['minor']],
                                 'runtime': cp.cuda.runtime.runtimeGetVersion(),
                                 'driver': cp.cuda.runtime.driverGetVersion(),
                                 'memory_bytes': props['totalGlobalMem']}
            receipt['process_after_cuda_init'] = process_snapshot()
            if args.case == 'cuda_init':
                receipt.update(status='initialized', elapsed_seconds=time.perf_counter()-started,
                               device_free_total_bytes=cp.cuda.runtime.memGetInfo())
                print(json.dumps(receipt), flush=True)
                return 0
            cp.get_default_memory_pool().set_limit(size=512 * 1024**2)
            stage = 'qutip_cupy_import'
            import qutip_cupy
            receipt['qutip_cupy_version'] = qutip_cupy.__version__
        receipt['imports_context_seconds'] = time.perf_counter() - started
        samples = []
        for repeat in range(4):
            stage = 'construct'
            wall = time.perf_counter()
            if args.case.startswith('transmon'):
                dimension = int(args.case.removeprefix('transmon'))
                model = scq.Transmon(EJ=15, EC=.3, ng=0, ncut=(dimension - 1)//2,
                                    truncated_dim=6, esys_method='esys_scipy_dense')
                h = model.hamiltonian().astype(np.complex128)
            elif args.case == 'spectrum256':
                dimension = 256
                indices = np.arange(dimension)
                h = np.diag(indices * .04).astype(np.complex128)
                h += np.diag(np.sqrt(indices[1:]) * .005, 1)
                h += np.diag(np.sqrt(indices[1:]) * .005, -1)
            else:
                dimension = 2
                h = np.array([[0, .01*np.pi], [.01*np.pi, 0]], dtype=np.complex128)
            constructed = time.perf_counter()
            stage = 'cpu_reference'
            values, vectors = scipy.linalg.eigh(h)
            reference_finished = time.perf_counter()
            same_worker_cpu_operation_seconds = None
            if cp is not None:
                stage = 'same_worker_cpu_operation'
                cpu_started = time.perf_counter()
                if args.case.startswith('transmon'):
                    model.eigensys(evals_count=6)
                elif args.case == 'spectrum256':
                    qt.Qobj(h).eigenenergies()
                else:
                    qt.sesolve(qt.Qobj(h), qt.basis(2, 0), np.linspace(0, 400, 401),
                               e_ops=[qt.num(2)], options={
                                   'atol': 1e-10, 'rtol': 1e-9, 'nsteps': 10000,
                                   'progress_bar': False})
                same_worker_cpu_operation_seconds = time.perf_counter()-cpu_started
            stage = 'native_operation'
            native = time.perf_counter()
            transfer = kernel = 0.0
            native_gpu = False
            if args.case.startswith('transmon'):
                if cp is None:
                    actual, basis = model.eigensys(evals_count=6)
                else:
                    model.esys_method = 'esys_cupy_dense'
                    calls = []
                    original = cp.linalg.eigh
                    original_asarray = cp.asarray
                    transfers = []
                    def measured_asarray(*a, **kw):
                        stamp = time.perf_counter()
                        result = original_asarray(*a, **kw)
                        cp.cuda.Stream.null.synchronize()
                        transfers.append(time.perf_counter() - stamp)
                        return result
                    def counted(matrix, *a, **kw):
                        assert isinstance(matrix, cp.ndarray)
                        stamp = time.perf_counter()
                        result = original(matrix, *a, **kw)
                        cp.cuda.Stream.null.synchronize()
                        calls.append(time.perf_counter() - stamp)
                        return result
                    cp.linalg.eigh = counted
                    cp.asarray = measured_asarray
                    try:
                        actual, basis = model.eigensys(evals_count=6)
                    finally:
                        cp.linalg.eigh = original
                        cp.asarray = original_asarray
                    assert len(calls) == 1, calls
                    kernel = calls[0]
                    transfer = sum(transfers)
                    native_gpu = True
                error = float(np.max(np.abs(actual - values[:6])))
                residual = float(np.linalg.norm(h @ basis - basis * actual))
                projector_error = float(np.linalg.norm(basis @ basis.conj().T - vectors[:, :6] @ vectors[:, :6].conj().T))
                assert error < 1e-9 and residual < 1e-8 and projector_error < 1e-8
                arrays = (actual, basis)
            elif args.case == 'spectrum256':
                obj = qt.Qobj(h).to('cupyd' if cp is not None else 'Dense')
                if cp is not None:
                    # This plugin registers no eigensolver. Expose any host
                    # conversion rather than describing storage as GPU solve.
                    import qutip_cupy.dense as dense
                    original = dense.CuPyDense.to_array
                    conversions = []
                    def counted_copy(data):
                        conversions.append(1)
                        return original(data)
                    dense.CuPyDense.to_array = counted_copy
                    try:
                        actual = obj.eigenenergies()
                    finally:
                        dense.CuPyDense.to_array = original
                    receipt['spectrum_host_conversions'] = len(conversions)
                else:
                    actual = obj.eigenenergies()
                error = float(np.max(np.abs(actual - values)))
                assert error < 1e-9
                arrays = (actual,)
            else:
                times = np.linspace(0, 400, 401)
                obj = qt.Qobj(h).to('cupyd' if cp is not None else 'Dense')
                psi = qt.basis(2, 0).to('cupyd' if cp is not None else 'Dense')
                number = qt.num(2).to('cupyd' if cp is not None else 'Dense')
                gpu_matmuls = []
                host_conversions = []
                if cp is not None:
                    import qutip_cupy.dense as dense
                    original_copy = dense.CuPyDense.to_array
                    original_matmul = dense.matmul_cupydense
                    def traced_copy(data):
                        host_conversions.append(1)
                        return original_copy(data)
                    def traced_matmul(left, right, *a, **kw):
                        assert isinstance(left._cp, cp.ndarray)
                        assert isinstance(right._cp, cp.ndarray)
                        gpu_matmuls.append(1)
                        return original_matmul(left, right, *a, **kw)
                    dense.CuPyDense.to_array = traced_copy
                    qt.core.data.matmul.add_specialisations(
                        [(dense.CuPyDense, dense.CuPyDense, dense.CuPyDense, traced_matmul)])
                result = qt.sesolve(obj, psi, times, e_ops=[number],
                                    options={'atol': 1e-10, 'rtol': 1e-9,
                                             'nsteps': 10000, 'progress_bar': False})
                if cp is not None:
                    dense.CuPyDense.to_array = original_copy
                    receipt.setdefault('rabi_gpu_matmuls', []).append(len(gpu_matmuls))
                    receipt.setdefault('rabi_host_conversions', []).append(len(host_conversions))
                    native_gpu = bool(gpu_matmuls)
                actual = result.expect[0]
                error = float(np.max(np.abs(actual - np.sin(.01*np.pi*times)**2)))
                assert error < 1e-4
                arrays = (np.asarray(actual),)
                # Kernel calls establish GPU work; host conversions still
                # expose mixed execution rather than a GPU-only integrator.
            if cp is not None:
                cp.cuda.Stream.null.synchronize()
            finished = time.perf_counter()
            output = io.BytesIO()
            np.savez(output, **{f'array_{i}': array for i, array in enumerate(arrays)})
            assert len(output.getvalue()) < 6*1024**2
            sample = {'repeat': repeat, 'cold': repeat == 0, 'dimension': dimension,
                      'precision': 'float64/complex128', 'numerical_error': error,
                      'host_construct_seconds': constructed-wall,
                      'cpu_reference_seconds': reference_finished-constructed,
                      'same_worker_cpu_operation_seconds': same_worker_cpu_operation_seconds,
                      'operation_seconds': finished-native,
                      'native_kernel_seconds': kernel,
                      'host_to_device_seconds': transfer,
                      'other_operation_seconds': finished-native-kernel-transfer,
                      'native_gpu_proven': native_gpu,
                      'algorithm_seconds': constructed-wall+finished-native,
                      'trial_including_reference_seconds': finished-wall,
                      'npz_bytes': len(output.getvalue()),
                      'rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
            if cp is not None:
                sample['cupy_pool_total_bytes'] = cp.get_default_memory_pool().total_bytes()
                sample['device_free_total_bytes'] = cp.cuda.runtime.memGetInfo()
            samples.append(sample)
        receipt['samples'] = samples
        receipt['status'] = 'passed'
        print(json.dumps(receipt))
        return 0
    except Exception as exc:
        receipt.update(status='failed', stage=stage, error_type=type(exc).__name__,
                       error=str(exc), elapsed_seconds=time.perf_counter()-started,
                       rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                       process_at_failure=process_snapshot())
        print(json.dumps(receipt), flush=True)
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
