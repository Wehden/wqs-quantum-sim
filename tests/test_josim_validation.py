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


def test_admission_plans_numeric_result_then_fails_closed_for_missing_native_certificate(monkeypatch):
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
    with pytest.raises(ValueError, match='resource_limit: JoSIM native workspace certificate'):
        request.admission_plan()
    assert len(observed) == 1
    plan = observed[0]
    rows = validate_josim_netlist(minimal())[1]['predicted_rows']
    assert [(s.name, s.shape, s.dtype) for s in plan.arrays] == [
        ('times', (rows,), 'float64'), ('traces', (rows, 1), 'float64')]
    assert plan.numeric_bytes == rows * 16
    assert plan.callback_upper_bytes < 12 * 1024**2
    assert request.canonical_envelope() == canonical_before


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
