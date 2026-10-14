"""Pre-allocation guards and safe failures for untrusted QASM requests."""
from unittest.mock import Mock

import pytest
from cirq.contrib.qasm_import._parser import QasmParser

from qsim.qasm_adapter import prepare
from test_qasm_sampling import PREFIX, run_sample


def request(suffix, shots=1000):
    return dict(engine='qsim', input_format='qasm2', operation='sample', shots=shots,
                circuit=PREFIX + suffix)


@pytest.mark.parametrize('suffix', [
    'qreg q[1000000000]; h q;',
    'qreg q[13]; qreg r[14]; h r;',
    'qreg q[1]; creg c[1000000000]; measure q[0] -> c[0];',
    'qreg q[1]; creg a[600]; creg b[401]; measure q[0] -> a[0];',
    'qreg q[1]; rx(2^1000000000) q[0];',
    'qreg q[1]; creg c[1]; if(c==0) x q[0];',
    'qreg q[1]; creg c[1]; if(c==1) x q[0];',
    'qreg q[1]; rx(1e309) q[0];',
])
def test_unsafe_input_rejected_before_parser(monkeypatch, suffix):
    parser = Mock(side_effect=AssertionError('parser must not run'))
    monkeypatch.setattr(QasmParser, 'parse', parser)
    with pytest.raises(ValueError, match='^(resource_limit|unsupported_feature|invalid_circuit):'):
        prepare(request(suffix))
    parser.assert_not_called()


@pytest.mark.parametrize('include', [
    'qelib1Xinc', 'stdgatesXinc', '../private.inc', '/private.inc',
    'https://example.invalid/private.inc',
])
def test_disallowed_includes_never_reach_parser(monkeypatch, include):
    parser = Mock(side_effect=AssertionError('parser must not run'))
    monkeypatch.setattr(QasmParser, 'parse', parser)
    payload = request('qreg q[1];')
    payload['circuit'] = f'OPENQASM 2.0; include "{include}"; qreg q[1];'
    with pytest.raises(ValueError, match='^(unsupported_feature|invalid_circuit):'):
        prepare(payload)
    parser.assert_not_called()


def test_comment_text_does_not_trigger_code_guards():
    payload = request('// if(c==1) x q; include "../private.inc"; 2^1000000000\n'
        'qreg q[1]; creg c[1]; measure q -> c;')
    _, metadata = prepare(payload)
    assert metadata['classical_bits'] == 1
    assert metadata['events'] == {'c_0': 1}


@pytest.mark.parametrize('classical_bits,allowed', [(999, True), (1000, False)])
def test_combined_memory_boundary_includes_unwritten_bits(classical_bits, allowed):
    # Exactly 1,000,000 cells at 999 classical columns + one event, 1,000 shots.
    payload = request(f'qreg q[1]; creg c[{classical_bits}]; measure q[0] -> c[0];')
    if allowed:
        _, metadata = prepare(payload)
        assert metadata['classical_bits'] == classical_bits
        assert sum(metadata['events'].values()) == 1
    else:
        with pytest.raises(ValueError, match='^resource_limit:'):
            prepare(payload)


@pytest.mark.parametrize('events,allowed', [(99, True), (100, False)])
def test_combined_memory_boundary_counts_repeated_writes(events, allowed):
    # Native records retain every write even though final memory has one column.
    payload = request('qreg q[1]; creg c[1]; ' + 'measure q[0] -> c[0]; ' * events,
                      shots=10000)
    if allowed:
        _, metadata = prepare(payload)
        assert metadata['events'] == {'c_0': events}
    else:
        with pytest.raises(ValueError, match='^resource_limit:'):
            prepare(payload)


@pytest.mark.parametrize('expression', ['1/0', 'sqrt(-1)', 'ln(0)', '1e309'])
def test_invalid_numeric_angles_fail_safely_through_worker(tmp_path, expression):
    result = run_sample(tmp_path,
        f'qreg q[1]; creg c[1]; rx({expression}) q[0]; measure q -> c;')
    assert result['status'] == 'FAILED'
    assert result['error'].startswith('invalid_circuit:')
    assert 'artifact' not in result
    assert 'summary' not in result
    assert expression not in result['error']


def test_unknown_source_identifier_is_not_exposed(tmp_path):
    sentinel = 'PRIVATE_QASM_SENTINEL_23879'
    result = run_sample(tmp_path,
        f'qreg q[1]; creg c[1]; {sentinel} q[0]; measure q -> c;')
    assert result['status'] == 'FAILED'
    assert result['error'].startswith('invalid_circuit:')
    assert sentinel not in str(result)
    assert 'artifact' not in result


@pytest.mark.parametrize('suffix', [
    'qubit[1000000000] q; x q;',
    'qubit[13] q; qubit[14] r; x r;',
    'qubit q; bit[1000000000] c; c[0] = measure q;',
    'qubit q; bit[600] a; bit[401] b; a[0] = measure q;',
])
def test_qasm3_register_allocation_rejected_before_parser(monkeypatch, suffix):
    parser = Mock(side_effect=AssertionError('parser must not run'))
    monkeypatch.setattr(QasmParser, 'parse', parser)
    payload = request('')
    payload.update(input_format='qasm3',
        circuit='OPENQASM 3.0; include "stdgates.inc"; ' + suffix)
    with pytest.raises(ValueError, match='^resource_limit:'):
        prepare(payload)
    parser.assert_not_called()


@pytest.mark.parametrize('expression,binding', [('theta/0', 1.0), ('theta*theta', 1e200)])
def test_finite_binding_with_invalid_derived_angle_is_safely_rejected(capsys, expression, binding):
    import warnings

    payload = request('')
    payload.update(input_format='qasm3', bindings={'theta': binding},
        circuit='OPENQASM 3.0; include "stdgates.inc"; input float[64] theta; '
        f'qubit q; bit c; rx({expression}) q; c = measure q;')
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter('always')
        with pytest.raises(ValueError, match='^(invalid_circuit|invalid_binding):') as error:
            prepare(payload)
    assert not emitted
    assert expression not in str(error.value)
    assert not capsys.readouterr().err
