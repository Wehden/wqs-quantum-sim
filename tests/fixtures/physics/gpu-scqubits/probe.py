"""Fixed scqubits CPU/GPU screening; invoked only in an owned discovery worker."""
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


def child(args):
    start = time.monotonic()
    resource.setrlimit(resource.RLIMIT_AS, (args.as_gib * GIB,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2,) * 2)
    import numpy as np
    from scipy import linalg, sparse
    import scqubits as scq
    from scqubits.core import diag

    assert scq.__version__ == '4.3.1'
    cp = None
    if args.arm.startswith('gpu'):
        import cupy as cp
        assert cp.cuda.runtime.getDeviceCount() == 1
        cp.cuda.runtime.free(0)
        free, total = cp.cuda.runtime.memGetInfo()
        assert total >= args.vram_gib * GIB * .98 and free >= total * .90
        cp.get_default_memory_pool().set_limit(size=int(total * .65))
    imports = time.monotonic() - start
    fixtures = json.loads(FIXTURES.read_text())
    label, cutoff, levels, operation = fixtures['screen'][args.child]
    cls = label.removesuffix('HighRatio')
    dimension = cutoff if cls == 'Fluxonium' else (2*cutoff+1)**(2 if cls == 'FluxQubit' else 1)
    # Conservative discovery bound for native construction plus solver/validation copies.
    estimate = 2 * GIB + 320 * dimension * dimension
    assert estimate <= args.ram_gib * GIB * .75
    params = {**fixtures['models'][label], ('cutoff' if cls == 'Fluxonium' else 'ncut'): cutoff}
    model = getattr(scq, cls)(**params)
    h = model.hamiltonian()
    assert h.shape == (dimension, dimension) and h.dtype in (np.float64, np.complex128)
    constructed = time.monotonic()
    values_only = operation == 'values'
    device_math = []
    if args.arm == 'gpu-dense':
        # Upstream calls CuPy's native solver. Record the actual device-array
        # call and CUDA event interval without counting it twice in wall time.
        method = 'eigvalsh' if values_only else 'eigh'
        solver = getattr(cp.linalg, method)
        before, after = cp.cuda.Event(), cp.cuda.Event()
        def observed(matrix, *a, **kw):
            assert isinstance(matrix, cp.ndarray) and matrix.device.id == 0
            before.record()
            result = solver(matrix, *a, **kw)
            after.record()
            after.synchronize()
            device_math.append(cp.cuda.get_elapsed_time(before, after))
            return result
        setattr(cp.linalg, method, observed)
        answer = (diag.evals_cupy_dense if values_only else diag.esys_cupy_dense)(h, levels)
        assert len(device_math) == 1
    elif args.arm in ('gpu-sparse', 'cpu-sparse'):
        if np.count_nonzero(h) > dimension * dimension / 4:
            print(json.dumps({'status':'ineligible', 'reason':'matrix density exceeds sparse threshold'}))
            return
        matrix = sparse.csr_matrix(h)
        v0 = np.random.default_rng(38).standard_normal(dimension)
        if cp is None:
            solver = sparse.linalg.eigsh
        else:
            from cupyx.scipy.sparse import csr_matrix
            from cupyx.scipy.sparse.linalg import eigsh
            matrix = csr_matrix(matrix)
            v0 = cp.asarray(v0)
            before, after = cp.cuda.Event(), cp.cuda.Event()
            before.record()
            solver = eigsh
        answer = solver(matrix, k=levels, which='SA', tol=1e-12, v0=v0, maxiter=20000)
        if cp is not None:
            after.record()
            after.synchronize()
            device_math.append(cp.cuda.get_elapsed_time(before, after))
            answer = tuple(cp.asnumpy(a) for a in answer)
        if values_only:
            answer = answer[0]
    elif args.arm == 'cpu-structured':
        assert cls == 'Transmon'
        answer = linalg.eigh_tridiagonal(np.diag(h), np.diag(h, 1), eigvals_only=values_only,
                                          select='i', select_range=(0, levels-1))
    else:
        answer = linalg.eigh(h, subset_by_index=(0, levels-1), driver='evr', eigvals_only=values_only)
    solved = time.monotonic()
    if values_only:
        arrays = {'energies': np.sort(answer)}
    else:
        e, v = answer
        order = np.argsort(e)
        e, v = e[order], v[:, order]
        arrays = {'energies': e, 'vectors': v}
        if operation == 'operator':
            op = model.n_1_operator() if cls == 'FluxQubit' else model.n_operator()
            arrays['operator'] = v.conj().T @ (op @ v)
    np.savez(args.output, **arrays)
    ended = time.monotonic()
    assert all(np.isfinite(a).all() for a in arrays.values())
    orthogonality = residual = None
    if not values_only:
        orthogonality = float(np.linalg.norm(v.conj().T @ v - np.eye(levels), ord=np.inf))
        scale = max(1., float(np.max(np.abs(h))))
        residual = float(np.linalg.norm(h @ v - v * e, ord=np.inf) / scale)
    print(json.dumps({'status':'ok','case':args.child,'dimension':dimension,'operation':operation,
                      'model':label,'arm':args.arm,'threads':args.threads,'import_s':imports,
                      'construct_s':constructed-start-imports,'solve_s':solved-constructed,
                      'output_s':ended-solved,'fresh_s':ended-start,
                      'device_event_ms':device_math,'rss_peak_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      'pool_bytes':cp.get_default_memory_pool().total_bytes() if cp is not None else None,
                      'residual':residual,'orthogonality':orthogonality,'bytes':args.output.stat().st_size}))


def verify(reference, result):
    import numpy as np
    a, b = reference, result
    np.testing.assert_allclose(a['energies'], b['energies'], atol=1e-9, rtol=1e-10)
    if 'vectors' not in a:
        return
    energies = a['energies']
    split = np.flatnonzero(np.diff(energies) > 1e-7) + 1
    groups = np.split(np.arange(len(energies)), split)
    alignment = np.zeros((len(energies), len(energies)), dtype=np.complex128)
    for group in groups:
        left, right = a['vectors'][:, group], b['vectors'][:, group]
        u, singular, vh = np.linalg.svd(right.conj().T @ left)
        # Align each degenerate eigenspace using only the returned eigenvectors.
        transform = u @ vh
        assert np.linalg.norm(right @ transform - left, ord=2) <= 1e-7
        alignment[np.ix_(group, group)] = transform
    if 'operator' in a:
        scale = max(1., float(np.linalg.norm(a['operator'], ord=np.inf)))
        error = np.linalg.norm(alignment.conj().T @ b['operator'] @ alignment - a['operator'], ord=np.inf)
        assert error <= 1e-8 * scale


def run(args):
    import numpy as np
    fixtures = json.loads(FIXTURES.read_text())
    print(json.dumps({'fixture_sha256':hashlib.sha256(FIXTURES.read_bytes()).hexdigest(), 'lane':'scqubits'}), flush=True)
    indices = range(len(fixtures['screen'])) if args.cases == 'all' else [int(i) for i in args.cases.split(',')]
    rng = random.Random(183)
    with tempfile.TemporaryDirectory() as directory:
        for index in indices:
            model, _, _, _ = fixtures['screen'][index]
            arms = [('cpu-dense',1), ('cpu-dense',8)]
            if model == 'Transmon':
                arms.append(('cpu-structured',1))
            if model in ('FluxQubit', 'Transmon'):
                arms.append(('cpu-sparse',8))
            if args.gpu:
                arms.append(('gpu-dense',1))
                if model in ('FluxQubit', 'Transmon'):
                    arms.append(('gpu-sparse',1))
            for trial in range(args.trials):
                receipts, results = {}, {}
                ordered = arms.copy()
                rng.shuffle(ordered)
                for arm, threads in ordered:
                    path = Path(directory) / 'result.npz'
                    path.unlink(missing_ok=True)
                    allowed = ('PATH','LD_LIBRARY_PATH','CUDA_VISIBLE_DEVICES','NVIDIA_VISIBLE_DEVICES')
                    env = {k:os.environ[k] for k in allowed if k in os.environ}
                    env.update({k:str(threads) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')})
                    env.update(CUPY_CACHE_DIR=str(Path(directory)/'cupy'), HOME=directory)
                    command = [sys.executable, __file__, '--child', str(index),'--output', str(path), '--arm', arm,
                               '--threads', str(threads), '--as-gib', str(args.as_gib), '--ram-gib', str(args.ram_gib), '--vram-gib', str(args.vram_gib)]
                    try:
                        proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
                        if proc.returncode:
                            receipt = {'status':'failed','error':proc.stderr[-1200:]}
                        else:
                            receipt = json.loads(proc.stdout.splitlines()[-1])
                            if receipt['status'] == 'ok':
                                with np.load(path, allow_pickle=False) as data:
                                    results[arm,threads] = dict(data)
                    except subprocess.TimeoutExpired:
                        receipt = {'status':'timeout'}
                    receipts[arm,threads] = receipt
                ref = results.get(('cpu-dense', 1))
                for key, record in receipts.items():
                    if record['status'] == 'ok':
                        try:
                            assert ref is not None
                            verify(ref, results[key])
                            assert all(record[m] is None or record[m] <= 1e-9 for m in ('residual','orthogonality'))
                            assert record['rss_peak_kib']*1024 <= args.ram_gib*GIB*.75
                            record['correctness'] = 'pass'
                        except (AssertionError, ValueError) as error:
                            record.update(status='incorrect', reason=str(error)[:1000])
                    print(json.dumps({**record,'case':index,'case_spec':fixtures['screen'][index],
                                      'arm':key[0],'threads':key[1],'trial':trial}),flush=True)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', type=int)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--arm', choices=('cpu-dense','cpu-structured','cpu-sparse','gpu-dense','gpu-sparse'))
    parser.add_argument('--threads', type=int, choices=(1,8), default=1)
    parser.add_argument('--as-gib', type=int, choices=(12,48), default=12)
    parser.add_argument('--ram-gib', type=int, choices=(16,64), default=16)
    parser.add_argument('--vram-gib', type=int, choices=(16,24), default=16)
    parser.add_argument('--cases', default='all')
    parser.add_argument('--trials', type=int, choices=(1,5,20), default=5)
    parser.add_argument('--gpu', action='store_true')
    args = parser.parse_args()
    child(args) if args.child is not None else run(args)
