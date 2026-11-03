"""Pinned native JoSIM boundary; admission remains certificate controlled."""
from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

from .josim_models import JoSIMRequest
from .josim_validation import validate_josim_netlist
from .physics_results import AllocationPlan, PhysicsExecutionResult

__all__ = ('run_josim',)

_BINARY = Path('/opt/josim/bin/josim-cli')
_BINARY_SHA256 = '87582444021dc3e68320162c62752ff3c3646c430def1600a4298e9ecb8cae21'
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
    _, info = validate_josim_netlist(request.netlist)
    ceiling, columns = info['predicted_rows'], len(info['trace_names'])
    specifications = plan.result.arrays
    if (len(specifications) != 2
            or [(s.name, s.dtype, s.shape, s.units) for s in specifications] != [
                ('times', 'float64', (ceiling,), 's'),
                ('traces', 'float64', (ceiling, columns), None)]):
        raise ValueError('resource_limit: JoSIM result reservation mismatch')
    reproduced = request._certified_allocation_plan()
    if (plan != reproduced
            or json.dumps(asdict(plan), sort_keys=True, allow_nan=False)
            != json.dumps(asdict(reproduced), sort_keys=True, allow_nan=False)):
        raise ValueError('resource_limit: JoSIM allocation certificate mismatch')
    try:
        table = info['trace_projection']
        if (type(table) is not list or len(table) != columns
                or type(info['native_trace_names']) is not list
                or len(info['native_trace_names']) != columns
                or type(info['native_netlist']) is not str
                or not info['native_netlist'].isascii()
                or len(info['native_netlist']) > 65568):
            raise ValueError
        for index, entry in enumerate(table):
            if (type(entry) is not dict
                    or set(entry) != {'public_name', 'native_name', 'scale', 'rewritten'}
                    or type(entry['scale']) is not int or entry['scale'] not in (-1, 1)
                    or type(entry['rewritten']) is not bool
                    or any(type(entry[key]) is not str or not entry[key].isascii()
                           or not 1 <= len(entry[key]) <= 35 for key in ('public_name', 'native_name'))
                    or entry['public_name'] != info['trace_names'][index]
                    or entry['native_name'] != info['native_trace_names'][index]
                    or entry['rewritten'] != entry['public_name'].startswith('V(I')
                    or (not entry['rewritten'] and (entry['scale'] != 1
                                                  or entry['native_name'] != entry['public_name']))):
                raise ValueError
        if (len(json.dumps(table, separators=(',', ':'), allow_nan=False)) > 2048
                or table != plan.coefficient_plan['trace_projection']
                or hashlib.sha256(info['native_netlist'].encode('ascii')).hexdigest()
                != plan.coefficient_plan['native_netlist_sha256']):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('resource_limit: invalid JoSIM native projection') from None
    if (_BINARY_SHA256 != plan.coefficient_plan['binary_sha256']
            or _SOURCE_COMMIT != plan.coefficient_plan['source_commit']):
        raise ValueError('native_failure: JoSIM binary identity differs from certificate')
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
            (root / 'input.cir').write_text(info['native_netlist'], encoding='ascii')
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
                  'binary_sha256': _BINARY_SHA256, 'source_commit': _SOURCE_COMMIT,
                  'trace_projection': table,
                  'projection_revision': plan.coefficient_plan['projection_revision']},
        work={'grid_row_upper_bound': str(ceiling), 'returned_rows': str(len(rows)),
              'trace_count': str(columns), 'binary_sha256': _BINARY_SHA256,
              'native_exit_status': 0})


def _parse_output(payload: bytes, info: Mapping) -> list:
    try:
        stream = io.StringIO(payload.decode('ascii'))
        expected = 'time,' + ','.join('"' + name + '"' for name in info['native_trace_names'])
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
            for index, entry in enumerate(info['trace_projection'], 1):
                if entry['rewritten']:
                    values[index] = 0.0 if values[index] == 0 else entry['scale']*values[index]
            rows.append(values)
        if not rows:
            raise ValueError
        return rows
    except (ValueError, UnicodeError, OverflowError):
        raise ValueError('invalid_result: invalid JoSIM time or selected traces') from None
