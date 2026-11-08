from pathlib import Path

import pytest

from qsim.josim_validation import validate_josim_netlist


FIXTURE = Path(__file__).parent / 'fixtures/physics/josim/sfq.cir'


def minimal(*, source='DC 1u', transient='.tran 0.25p 250p', traces='.print DEVV B1'):
    return ('I1 0 1 ' + source + '\nR1 1 0 5\nL1 1 2 2p\n'
            'B1 2 0 J AREA=1\n'
            '.model J JJ(RTYPE=1 VG=2.8mV CAP=0.07pF R0=160 RN=16 ICRIT=0.1mA)\n'
            + transient + '\n' + traces + '\n.end\n')


def test_native_fixture_is_accepted_with_ordered_si_metadata():
    normalized, info = validate_josim_netlist(FIXTURE.read_text())
    assert normalized.endswith('.END\n')
    assert '*' not in normalized and normalized == normalized.upper()
    assert info['device_count'] == 16
    assert info['trace_names'] == ['V(B01)', 'P(B01)', 'V(B02)', 'P(B02)', 'I(ROUT)']
    assert info['trace_units'] == ['V', 'rad', 'V', 'rad', 'A']
    assert info['predicted_rows'] == 1002  # Conservative ceiling of the parsed binary-float ratio.
    assert info['step_seconds'] == 0.25e-12
    assert info['stop_seconds'] == 250e-12


def test_normalization_is_repeatable_and_preserves_supported_continuations():
    text = minimal(source='PWL(0 0 5p 1u)')
    changed = text.lower().replace('5p 1u', '5p\n+ 1u').replace('r1 1 0 5', '  r1\t1 0 5  ')
    first = validate_josim_netlist(text)
    assert validate_josim_netlist(changed) == first
    assert validate_josim_netlist(first[0]) == first
    assert validate_josim_netlist('* comment\n# comment\n' + text) == first


@pytest.mark.parametrize('suffix,expected', [('1', 1.0), ('1e-3', .001), ('1m', .001), ('1k', 1000.0)])
def test_finite_numeric_source_literals(suffix, expected):
    _, info = validate_josim_netlist(minimal(source='DC ' + suffix))
    assert info['trace_names'] == ['V(B1)']


@pytest.mark.parametrize('replacement', ['.tran 1p 4095p', '.tran 1p 1p'])
def test_transient_boundaries_accept_exact_structural_limits(replacement):
    _, info = validate_josim_netlist(minimal(transient=replacement))
    assert info['predicted_rows'] <= 4096


@pytest.mark.parametrize('line,replacement', [
    ('R1 1 0 5', 'R1 1 0 0'), ('R1 1 0 5', 'R1 1 0 1.000001MEG'),
    ('L1 1 2 2p', 'L1 1 2 1.001m'), ('L1 1 2 2p', 'L1 1 1 2p'),
    ('B1 2 0 J AREA=1', 'B1 2 0 J AREA=0'),
    ('B1 2 0 J AREA=1', 'B1 2 0 J AREA=100.001'),
    ('B1 2 0 J AREA=1', 'B1 2 0 MISSING AREA=1'),
    ('RTYPE=1', 'RTYPE=2'), ('CAP=0.07pF', 'CAP=0'),
    ('CAP=0.07pF', 'CAP=-1p'), ('CAP=0.07pF', 'CAP=1pF CAP=2pF'),
    ('CAP=0.07pF', 'CAP=1pF EXTRA=2'),
    ('R0=160', 'R0=1e999'), ('R0=160', 'R0=nan'),
    ('R1 1 0 5', 'R1 1 0 5V'), ('L1 1 2 2p', 'L1 1 2 2pA'),
    ('VG=2.8mV', 'VG=2.8mA'), ('ICRIT=0.1mA', 'ICRIT=0.1mV'),
])
def test_model_device_numeric_and_unit_boundaries(line, replacement):
    with pytest.raises(ValueError):
        validate_josim_netlist(minimal().replace(line, replacement))


@pytest.mark.parametrize('source', [
    'PWL(0 0 0 1u)', 'PWL(1p 0 2p 1u)', 'PWL(0 0 1.01u 1u)',
    'PWL(0 0 1p)', 'PWL(0 0 1p nan)', 'DC inf', 'DC 1e-999',
    'PWL(0 0 1p 1V)',
])
def test_bad_or_underflowed_waveforms(source):
    with pytest.raises(ValueError):
        validate_josim_netlist(minimal(source=source))


@pytest.mark.parametrize('transient', [
    '.tran 0 1p', '.tran -1p 1p', '.tran 2p 1p', '.tran 1p 4096p',
    '.tran 1u 1.01u', '.tran 1e-999 1p', '.tran 1e-300 1p',
    '.tran 1p 5p 0 1p', '.tran 1pV 5p',
])
def test_bad_transient_bounds(transient):
    with pytest.raises(ValueError):
        validate_josim_netlist(minimal(transient=transient))


@pytest.mark.parametrize('trace', ['.print DEVV ABSENT', '.print PHASE R1', '.print DEVI L1\n.print DEVI L1', ''])
def test_trace_reference_type_and_duplicates(trace):
    with pytest.raises(ValueError):
        validate_josim_netlist(minimal(traces=trace))


def test_duplicate_devices_models_and_transients_are_rejected():
    for line in ['R1 1 0 5', '.tran 0.25p 250p', '.model J JJ(RTYPE=1 VG=2.8mV CAP=0.07pF R0=160 RN=16 ICRIT=0.1mA)']:
        with pytest.raises(ValueError):
            validate_josim_netlist(minimal().replace('.end', line + '\n.end'))


def test_structural_size_and_trace_limits():
    for text in ['*' + 'x' * 65536 + '\n' + minimal(), 'R' + 'A' * 32 + ' 1 0 5\n' + minimal(),
                 'R1 1 0 ' + '1' * 4090 + '\n' + minimal(), '\n'.join(['R2 2 0 5'] * 257) + minimal()]:
        with pytest.raises(ValueError):
            validate_josim_netlist(text)
    # Nine unique valid device traces exceed the independent trace bound.
    traces = '\n'.join(f'.print DEVV R{i}' for i in range(1, 10))
    extra = '\n'.join(f'R{i} 1 0 5' for i in range(2, 10)) + '\n'
    with pytest.raises(ValueError):
        validate_josim_netlist(extra + minimal(traces=traces))


def test_strict_request_defaults_canonicalize_to_explicit_envelope():
    from qsim.josim_models import JoSIMRequest
    request = JoSIMRequest(netlist=minimal())
    envelope = request.canonical_envelope()
    assert envelope == {
        'engine': 'josim', 'input_format': 'josim_netlist', 'schema_version': 1,
        'operation': 'transient', 'name': 'Untitled simulation',
        'netlist': validate_josim_netlist(minimal())[0],
    }
    assert JoSIMRequest(**envelope).canonical_envelope() == envelope
    assert JoSIMRequest(netlist=minimal().upper()).canonical_envelope() == envelope
    assert request.model_dump(mode='json') == envelope


@pytest.mark.parametrize('field,value', [
    ('engine', 'qsim'), ('input_format', 'qasm2'), ('operation', 'sample'),
    ('schema_version', True), ('schema_version', '1'), ('schema_version', 1.0),
    ('schema_version', 0), ('schema_version', 2), ('netlist', 5),
    ('name', ''), ('name', 'x' * 81), ('name', 42),
    ('shots', 1000), ('seed', 42), ('temperature', 4.2),
    ('noise', True), ('options', {}), ('resources', {'cpus': 32}),
    ('output', '/private'), ('model', {}), ('circuit', 'input'),
])
def test_request_refuses_inapplicable_fields_and_coercion(field, value):
    from pydantic import ValidationError
    from qsim.josim_models import JoSIMRequest
    with pytest.raises(ValidationError):
        JoSIMRequest(**{'netlist': minimal(), field: value})


def test_generated_schema_is_strict_and_describes_semantic_validation():
    from jsonschema import Draft202012Validator
    from qsim.josim_models import JoSIMRequest
    schema = JoSIMRequest.model_json_schema()
    Draft202012Validator.check_schema(schema)
    assert schema['additionalProperties'] is False
    assert schema['properties']['netlist']['maxLength'] == 65536
    assert 'Semantic validation' in schema['properties']['netlist']['description']
    assert schema['properties']['schema_version']['type'] == 'integer'
    assert schema['properties']['schema_version']['minimum'] == 1
    assert schema['properties']['schema_version']['maximum'] == 1


def test_rejected_netlist_validation_error_has_field_location_without_input_echo():
    from pydantic import ValidationError
    from qsim.josim_models import JoSIMRequest
    with pytest.raises(ValidationError) as error:
        JoSIMRequest(netlist=minimal().replace('.end', '.include /private\n.end'))
    errors = error.value.errors(include_input=False, include_context=False, include_url=False)
    assert errors[0]['loc'] == ('netlist',)
    assert '/private' not in errors[0]['msg']


def test_admission_plans_bound_numeric_result_and_preserves_canonical_identity(monkeypatch):
    from qsim import josim_models
    from qsim.physics_results import plan_result
    observed = []

    def checked(arrays, **kwargs):
        result = plan_result(arrays, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(josim_models, 'plan_result', checked)
    request = josim_models.JoSIMRequest(netlist=minimal())
    canonical_before = request.canonical_envelope()
    allocation = request._certified_allocation_plan()
    assert allocation == request._certified_allocation_plan()
    assert allocation.result == observed[0]
    assert len(observed) == 2
    plan = observed[0]
    rows = validate_josim_netlist(minimal())[1]['predicted_rows']
    assert [(s.name, s.shape, s.dtype) for s in plan.arrays] == [
        ('times', (rows,), 'float64'), ('traces', (rows, 1), 'float64')]
    assert plan.numeric_bytes == rows * 16
    assert plan.callback_upper_bytes < 12 * 1024**2
    assert request.canonical_envelope() == canonical_before
    with pytest.raises(ValueError, match='certificate is unavailable'):
        request.admission_plan()


@pytest.mark.parametrize('step,stop', [('1e-12', '1e-9'), ('1e-11', '1e-8'), ('1e-9', '1e-6')])
def test_small_output_grid_uses_fixed_bounded_history_not_internal_step_charge(step, stop):
    from qsim.josim_models import JoSIMRequest

    request = JoSIMRequest(netlist=(
        'B01 1 0 MOD AREA=1\n'
        'V01 1 0 DC 0.1\n'
        '.MODEL MOD JJ(RTYPE=1 VG=0.0028 CAP=7e-14 R0=160 RN=16 ICRIT=0.0001)\n'
        f'.TRAN {step} {stop}\n'
        '.PRINT PHASE B01\n.END\n'))
    _, info = validate_josim_netlist(request.netlist)
    assert 1000 <= info['predicted_rows'] <= 1002
    assert info['trace_names'] == ['P(B01)']
    with pytest.raises(ValueError, match='certificate is unavailable'):
        request.admission_plan()
    plan = request._certified_allocation_plan()
    assert dict(plan.allocation_items)['history'] == 2397056
    assert plan.peak_bytes < 2*1024**3
    assert plan.coefficient_plan['binary_sha256'] == (
        '0bca2802d5e152a1b4325cc4a9f4e8e5b01331d705d45ea757c72e0cb1afe676')


def test_numeric_node_labels_are_not_silently_merged():
    text = minimal().replace('L1 1 2 2p', 'L1 01 2 2p')
    normalized, _ = validate_josim_netlist(text)
    assert 'L1 01 2 2P' in normalized


def test_maximum_devices_and_nodes_have_independent_boundaries():
    # Four base devices plus 28 distinct parallel resistors use three nodes.
    extra = '\n'.join(f'R{i} 1 0 5' for i in range(2, 30)) + '\n'
    _, info = validate_josim_netlist(extra + minimal())
    assert info['device_count'] == 32
    with pytest.raises(ValueError, match='32 devices'):
        validate_josim_netlist('R30 1 0 5\n' + extra + minimal())
    extra_nodes = '\n'.join(f'R{i} {i} 0 5' for i in range(3, 16)) + '\n'
    _, info = validate_josim_netlist(extra_nodes + minimal())
    assert info['node_count'] == 16
    with pytest.raises(ValueError, match='16 nodes'):
        validate_josim_netlist('R16 16 0 5\n' + extra_nodes + minimal())


def test_logical_line_and_text_limits_apply_after_continuations():
    validate_josim_netlist('*' + 'x' * 4096 + '\n' + minimal())
    # Individually short continuations cannot grow an unbounded logical line.
    text = minimal().replace('DC 1u', 'DC 1u' + ('\n+ ' + '1 ' * 1000) * 3)
    with pytest.raises(ValueError, match='logical line'):
        validate_josim_netlist(text)


def test_pwl_pair_limit_and_exactly_eight_selected_traces():
    pairs = ' '.join(f'{i}p {i}u' for i in range(64))
    validate_josim_netlist(minimal(source=f'PWL({pairs})'))
    with pytest.raises(ValueError, match='64 numeric'):
        validate_josim_netlist(minimal(source=f'PWL({pairs} 64p 64u)'))
    extra = '\n'.join(f'R{i} 1 0 5' for i in range(2, 9)) + '\n'
    traces = '\n'.join(f'.print DEVV R{i}' for i in range(1, 9))
    assert len(validate_josim_netlist(extra + minimal(traces=traces))[1]['trace_names']) == 8


@pytest.mark.parametrize('before,after', [
    ('R1 1 0 5', 'R1 1 0 0'), ('R1 1 0 5', 'R1 1 0 1e7'),
    ('L1 1 2 2p', 'L1 1 2 1'), ('L1 1 2 2p', 'L1 1 1 2p'),
    ('AREA=1', 'AREA=-1'), ('RTYPE=1', 'RTYPE=2'),
    ('VG=2.8mV', 'VG=0'), ('R0=160', 'R0=1e13'),
    ('DC 1u', 'DC 1e13'), ('.tran 0.25p 250p', '.tran 0 1p'),
    ('.tran 0.25p 250p', '.tran 1p 1u'),
    ('.print DEVV B1', '.print DEVV ABSENT'),
    ('.print DEVV B1', '.print PHASE R1'),
    ('B1 2 0 J AREA=1', 'B1 2 0 ABSENT AREA=1'),
    ('DC 1u', 'PWL(0 0 0 1u)'),
])
def test_finite_physical_and_resource_refusals_follow_canonicalization(before, after):
    from qsim.josim_models import JoSIMRequest
    text = minimal().replace(before, after)
    request = JoSIMRequest(netlist=text)
    assert JoSIMRequest(**request.canonical_envelope()).canonical_envelope() == request.canonical_envelope()
    with pytest.raises(ValueError):
        request.admission_plan()
    with pytest.raises(ValueError):
        request._certified_allocation_plan()
    with pytest.raises(ValueError):
        validate_josim_netlist(request.netlist)


def test_resource_collection_limits_follow_canonicalization():
    from qsim.josim_models import JoSIMRequest
    too_many_devices = '\n'.join(f'R{i} 1 0 5' for i in range(2, 31)) + '\n' + minimal()
    too_many_pairs = minimal(source='PWL(' + ' '.join(f'{i}p {i}u' for i in range(65)) + ')')
    for text in [too_many_devices, too_many_pairs]:
        request = JoSIMRequest(netlist=text)
        assert request.canonical_envelope()['netlist'] == request.netlist
        with pytest.raises(ValueError):
            request.admission_plan()


def test_current_admission_policy_does_not_change_normalized_request_identity(monkeypatch):
    from qsim import josim_models
    original = josim_models.JoSIMRequest(netlist=minimal()).canonical_envelope()

    def changed_policy(text):
        raise ValueError('resource_limit: new policy')

    monkeypatch.setattr(josim_models, 'validate_josim_netlist', changed_policy)
    replay = josim_models.JoSIMRequest(**original)
    assert replay.canonical_envelope() == original
    with pytest.raises(ValueError, match='new policy'):
        replay.admission_plan()


def test_certificate_matches_frozen_source_charge_and_build_identity():
    import runpy
    from qsim.josim_models import JoSIMRequest

    proof = runpy.run_path('spec/physics-engines/probe_josim_history.py')
    plan = JoSIMRequest(netlist=minimal())._certified_allocation_plan()
    receipt = proof['BUILD_PROVENANCE']
    certificate = plan.coefficient_plan
    assert certificate['binary_sha256'] == '0bca2802d5e152a1b4325cc4a9f4e8e5b01331d705d45ea757c72e0cb1afe676'
    assert certificate['source_commit'] == receipt['upstream_commit']
    for key in ('patch_sha256', 'patched_source_sha256', 'source_date_epoch', 'build_type'):
        assert certificate[key] == receipt[key]
    assert certificate['build_image'] == 'python@sha256:dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0'
    assert certificate['compiler'] == 'g++ (Debian 12.2.0-14+deb12u1) 12.2.0'
    assert certificate['binary_sha256'] != receipt['candidate_binary_sha256']
    assert certificate['binary_sha256'] != receipt['original_binary_sha256']
    assert certificate['binary_sha256'] != receipt['instrumented_binary_sha256']
    charge = proof['_workspace_charge']()
    items = dict(plan.allocation_items)
    assert {key: items[key] for key in charge['components_bytes']} == charge['components_bytes']
    assert sum(charge['components_bytes'].values()) == 570708992
    assert plan.peak_bytes == sum(items.values()) < 2*1024**3
    assert plan.policy_revision == 'josim-cpu-bookworm-nodev-v1'
    assert plan.algorithm == 'josim-phase-bounded-history-nodev-v1'
    assert plan.dimensions['native_dimension_upper_bound'] == 80
    assert plan.dimensions['selected_variables_upper_bound'] == 24
    assert certificate['phase_analysis'] is True
    assert certificate['startup'] is True
    assert certificate['print_start_seconds'] == 0
    assert certificate['fir_window_seconds'] == 0
    assert not any('deadline' in key or 'iteration' in key or 'internal_steps' in key
                   for key in certificate)
    assert certificate['cpu_count'] == 2
    assert certificate['scheduler_memory_bytes'] == 4*1024**3
    assert certificate['child_address_space_bytes'] == 3*1024**3
    assert certificate['child_file_bytes'] == 12*1024**2
    assert 'worker_image' not in certificate


def test_largest_certified_grammar_shapes_include_output_and_parent_child_overlap():
    import runpy
    from qsim.josim_models import JoSIMRequest

    proof = runpy.run_path('spec/physics-engines/probe_josim_history.py')
    for text in proof['_capacity_cases']().values():
        plan = JoSIMRequest(netlist=text)._certified_allocation_plan()
        items = dict(plan.allocation_items)
        assert plan.peak_bytes == sum(items.values()) < 2*1024**3
        assert items['csv_payload_and_text'] >= 6*6*1024**2
        assert items['parsed_python_rows'] >= 4096*(256 + 32*9)
        assert items['numeric_result_arrays'] >= 2*plan.result.numeric_bytes
        assert items['npz_serialization_buffers'] >= 4*plan.result.npz_upper_bytes
        assert items['base64_bytes_and_ascii_string'] >= 8*((plan.result.npz_upper_bytes+2)//3)
        assert items['callback_json_unicode_and_utf8'] >= 5*plan.result.callback_upper_bytes
        assert items['parent_child_handoff'] >= 2*plan.result.callback_upper_bytes


def test_plan_certificate_cannot_be_mutated_to_change_later_admission():
    from qsim.josim_models import JoSIMRequest

    request = JoSIMRequest(netlist=minimal())
    original = request._certified_allocation_plan()
    altered = request._certified_allocation_plan()
    altered.coefficient_plan['binary_sha256'] = '0'*64
    altered.coefficient_plan['patched_source_sha256']['src/Simulation.cpp'] = '0'*64
    altered.dimensions['native_dimension_upper_bound'] = 800
    assert request._certified_allocation_plan() == original
    assert request._certified_allocation_plan() != altered


def test_native_projection_preserves_canonical_request_and_duplicate_trace_positions():
    from qsim.josim_models import JoSIMRequest
    text = minimal(traces='.print DEVV I1\n.print DEVV R1\n.print DEVI I1\n.print DEVV I2')
    text = 'I2 0 1 DC 2u\n' + text
    request = JoSIMRequest(netlist=text)
    before = request.canonical_envelope()
    normalized, info = validate_josim_netlist(request.netlist)
    assert normalized == request.netlist
    assert info['native_netlist'] == normalized.replace('.PRINT DEVV I1', '.PRINT NODEV 1 0').replace('.PRINT DEVV I2', '.PRINT NODEV 1 0')
    assert info['native_trace_names'] == ['V(1,0)', 'V(R1)', 'I(I1)', 'V(1,0)']
    assert info['trace_projection'] == [
        {'public_name': 'V(I1)', 'native_name': 'V(1,0)', 'scale': -1, 'rewritten': True},
        {'public_name': 'V(R1)', 'native_name': 'V(R1)', 'scale': 1, 'rewritten': False},
        {'public_name': 'I(I1)', 'native_name': 'I(I1)', 'scale': 1, 'rewritten': False},
        {'public_name': 'V(I2)', 'native_name': 'V(1,0)', 'scale': -1, 'rewritten': True},
    ]
    assert request.canonical_envelope() == before
    plan = request._certified_allocation_plan()
    assert dict(plan.allocation_items)['native_projection_envelope'] == 1024**2
    assert plan.coefficient_plan['trace_projection'] == info['trace_projection']


def test_projection_owner_and_copy_bound_fits_approved_envelope():
    from dataclasses import asdict, replace
    import json
    import runpy
    import sys
    from qsim.josim_models import JoSIMRequest

    assert sys.version_info[:2] == (3, 12)
    assert sys.getsizeof('') <= 49
    assert sys.getsizeof(b'') == 33
    assert sys.getsizeof([]) == 56
    assert sys.getsizeof({'a': 1, 'b': 2, 'c': 3, 'd': 4}) == 184
    assert sys.getsizeof(1.0) == 24
    proof = runpy.run_path('spec/physics-engines/probe_josim_history.py')
    plan = JoSIMRequest(netlist=proof['_capacity_cases']()['maximum_source_storage'])._certified_allocation_plan()
    # Worst-length permitted labels, independent of this fixture's short names.
    table = [{'public_name': 'V('+'I'*32+')', 'native_name': 'V('+'R'*32+')',
              'scale': -1, 'rewritten': True} for _ in range(8)]
    assert len(json.dumps(table)) <= 2048
    certificate = dict(plan.coefficient_plan, trace_projection=table)
    worst = replace(plan, coefficient_plan=certificate)
    encoded = json.dumps(asdict(worst), sort_keys=True, allow_nan=False)
    assert encoded.isascii() and len(encoded) <= 8192

    def owners(value):
        # Counting repeated references repeatedly is conservative here.
        size = sys.getsizeof(value)
        if isinstance(value, dict):
            size += sum(owners(k)+owners(v) for k, v in value.items())
        elif isinstance(value, (list, tuple)):
            size += sum(owners(v) for v in value)
        return size

    assert owners(asdict(worst)) <= 32768
    # Two validator invocations can overlap during full-plan reproduction.
    # Their generated strings/fragments include list slots and string headers.
    # Hashing/writing ASCII is charged twice even though those stages serialize.
    # Full-plan asdict/JSON comparisons and metadata table copies are separate.
    components = {
        'native_token_delta': 4*36*(65568-65536),
        'two_generated_text_and_fragment_sets': 2*(2*(65568+49)+256*(49+8)),
        'encoded_hash_and_write_copies': 2*(65568+33),
        'three_projection_tables': 3*(56+8*8+8*(184+2*(49+35)+2*28)),
        'three_native_header_lists': 3*(56+8*8),
        'table_serialization_unicode_and_utf8': 3*5*2048,
        'two_full_plan_asdict_trees': 2*32768,
        'two_ascii_full_plan_comparison_strings': 2*8192,
        'source_terminal_map': 12288,
        'one_row_projection_float_temporaries': 9*24,
        # The implementation edits parsed rows in place, so uses none of this
        # permitted extra full-matrix allowance. No hidden history is retained.
        'permitted_single_projected_matrix': 8*4096*8,
    }
    assert components['native_token_delta'] == 4608
    assert sum(components.values()) <= dict(plan.allocation_items)['native_projection_envelope'] == 1024**2
    assert plan.peak_bytes == 622775052


def test_full_public_byte_cap_can_expand_only_into_fixed_native_byte_cap():
    from qsim.josim_models import JoSIMRequest
    sources = [f'I{i} 98 99 DC 1.' for i in range(1, 17)]
    tail = ['R1 98 0 1', 'R2 99 0 1',
            '.MODEL J JJ(RTYPE=1 VG=2mV CAP=1pF R0=100 RN=10 ICRIT=1mA)',
            '.TRAN 1p 5p', *[f'.PRINT DEVV I{i}' for i in range(1, 9)], '.END']
    remaining = 65536-len('\n'.join([*sources, *tail])+'\n')
    for index, line in enumerate(sources):
        count = min(4096-len(line), remaining)
        sources[index] += '0'*count
        remaining -= count
    assert remaining == 0
    text = '\n'.join([*sources, *tail])+'\n'
    request = JoSIMRequest(netlist=text)
    _, info = validate_josim_netlist(request.netlist)
    assert len(text) == len(request.netlist) == 65536
    assert len(info['native_netlist']) == 65568
    assert len(info['trace_projection']) == 8
    assert all(entry['scale'] == 1 and entry['rewritten'] for entry in info['trace_projection'])
    with pytest.raises(ValueError):
        JoSIMRequest(netlist='*\n'+text)
