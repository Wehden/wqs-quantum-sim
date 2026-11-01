#!/usr/bin/env python3
"""Fixed CPU-only discovery comparisons, never a production execution interface.

Original and candidate are explicit absolute paths to trusted local executables.
No downloads, provisioning, network access, shell, or product work/time limit.
The optional wall-clock guard bounds this fixed discovery experiment only.
Self-comparison proves the harness runs; it does not prove a native patch.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import resource
import struct
import subprocess
import tempfile
import time

ORIGINAL_SHA256 = 'b75dae95de6c4601efef4aac377104ba6b12a94c7d36f6b9b0ecde8cf9372450'
SOURCE_COMMIT = '02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224'
PHI0 = 2.0678338484619295e-15
# Independently rebuilt original and source-matched candidate/audit builds.
BUILD_PROVENANCE = {'build_type': 'Release -O3 -DNDEBUG',
 'candidate_binary_sha256': '87582444021dc3e68320162c62752ff3c3646c430def1600a4298e9ecb8cae21',
 'compiler': 'GCC 13.3.0-6ubuntu2~24.04.1',
 'image': 'sha256:30465f32041d36dc6f3d0ac1e20fe5b856f72a25943f74829683cfc7fea61b79',
 'instrumented_binary_sha256': '2d610f8f02d5e16298bfad89ae844f4593744e5f916d610ebcb89235cf19e830',
 'original_binary_sha256': 'b75dae95de6c4601efef4aac377104ba6b12a94c7d36f6b9b0ecde8cf9372450',
 'patch_sha256': 'eb727347b5933f7bb42c983c26ec63098e51d0d5a43fa7ceb865bfa28ce768ae',
 'patched_source_sha256': {'include/JoSIM/Simulation.hpp': '52be5d18624dab8a5311d29412152a427ba4c6b1528c37fd6ba9accafc7986e4',
                           'include/JoSIM/Transient.hpp': '6633e999427b540fd055e823a5323bb86c4378311b373fe1f40d3e07bffcfedd',
                           'src/Output.cpp': 'e15afbe9799edb873f988bbe090028dbca1013dd5e2cb039483a40d53066ba13',
                           'src/Simulation.cpp': '43b309c8861369558b6722cc6d6324da1121c23b89db7a038907a7c309a1707c'},
 'source_date_epoch': 1792989136,
 'upstream_commit': '02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224'}

MODEL = '.MODEL MOD JJ(RTYPE=1 VG=0.0028 CAP=7e-14 R0=160 RN=16 ICRIT=0.0001)\n'
# Filled only from the pinned original. A candidate never updates references.
FROZEN = {'dc_10ns': {'array_sha256': '6ca9f55fe4183ce7bca8cc1d82d364c79d7e2b3230a9c3001d130fdca97541c2',
             'bytes': 26014,
             'csv_sha256': '9b91077359e197ecc290197e05ff4f5e710bcaebf726b6a0befc9a49fe1343b1',
             'diagnostic_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
             'fixture_sha256': '4057172e1490f3da22b7eb4b4d80dd3f0a072670a3de4b14cd7357e88c898aa6',
             'header': ['time', 'P(B01)'],
             'rows': 1000,
             'status': 0},
 'dc_1ns': {'array_sha256': '67e69e4b6f1394e2f950938aaf8c001e226d8cd2c20b63397ee9983175b1c94f',
            'bytes': 26014,
            'csv_sha256': 'efd04059200b6fcb69c9ecea90ef6e0f043bf65ec051b383127105a499d3c105',
            'diagnostic_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            'fixture_sha256': 'c162ab9f865ff506b9b910fd2c8f7e7f44e2924074e5ee334f544367330c6384',
            'header': ['time', 'P(B01)'],
            'rows': 1000,
            'status': 0},
 'families': {'array_sha256': '8f16e74b6bcde692c7d7428459c127812583d8395d34225fc6098bef2ae1cd7b',
              'bytes': 9635,
              'csv_sha256': '20fffb7b8d99f4407b17176d8c9dc270b4df04032c1b4a214c34c8854b9ba562',
              'diagnostic_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
              'fixture_sha256': 'af81a6c20a57162a05a91d015dcb442b0ecd29891a7e2ba4667378a9e5be0cfc',
              'header': ['time',
                         'V(R01)',
                         'I(R01)',
                         'V(L01)',
                         'I(L01)',
                         'V(B01)',
                         'I(B01)',
                         'P(B01)',
                         'I(I02)'],
              'rows': 79,
              'status': 0},
 'retry_derivative': {'array_sha256': '9c623392b9426cbd8230de73b7c2afd9518182d1cb8b2da522b8408cc741fe8c',
                      'bytes': 92063,
                      'csv_sha256': '8f68cb4f52898bc250e7c5eb2a89a2ba1f81f2fd6c64fa7377b25e92b50b16c6',
                      'diagnostic_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                      'fixture_sha256': 'bf609fb7442de94d4f1cf6d591e8bfedfbea4d72bac61d34b3c24a7ad8f4989d',
                      'header': ['time', 'V(V01)', 'I(V01)', 'I(B01)', 'V(B01)', 'P(B01)', 'I(I01)'],
                      'rows': 1000,
                      'status': 0},
 'sfq': {'array_sha256': '698addcdc6ff152b94d4d908fd4023738560622fc31ab306a2b0e407fe5beb04',
         'bytes': 78282,
         'csv_sha256': '4696f79dee7f8b6504b89d9424c9fdd257fc2f9906d326507daf9a27993fcbcb',
         'diagnostic_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
         'fixture_sha256': '0b8066e6d3074e4fcf7bde182d03e9e4aa134346739640315f1f2f6d025146be',
         'header': ['time', 'V(B01)', 'P(B01)', 'V(B02)', 'P(B02)', 'I(ROUT)'],
         'rows': 999,
         'status': 0},
 'singular_failure': {'array_sha256': None,
                      'bytes': 0,
                      'csv_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                      'diagnostic_sha256': 'fc368e5c2bafa1169644b5115bf844dab0c5d2b9682adcec08f73466f37c0f2f',
                      'fixture_sha256': '82d584103c970a092f7355b87e451ff6fcd597318f20ed7088beec0e7c4470d4',
                      'header': [],
                      'rows': 0,
                      'status': 255},
 'source_traces': {'array_sha256': '24d0b5b749a4cf0ca967ab9239aaaf64863bd8bf7e1fb9d24fec83683b6d6b8d',
                   'bytes': 574,
                   'csv_sha256': '4e3f3187dc6f6b88dd08a38212bc25f9820da5428b1443486b9ee2ff38ced8c4',
                   'diagnostic_sha256': '54c0cfc11290e02840f640ad9da63bf5207f7972cc8f327317b30f6ad5cbba2e',
                   'fixture_sha256': '08308c6c72d4d782e55c443f36625fcff6adec6533dca74bbd96e2f749952869',
                   'header': ['time', 'V(V01)', 'I(V01)', 'I(I01)', 'V(R02)'],
                   'rows': 8,
                   'status': 0}}


def _limits():
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _fixtures():
    """Each tuple is (netlist, expected success); no random or caller circuits."""
    sfq = Path(__file__).resolve().parents[2] / 'tests/fixtures/physics/josim/sfq.cir'
    cases = {'sfq': (sfq.read_text(), True)}
    for name, step, stop in (('dc_1ns', '1e-12', '1e-9'),
                              ('dc_10ns', '1e-11', '1e-8')):
        cases[name] = ('B01 1 0 MOD AREA=1\nV01 1 0 DC 0.1\n' + MODEL +
                       f'.TRAN {step} {stop}\n.PRINT PHASE B01\n.END\n', True)
    cases['retry_derivative'] = ('B01 1 0 MOD AREA=1\nV01 1 0 DC 0.1\n'
        'I01 0 1 PWL(0 0 1.375p 1u 2.625p -1u 5.125p 0)\n' + MODEL +
        '.TRAN 1p 1n\n.PRINT DEVV V01\n.PRINT DEVI V01\n.PRINT DEVI B01\n'
        '.PRINT DEVV B01\n.PRINT PHASE B01\n.PRINT DEVI I01\n.END\n', True)
    # POSGND, GNDNEG and POSNEG node configurations; signed DC and PWL;
    # all admitted R/L/B/I/V device types and every applicable trace family.
    cases['families'] = ('V01 1 0 PWL(0 0 3p 1u 7.5p -1u 12p 0)\n'
        'R01 1 2 4\nL01 2 0 2p\nI01 0 2 DC 1u\n'
        'B01 3 0 MOD AREA=1\nI02 0 3 PWL(0 0 5p 2u 9p -1u)\n' + MODEL +
        '.TRAN 0.25p 20p\n.PRINT DEVV R01\n.PRINT DEVI R01\n'
        '.PRINT DEVV L01\n.PRINT DEVI L01\n.PRINT DEVV B01\n'
        '.PRINT DEVI B01\n.PRINT PHASE B01\n.PRINT DEVI I02\n.END\n', True)
    cases['source_traces'] = ('V01 0 1 DC -1u\nR01 1 0 2\n'
        'I01 1 2 PWL(0 0 0.375p 1u 0.75p -1u 1.125p 0)\nR02 2 0 3\n' + MODEL +
        '.TRAN 0.25p 2.125p\n.PRINT DEVV V01\n.PRINT DEVI V01\n'
        '.PRINT DEVV I01\n.PRINT DEVI I01\n.PRINT DEVV R02\n.END\n', True)
    # Native factorization failure is the deterministic admitted-subset
    # convergence/failure path: two contradictory ideal voltage constraints.
    cases['singular_failure'] = ('V01 1 0 DC 1u\nV02 1 0 DC 2u\nR01 1 0 2\n' + MODEL +
        '.TRAN 0.25p 2p\n.PRINT DEVI R01\n.END\n', False)
    return cases


def _parse(raw):
    rows = list(csv.reader(io.StringIO(raw.decode('ascii'))))
    if not rows or len(rows) > 4097 or not 2 <= len(rows[0]) <= 9:
        raise ValueError('CSV shape')
    if not rows[0][0].lower().startswith('time') or len(set(rows[0])) != len(rows[0]):
        raise ValueError('CSV header')
    values = tuple(tuple(float(x) for x in row) for row in rows[1:])
    if not values or any(len(row) != len(rows[0]) or not all(map(math.isfinite, row)) for row in values):
        raise ValueError('CSV finite rectangular arrays')
    if any(b[0] <= a[0] for a, b in zip(values, values[1:])) or values[0][0] < 0:
        raise ValueError('CSV time order')
    packed = b''.join(struct.pack('!d', value) for row in values for value in row)
    return tuple(rows[0]), values, hashlib.sha256(packed).hexdigest()


def _run(binary, netlist, success, guard, audit=False):
    with tempfile.TemporaryDirectory(prefix='wqs-josim-history-') as scratch:
        root = Path(scratch)
        (root / 'input.cir').write_text(netlist, encoding='ascii')
        env = {'PATH': '/usr/bin:/bin', 'HOME': scratch, 'LC_ALL': 'C',
               'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
        started = time.monotonic()
        with (root / 'diagnostic.txt').open('wb') as diagnostic:
            completed = subprocess.run([str(binary), '-m', '-o', 'output.csv', 'input.cir'],
                cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=diagnostic,
                preexec_fn=_limits, timeout=guard, check=False)
        elapsed = time.monotonic() - started
        if {p.name for p in root.iterdir()} - {'input.cir', 'output.csv', 'diagnostic.txt'}:
            raise ValueError('unexpected native output files')
        if success != (completed.returncode == 0):
            raise ValueError(f'unexpected native status {completed.returncode}')
        output = root / 'output.csv'
        if output.exists() and output.stat().st_size > 6 * 1024**2:
            raise ValueError('CSV byte bound')
        raw = output.read_bytes() if output.exists() else b''
        if not success and raw:
            raise ValueError('failure must not produce partial CSV')
        if not success and b'Matrix is singular.' not in (root / 'diagnostic.txt').read_bytes():
            raise ValueError('expected native singular-matrix failure')
        diagnostics = (root / 'diagnostic.txt').read_bytes()
        measurements = []
        if audit:
            measurements = [json.loads(line.removeprefix(b'WQS_HISTORY '))
                            for line in diagnostics.splitlines() if line.startswith(b'WQS_HISTORY ')]
            if len(measurements) != 1 or measurements[0].get('bounded') is not True:
                raise ValueError('bounded native instrumentation missing')
            diagnostics = b'\n'.join(line for line in diagnostics.splitlines()
                                     if not line.startswith(b'WQS_HISTORY '))
            if diagnostics:
                diagnostics += b'\n'
        diagnostic_hash = hashlib.sha256(diagnostics).hexdigest()
        parsed = _parse(raw) if success else None
        return {'status': completed.returncode, 'diagnostic_sha256': diagnostic_hash, 'csv_sha256': hashlib.sha256(raw).hexdigest(),
                'array_sha256': parsed[2] if parsed else None,
                'rows': len(parsed[1]) if parsed else 0, 'bytes': len(raw),
                'elapsed_seconds': round(elapsed, 6),
                'history': measurements[0] if measurements else None}, raw, parsed


def _compare(original, candidate):
    for key in ('status', 'diagnostic_sha256', 'csv_sha256', 'array_sha256', 'rows', 'bytes'):
        if original[0][key] != candidate[0][key]:
            raise ValueError(f'exact native comparison failed: {key}')
    if original[1] != candidate[1] or original[2] != candidate[2]:
        raise ValueError('exact native CSV/parsed-array comparison failed')


def _self_checks():
    """Comparator/malformed-parser checks; never substitute for real binaries."""
    raw = b'time,v\n0,0\n1,2\n'
    parsed = _parse(raw)
    item = ({'status': 0, 'diagnostic_sha256': '', 'csv_sha256': hashlib.sha256(raw).hexdigest(),
             'array_sha256': parsed[2], 'rows': 2, 'bytes': len(raw)}, raw, parsed)
    _compare(item, item)
    for bad in (b'time,v\n0,nan\n', b'time,v\n1,0\n0,1\n', b'time,v\n0\n'):
        try:
            _parse(bad)
        except (ValueError, UnicodeError):
            pass
        else:
            raise AssertionError('malformed CSV accepted')
    try:
        _compare(item, (dict(item[0], array_sha256='changed'), raw, parsed))
    except ValueError:
        pass
    else:
        raise AssertionError('changed arrays accepted')
    return {'malformed_csv_rejected': 3, 'comparison_tamper_rejected': 1}



def _capacity_cases():
    """Fixed admitted shapes; counts grow without changing output reservation."""
    cases = {}
    for name, step, stop in (('dc_1ns', '1e-12', '1e-9'),
                            ('dc_10ns', '1e-11', '1e-8'),
                            ('dc_100ns', '1e-10', '1e-7'),
                            ('dc_1us', '1e-9', '1e-6')):
        cases[name] = ('B01 1 0 MOD AREA=1\nV01 1 0 DC 0.1\n' + MODEL +
                       f'.TRAN {step} {stop}\n.PRINT PHASE B01\n.END\n')
    lines = [f'B{i:02d} {2*i-1} {2*i if i<8 else 0} MOD AREA=1' for i in range(1, 9)]
    lines += [f'B{i:02d} {(i-9)%15+1} 0 MOD AREA=1' for i in range(9, 33)]
    lines += [MODEL.rstrip(), '.TRAN 9.094947017729282e-13 3.724380803760141e-9']
    lines += [f'.PRINT DEVV B{i:02d}' for i in range(1, 9)]
    cases['maximum_history'] = '\n'.join([*lines, '.END', ''])
    # Simultaneously cover the maximum admitted PWL input and output rows.
    knots = ' '.join(f'{i*1e-12:.17g} {0 if i%2==0 else 1e-6}' for i in range(64))
    lines[31] = f'I32 0 1 PWL({knots})'
    cases['maximum_pwl'] = '\n'.join([*lines, '.END', ''])
    sources = [f'I{i:02d} 0 1 PWL({knots})' for i in range(1, 32)]
    cases['maximum_source_storage'] = '\n'.join([*sources, 'R32 1 0 1', MODEL.rstrip(),
        '.TRAN 9.094947017729282e-13 3.724380803760141e-9',
        *[f'.PRINT DEVI I{i:02d}' for i in range(1, 9)], '.END', ''])
    return cases


def _verify_capacity(record):
    dimension, selected, rows = (record[key] for key in ('dimension', 'selected_variables', 'rows'))
    if (record.get('bounded') is not True or not 0 < dimension <= 80
            or not 0 <= selected <= 24 or not 0 < rows <= 4096):
        raise ValueError('bounded history dimensions')
    # GCC13/libstdc++ reserve(n) allocates exactly n elements. Include 80 optional
    # vector objects, all ring/vector owners, and allocator alignment headroom.
    history_bound = 8 * (3*dimension + 3*selected*4096 + 4096) + 80*32 + 512
    rhs_bound = 4 * (dimension+1) * 8
    if (not 0 < record['history_capacity_bytes'] <= history_bound
            or not 0 < record['rhs_capacity_bytes'] <= rhs_bound):
        raise ValueError('bounded history/RHS capacity exceeded')
    if record['attempts'] < 1 or record['internal_steps'] < record['final_attempt_steps']:
        raise ValueError('invalid native attempt counters')
    return {'history_bound_bytes': history_bound, 'rhs_bound_bytes': rhs_bound}



def _workspace_charge():
    """Pinned real-double/64-bit-int KLU and the accepted native envelope only.

    JoSIM Simulation/Output keep fixed histories plus bounded selected output.
    MatrixInfo has value/column/row-pointer vectors per device; dense D^2 is a
    conservative ceiling for each admitted R/L/B stamp. Charge doubled vector
    capacity and simultaneous old/new storage rather than logical length only.
    Input.cpp read/parse and Netlist expansion can coexist with copied tokens;
    four complete token sets cover those stages and line temporaries. With
    libstdc++'s 32-byte string objects and heap capacity <= 2*(length+1), 36B
    per source byte bounds the complete token set (including one-byte tokens).
    Function.cpp parse_pwl moves its two double vectors. Sourcegen growth can
    copy existing functions, so charge all 32 sources twice, each with 64 pairs.

    Pinned CMakeSuiteSparse f4bc356: klu.c clamps L/U to n(n+1)/2; real Unit,
    Int and Entry are eight bytes. klu_kernel.c grows by 1.2*capacity+2n+1,
    with at most n entries per dense column in each factor. Charge both buffers
    across realloc, all off-diagonal n^2 entries, linear index/solve work,
    structs and AMD's <=2.4*nz+7n workspace. Two complete factorization budgets
    cover the old KLU state during a retry matrix rebuild. No KLU allocation
    scales with the number of internal timesteps. All arithmetic below is on
    small fixed constants, and history allocation is preceded by the native
    dimension/selected-variable/row checks.

    The approved 512 MiB native/import reserve also covers allocator metadata,
    library/regex/stack overhead and bounded device/container object owners.
    This is a source/shape charge, not an RSS-derived multiplier. Final worker
    integration, serialization and binary-bound admission remain separate.
    """
    d, k, rows, devices, source_bytes = 80, 24, 4096, 32, 65536
    history = 8*(3*d + 3*k*rows + rows) + 80*32 + 512
    output = 8*(9*rows + rows) + 512  # time/traces, selected-index vector
    token_sets = 4*(36*source_bytes + 2*256*24)
    source_vectors = 2 * devices * 2 * 64 * 8
    matrix_vectors = 4 * 3 * d*d * 8 * (devices+1)
    factor_units = 2*d*(d+1)
    grown_units = math.ceil(1.2*factor_units) + 2*d+1
    factors_and_realloc = 2 * grown_units * 8
    numeric = factors_and_realloc + 16*(d*d+1) + 8*27*d + 1024
    ordering = 8*(5*d*d + 21*d + 2)
    klu = 2*(numeric + ordering)
    charges = {'history': history, 'output_and_indices': output,
               'source_tokens_and_temporaries': token_sets,
               'all_source_vectors_and_copy_overlap': source_vectors,
               'device_matrix_vectors_and_copy_overlap': matrix_vectors,
               'klu_factor_retry_reallocation_and_ordering': klu,
               'rhs_with_sentinel_and_copy_overlap': 4*(d+1)*8,
               'native_import_reserve': 512*1024**2}
    return {'components_bytes': charges, 'total_bytes': sum(charges.values()),
            'maximum_charge_bytes': 2*1024**3,
            'source_commit': SOURCE_COMMIT, 'build_image':
            'sha256:30465f32041d36dc6f3d0ac1e20fe5b856f72a25943f74829683cfc7fea61b79',
            'source_date_epoch': 1792989136,
            'integration_certificate': 'not enabled; final binary/worker admission is separate'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--instrumented', type=Path, help='same patched source compiled with WQS_JOSIM_HISTORY_AUDIT')
    parser.add_argument('--guard-seconds', type=float, default=30,
                        help='fixed discovery harness guard only; never product policy')
    args = parser.parse_args()
    if not math.isfinite(args.guard_seconds) or not 0 < args.guard_seconds <= 120:
        parser.error('discovery guard must be in (0, 120] seconds')
    identities = {}
    for label, path in (('original', args.original), ('candidate', args.candidate)):
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            parser.error(f'{label} requires a trusted absolute executable path')
        identities[label] = hashlib.sha256(path.read_bytes()).hexdigest()
    if identities['original'] != ORIGINAL_SHA256:
        parser.error('original binary differs from the frozen pinned reference')
    result = {'source_commit': SOURCE_COMMIT, 'binaries': identities,
              'self_comparison_only': identities['original'] == identities['candidate'],
              'harness_checks': _self_checks(), 'fixtures': {},
              'known_capability_debt': {
                  'DEVV_I01': 'native exit0 drops the column with warning; WQS exact-header boundary rejects; TASK-020/021/G2'}}
    fixtures = _fixtures()
    if set(fixtures) != set(FROZEN):
        raise ValueError('fixture coverage differs from frozen evidence')
    for name, (netlist, success) in fixtures.items():
        original = _run(args.original, netlist, success, args.guard_seconds)
        candidate = _run(args.candidate, netlist, success, args.guard_seconds)
        _compare(original, candidate)
        frozen = {key: original[0][key] for key in ('status', 'diagnostic_sha256', 'csv_sha256', 'array_sha256', 'rows', 'bytes')}
        frozen['header'] = list(original[2][0]) if success else []
        frozen['fixture_sha256'] = hashlib.sha256(netlist.encode('ascii')).hexdigest()
        if frozen != FROZEN.get(name):
            raise ValueError(f'{name}: original differs from frozen evidence')
        result['fixtures'][name] = dict(frozen, original_seconds=original[0]['elapsed_seconds'],
                                       candidate_seconds=candidate[0]['elapsed_seconds'])
        if name == 'sfq':
            header, rows, _ = original[2]
            integrals = []
            for column in (1, 3):
                selected = [row for row in rows if 50e-12 <= row[0] <= 200e-12]
                flux = sum((b[0] - a[0]) * (a[column] + b[column]) / 2
                           for a, b in zip(selected, selected[1:]))
                integrals.append(flux / PHI0)
            if not all(abs(value - 1) <= 0.02 for value in integrals):
                raise ValueError('separate SFQ physical integral check failed')
            result['sfq_flux_quanta'] = integrals
    if args.instrumented:
        if (not args.instrumented.is_absolute() or not args.instrumented.is_file()
                or not os.access(args.instrumented, os.X_OK)):
            parser.error('instrumented requires a trusted absolute executable path')
        result['instrumented_sha256'] = hashlib.sha256(args.instrumented.read_bytes()).hexdigest()
        if (identities['candidate'] != BUILD_PROVENANCE['candidate_binary_sha256']
                or result['instrumented_sha256'] != BUILD_PROVENANCE['instrumented_binary_sha256']):
            raise ValueError('instrumented/candidate differs from the source-matched build receipt')
        result['build_provenance'] = BUILD_PROVENANCE
        result['workspace_charge'] = _workspace_charge()
        if result['workspace_charge']['total_bytes'] > result['workspace_charge']['maximum_charge_bytes']:
            raise ValueError('source-derived planned charge exceeded')
        result['capacities'] = {}
        for name, netlist in _capacity_cases().items():
            measured = _run(args.instrumented, netlist, True, args.guard_seconds, audit=True)
            record = measured[0]['history']
            bound = _verify_capacity(record)
            # Instrumentation is permitted to observe allocation only. Every
            # long/max-shape candidate CSV must match its instrumented build.
            native = _run(args.candidate, netlist, True, args.guard_seconds)
            _compare(native, measured)
            if name.startswith('maximum_'):
                _compare(_run(args.original, netlist, True, args.guard_seconds), native)
            result['capacities'][name] = {**record, **bound, 'csv_sha256': native[0]['csv_sha256']}
        dc = [result['capacities'][name] for name in ('dc_1ns', 'dc_10ns', 'dc_100ns', 'dc_1us')]
        if len({r['history_capacity_bytes'] for r in dc}) != 1:
            raise ValueError('history capacity grows with native internal steps')
        if any(b['internal_steps'] <= a['internal_steps'] for a, b in zip(dc, dc[1:])):
            raise ValueError('internal-step growth experiment did not grow')
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
