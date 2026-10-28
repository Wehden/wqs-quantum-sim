"""Pinned native JoSIM boundary; admission remains certificate controlled."""
from collections.abc import Mapping
import hashlib
import io
from pathlib import Path
import subprocess
import tempfile

import numpy as np

from .josim_models import JoSIMRequest
from .josim_validation import validate_josim_netlist
from .physics_results import AllocationPlan, PhysicsExecutionResult

__all__ = ('run_josim',)

_BINARY = Path('/opt/josim/bin/josim-cli')
_BINARY_SHA256 = 'b75dae95de6c4601efef4aac377104ba6b12a94c7d36f6b9b0ecde8cf9372450'
_SOURCE_COMMIT = '02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224'
_OUTPUT_BYTES = 6 * 1024**2


def run_josim(request: JoSIMRequest, plan: AllocationPlan, *,
              execution_context: Mapping) -> PhysicsExecutionResult:
    """Reproduce admission before entering the private native boundary."""
    reproduced = request.admission_plan()
    if reproduced != plan:
        raise ValueError('resource_limit: JoSIM admission plan mismatch')
    return _execute_josim(request, plan)


def _execute_josim(request: JoSIMRequest, plan: AllocationPlan) -> PhysicsExecutionResult:
    """Execute normalized input and preserve every bounded native output row."""
    normalized, info = validate_josim_netlist(request.netlist)
    ceiling, columns = info['predicted_rows'], len(info['trace_names'])
    specifications = plan.result.arrays
    if (len(specifications) != 2
            or [(s.name, s.dtype, s.shape, s.units) for s in specifications] != [
                ('times', 'float64', (ceiling,), 's'),
                ('traces', 'float64', (ceiling, columns), None)]):
        raise ValueError('resource_limit: JoSIM result reservation mismatch')
    try:
        digest = hashlib.sha256()
        with _BINARY.open('rb') as stream:
            while block := stream.read(65536):
                digest.update(block)
        if digest.hexdigest() != _BINARY_SHA256:
            raise ValueError('native_failure: JoSIM binary identity mismatch')
        with tempfile.TemporaryDirectory(prefix='wqs-josim-') as directory:
            root = Path(directory)
            root.chmod(0o700)
            (root / 'input.cir').write_text(normalized, encoding='ascii')
            completed = subprocess.run(
                [str(_BINARY), '-m', '-o', 'output.csv', 'input.cir'], cwd=root,
                env={'PATH': '/usr/bin:/bin', 'OMP_NUM_THREADS': '1'},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False)
            if completed.returncode:
                raise ValueError('native_failure: JoSIM transient did not converge')
            output = root / 'output.csv'
            if (set(path.name for path in root.iterdir()) != {'input.cir', 'output.csv'}
                    or output.is_symlink() or not output.is_file()):
                raise ValueError('invalid_result: unexpected JoSIM output files')
            if output.stat().st_size > _OUTPUT_BYTES:
                raise ValueError('resource_limit: JoSIM output byte limit')
            with output.open('rb') as stream:
                payload = stream.read(_OUTPUT_BYTES + 1)
            if len(payload) > _OUTPUT_BYTES:
                raise ValueError('resource_limit: JoSIM output byte limit')
            rows = _parse_output(payload, info)
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise ValueError('native_failure: JoSIM execution or output unavailable') from None
    data = np.asarray(rows, dtype=np.float64)
    return PhysicsExecutionResult(
        arrays={'times': data[:, 0].copy(), 'traces': data[:, 1:].copy()},
        metadata={'engine': 'josim', 'operation': 'transient', 'schema_version': 1,
                  'engine_version': '2.7.02a34ee', 'backend': 'cpu',
                  'precision': 'float64', 'trace_names': info['trace_names'],
                  'trace_units': info['trace_units'], 'time_units': 's',
                  'binary_sha256': _BINARY_SHA256, 'source_commit': _SOURCE_COMMIT},
        work={'grid_row_upper_bound': str(ceiling), 'returned_rows': str(len(rows)),
              'trace_count': str(columns), 'binary_sha256': _BINARY_SHA256,
              'native_exit_status': 0})


def _parse_output(payload: bytes, info: Mapping) -> list:
    try:
        stream = io.StringIO(payload.decode('ascii'))
        expected = 'time,' + ','.join('"' + name + '"' for name in info['trace_names'])
        if stream.readline(4097).rstrip('\r\n') != expected:
            raise ValueError
        rows = []
        while line := stream.readline(4097):
            if len(line) > 4096 or not line.endswith('\n'):
                raise ValueError
            cells = line.strip().split(',')
            if len(cells) != len(info['trace_names']) + 1 or any(len(c) > 128 for c in cells):
                raise ValueError
            values = [float(cell) for cell in cells]
            if (not all(np.isfinite(value) for value in values)
                    or not 0 <= values[0] <= info['stop_seconds']
                    or (rows and values[0] <= rows[-1][0])
                    or len(rows) >= info['predicted_rows']):
                raise ValueError
            rows.append(values)
        if not rows:
            raise ValueError
        return rows
    except (ValueError, UnicodeError, OverflowError):
        raise ValueError('invalid_result: invalid JoSIM time or selected traces') from None
