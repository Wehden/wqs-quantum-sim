"""Source boundary and real-binary checks for the pinned history correction."""
from pathlib import Path
import hashlib
import os
import re
import runpy

import pytest


def test_patch_targets_exact_audited_sources():
    patch = Path('vendor/josim/bounded-history.patch').read_text()
    paths = re.findall(r'^\+\+\+ b/(.+)$', patch, re.MULTILINE)
    assert set(paths) == {'include/JoSIM/Simulation.hpp', 'src/Simulation.cpp',
                          'src/Output.cpp', 'include/JoSIM/Transient.hpp'}
    assert len(paths) == 4


PROBE = runpy.run_path('spec/physics-engines/probe_josim_history.py')


def test_capacity_verifier_accounts_for_rhs_sentinel():
    record = {'bounded': True, 'dimension': 80, 'selected_variables': 24,
              'rows': 4096, 'history_capacity_bytes': 2397056,
              'rhs_capacity_bytes': 2*81*8, 'attempts': 18,
              'internal_steps': 262144000, 'final_attempt_steps': 131072000}
    bounds = PROBE['_verify_capacity'](record)
    assert bounds['rhs_bound_bytes'] >= 2*81*8
    for field, value in [('dimension', 81), ('selected_variables', 25), ('rows', 4097),
                         ('history_capacity_bytes', bounds['history_bound_bytes']+1),
                         ('rhs_capacity_bytes', bounds['rhs_bound_bytes']+1),
                         ('internal_steps', 0), ('bounded', False)]:
        with pytest.raises(ValueError):
            PROBE['_verify_capacity']({**record, field: value})


@pytest.mark.parametrize('name', sorted(PROBE['FROZEN']))
def test_real_original_candidate_exact_frozen_output(name):
    original = os.environ.get('WQS_TEST_JOSIM_ORIGINAL')
    candidate = os.environ.get('WQS_TEST_JOSIM_CANDIDATE')
    if not original or not candidate:
        pytest.skip('Distinct original/candidate executables required for native acceptance')
    original, candidate = Path(original), Path(candidate)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == PROBE['ORIGINAL_SHA256']
    assert hashlib.sha256(candidate.read_bytes()).hexdigest() != PROBE['ORIGINAL_SHA256']
    netlist, success = PROBE['_fixtures']()[name]
    reference = PROBE['_run'](original, netlist, success, 30)
    patched = PROBE['_run'](candidate, netlist, success, 30)
    PROBE['_compare'](reference, patched)
    assert {key: reference[0][key] for key in ('status', 'diagnostic_sha256', 'csv_sha256',
                                             'array_sha256', 'rows', 'bytes')} == {
        key: PROBE['FROZEN'][name][key] for key in ('status', 'diagnostic_sha256', 'csv_sha256',
                                                 'array_sha256', 'rows', 'bytes')}


@pytest.mark.parametrize('name', ['maximum_history', 'maximum_pwl', 'maximum_source_storage'])
def test_real_maximum_history_and_rhs_sentinel(name):
    binary = os.environ.get('WQS_TEST_JOSIM_INSTRUMENTED')
    if not binary:
        pytest.skip('Source-matched instrumented executable required for native acceptance')
    measured = PROBE['_run'](Path(binary), PROBE['_capacity_cases']()[name], True, 30, audit=True)
    record = measured[0]['history']
    PROBE['_verify_capacity'](record)
    assert record['rows'] == 4095
    if name == 'maximum_source_storage':
        assert record['dimension'] == 2 and record['selected_variables'] == 0
    else:
        assert record['dimension'] >= 77
        assert record['selected_variables'] >= 23
    assert record['rhs_capacity_bytes'] >= 2 * (record['dimension'] + 1) * 8


def test_checked_transient_index_representation(tmp_path):
    source = os.environ.get('WQS_TEST_JOSIM_SOURCE')
    if not source:
        pytest.skip('Pinned patched native headers required for representation acceptance')
    import subprocess
    unit = tmp_path / 'indices.cpp'
    unit.write_text(r'''
#include "JoSIM/Transient.hpp"
#include <cassert>
#include <cmath>
#include <limits>
int main() {
    JoSIM::Transient t;
    t.tstop(1.0);
    t.tstep(1.0 / 4096);
    assert(t.simsize() == 4096);
    t.tstep(1.0);
    t.tstop(std::nextafter(std::ldexp(1.0, 63), 0.0));
    assert(t.simsize() == static_cast<int64_t>(std::nextafter(std::ldexp(1.0, 63), 0.0)));
    t.tstop(1.0);
    for (double step : {0.0, -1.0, std::numeric_limits<double>::quiet_NaN(),
                        std::numeric_limits<double>::infinity(),
                        std::ldexp(1.0, -63), std::numeric_limits<double>::denorm_min()}) {
        t.tstep(step);
        bool rejected = false;
        try { t.simsize(); } catch (const std::runtime_error&) { rejected = true; }
        assert(rejected);
    }
}
''')
    binary = tmp_path / 'indices'
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-I'+str(Path(source)/'include'),
                    str(unit), '-o', str(binary)], check=True, capture_output=True, timeout=30)
    subprocess.run([str(binary)], check=True, capture_output=True, timeout=5)


def test_largest_native_fixtures_remain_within_admitted_positive_grammar():
    from qsim.josim_validation import validate_josim_netlist
    for name, netlist in PROBE['_capacity_cases']().items():
        normalized, info = validate_josim_netlist(netlist)
        assert len(normalized.encode('ascii')) <= 65536
        if name.startswith('maximum_'):
            assert info['device_count'] == 32
            assert info['predicted_rows'] == 4096
            assert len(info['trace_names']) == 8
    charge = PROBE['_workspace_charge']()
    assert charge['total_bytes'] <= 2*1024**3
    assert charge['components_bytes']['all_source_vectors_and_copy_overlap'] >= 2*32*2*64*8


@pytest.mark.parametrize('legacy', ['fir', 'voltage', 'no_startup'])
def test_unsupported_history_modes_keep_exact_legacy_path(tmp_path, legacy):
    import json
    import subprocess
    names = ('WQS_TEST_JOSIM_ORIGINAL', 'WQS_TEST_JOSIM_CANDIDATE',
             'WQS_TEST_JOSIM_INSTRUMENTED')
    binaries = [os.environ.get(name) for name in names]
    if not all(binaries):
        pytest.skip('Original/candidate/instrumented executables required for legacy acceptance')
    netlist = PROBE['_fixtures']()['families'][0]
    flags = []
    if legacy == 'fir':
        netlist = netlist.replace('.TRAN 0.25p 20p', '.TRAN 0.25p 20p 0 0.25p 0.5p')
    elif legacy == 'voltage':
        flags = ['-a', '0']
    else:
        netlist = netlist.replace('.TRAN 0.25p 20p', '.TRAN 0.25p 20p DST')
    outputs = []
    for index, binary in enumerate(binaries):
        root = tmp_path / str(index)
        root.mkdir()
        (root / 'input.cir').write_text(netlist)
        result = subprocess.run([binary, '-m', *flags, '-o', 'output.csv', 'input.cir'],
                                cwd=root, env={'PATH': '/usr/bin:/bin', 'OMP_NUM_THREADS': '1'},
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                preexec_fn=PROBE['_limits'], timeout=30, check=True)
        outputs.append((root / 'output.csv').read_bytes())
        if index == 2:
            records = [json.loads(line.removeprefix(b'WQS_HISTORY '))
                       for line in result.stderr.splitlines() if line.startswith(b'WQS_HISTORY ')]
            assert len(records) == 1 and records[0]['bounded'] is False
    assert outputs[0] == outputs[1] == outputs[2]


def test_build_receipt_pins_patch_and_applied_source():
    receipt = PROBE['BUILD_PROVENANCE']
    assert receipt['upstream_commit'] == PROBE['SOURCE_COMMIT']
    assert receipt['original_binary_sha256'] == PROBE['ORIGINAL_SHA256']
    assert hashlib.sha256(Path('vendor/josim/bounded-history.patch').read_bytes()).hexdigest() == receipt['patch_sha256']
    source = os.environ.get('WQS_TEST_JOSIM_SOURCE')
    if source:
        for name, expected in receipt['patched_source_sha256'].items():
            assert hashlib.sha256((Path(source)/name).read_bytes()).hexdigest() == expected


def test_real_tiny_finite_timestep_saturates_existing_startup_clamp():
    binary = os.environ.get('WQS_TEST_JOSIM_CANDIDATE')
    if not binary:
        pytest.skip('Patched native binary required for startup representation acceptance')
    from qsim.josim_validation import validate_josim_netlist
    netlist = ('V01 1 0 DC 0\nR01 1 0 1\n' + PROBE['MODEL'] +
               '.TRAN 1e-200 1e-197\n.PRINT DEVV R01\n.PRINT DEVI R01\n.END\n')
    validate_josim_netlist(netlist)
    result = PROBE['_run'](Path(binary), netlist, True, 10)
    assert result[0]['rows'] >= 999
    assert all(all(value == 0 for value in row[1:]) for row in result[2][1])
