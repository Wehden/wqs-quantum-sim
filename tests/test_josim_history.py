"""Source boundary and real-binary checks for the pinned history correction."""
from pathlib import Path
import re


def test_patch_targets_exact_audited_sources():
    patch = Path('vendor/josim/bounded-history.patch').read_text()
    paths = re.findall(r'^\+\+\+ b/(.+)$', patch, re.MULTILINE)
    assert set(paths) == {'include/JoSIM/Simulation.hpp', 'src/Simulation.cpp',
                          'src/Output.cpp', 'include/JoSIM/Transient.hpp'}
    assert len(paths) == 4


import hashlib
import os
import runpy

import pytest

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


@pytest.mark.parametrize('name', ['maximum_history', 'maximum_pwl'])
def test_real_maximum_history_and_rhs_sentinel(name):
    binary = os.environ.get('WQS_TEST_JOSIM_INSTRUMENTED')
    if not binary:
        pytest.skip('Source-matched instrumented executable required for native acceptance')
    measured = PROBE['_run'](Path(binary), PROBE['_capacity_cases']()[name], True, 30, audit=True)
    record = measured[0]['history']
    PROBE['_verify_capacity'](record)
    assert record['rows'] == 4096
    assert record['dimension'] >= 77
    assert record['selected_variables'] >= 23
    assert record['rhs_capacity_bytes'] >= 2 * (record['dimension'] + 1) * 8
