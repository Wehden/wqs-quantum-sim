"""Pinned upstream capability probes, not claims about service admission.

Positive tests execute the selected native engine. Negative tests capture an
explicit unsupported boundary; none are xfailed or silently skipped. Fixtures
cover requirements REQ-001 and REQ-003 through REQ-011 (validation V01/V03–V05).
"""

import math
from importlib.metadata import version

import cirq
import clifft
import numpy as np
import pytest
import qsimcirq
from cirq.contrib.qasm_import._parser import QasmParser
from cirq.contrib.qasm_import.exception import QasmException

QASM2 = 'OPENQASM 2.0; include "qelib1.inc"; '
QASM3 = 'OPENQASM 3.0; include "stdgates.inc"; '
SHOTS = 64
SEED = 42


def test_probe_dependency_versions():
    assert version('clifft') == '0.11.0'
    assert version('qsimcirq') == '0.22.1'
    assert version('cirq-core') == '1.7.0'


@pytest.mark.parametrize('source', [
    QASM2 + 'qreg q[2]; creg c[2]; x q[0]; measure q -> c;',
    QASM3 + 'qubit[2] q; bit[2] c; x q[0]; c = measure q;',
])
def test_qsim_terminal_sampling_asymmetric(source):
    parsed = QasmParser().parse(source)
    result = qsimcirq.QSimSimulator(seed=SEED).run(parsed.circuit, repetitions=SHOTS)
    assert np.array_equal(result.records['c_0'], np.ones((SHOTS, 1, 1)))
    assert np.array_equal(result.records['c_1'], np.zeros((SHOTS, 1, 1)))


def test_qsim_mid_measurement_collapses_and_preserves_correlation():
    parsed = QasmParser().parse(QASM2 +
        'qreg q[2]; creg c[2]; h q[0]; measure q[0] -> c[0]; '
        'cx q[0],q[1]; measure q[1] -> c[1];')
    result = qsimcirq.QSimSimulator(seed=SEED).run(parsed.circuit, repetitions=SHOTS)
    assert np.array_equal(result.records['c_0'], result.records['c_1'])
    # Seeded coverage of both outcomes; not a distribution/tolerance claim.
    assert set(result.records['c_0'].ravel()) == {0, 1}


def test_qsim_entangled_reset_preserves_partner_and_starts_fresh_shots():
    parsed = QasmParser().parse(QASM2 +
        'qreg q[2]; creg c[2]; creg before[1]; h q[0]; cx q[0],q[1]; '
        'measure q[1] -> before[0]; reset q[0]; measure q -> c;')
    result = qsimcirq.QSimSimulator(seed=SEED).run(parsed.circuit, repetitions=SHOTS)
    assert np.array_equal(result.records['c_0'], np.zeros((SHOTS, 1, 1)))
    assert np.array_equal(result.records['c_1'], result.records['before_0'])
    assert set(result.records['c_1'].ravel()) == {0, 1}


def test_qsim_repeated_write_records_are_events_not_final_memory():
    parsed = QasmParser().parse(QASM2 +
        'qreg q[1]; creg c[1]; x q[0]; measure q[0] -> c[0]; '
        'x q[0]; measure q[0] -> c[0];')
    result = qsimcirq.QSimSimulator(seed=SEED).run(parsed.circuit, repetitions=SHOTS)
    assert result.records['c_0'].shape == (SHOTS, 2, 1)
    assert np.all(result.records['c_0'][:, 0, 0] == 1)
    assert np.all(result.records['c_0'][:, -1, 0] == 0)


def test_importer_preserves_register_order_unused_bits_and_numeric_indices():
    parsed = QasmParser().parse(QASM2 +
        'qreg q[2]; creg z[12]; creg a[2]; x q[0]; '
        'measure q[0] -> z[10]; measure q[1] -> a[1];')
    assert list(parsed.cregs.items()) == [('z', 12), ('a', 2)]
    result = qsimcirq.QSimSimulator(seed=SEED).run(parsed.circuit, repetitions=SHOTS)
    assert set(result.records) == {'z_10', 'a_1'}
    assert np.all(result.records['z_10'] == 1)
    assert np.all(result.records['a_1'] == 0)
    # Unwritten bits have no native record: an adapter must initialize them.


@pytest.mark.parametrize('value', [0, 1])
def test_qsim_conditions_parse_but_execution_rejects(value):
    parsed = QasmParser().parse(QASM2 +
        'qreg q[2]; creg c[1]; x q[0]; measure q[0] -> c[0]; '
        f'if(c=={value}) x q[1];')
    assert any(isinstance(op, cirq.ClassicallyControlledOperation)
               for op in parsed.circuit.all_operations())
    with pytest.raises(ValueError, match='Encountered unparseable op'):
        qsimcirq.QSimSimulator(seed=SEED).run(parsed.circuit, repetitions=SHOTS)


@pytest.mark.parametrize('prefix,register', [
    (QASM2, 'qreg q[1];'), (QASM3, 'qubit[1] q;'),
])
def test_qsim_nested_parameterized_custom_gate_probabilities(prefix, register):
    parsed = QasmParser().parse(prefix +
        'gate turn(a) t { rx(a) t; } gate nested(b) t { turn(b) t; } '
        + register + ' nested(pi/3) q[0];')
    amplitudes = qsimcirq.QSimSimulator().compute_amplitudes(parsed.circuit, [0, 1])
    assert np.abs(amplitudes) ** 2 == pytest.approx([.75, .25], abs=1e-6)


@pytest.mark.parametrize('kind', ['float[64]', 'angle[32]'])
@pytest.mark.parametrize('theta,expected', [(0.0, 0), (math.pi, 1)])
def test_qsim_qasm3_sized_inputs_bind_through_native_resolver(kind, theta, expected):
    parsed = QasmParser().parse(QASM3 +
        f'input {kind} theta; qubit[1] q; bit[1] c; rx(theta) q[0]; c=measure q;')
    assert parsed.input_params == {'theta': kind}
    resolved = cirq.resolve_parameters(parsed.circuit, {'theta': theta})
    assert not cirq.is_parameterized(resolved)
    result = qsimcirq.QSimSimulator(seed=SEED).run(resolved, repetitions=SHOTS)
    assert np.all(result.records['c_0'] == expected)


@pytest.mark.parametrize('kind', ['float', 'angle'])
def test_cirq_unsized_qasm3_inputs_are_not_accepted(kind):
    with pytest.raises(QasmException, match='Syntax error'):
        QasmParser().parse(QASM3 + f'input {kind} theta; qubit[1] q; rx(theta) q[0];')


def test_qsim_unresolved_input_fails_execution():
    parsed = QasmParser().parse(QASM3 +
        'input float[64] theta; qubit[1] q; bit[1] c; rx(theta) q[0]; c=measure q;')
    with pytest.raises(ValueError, match='Parameters must be numeric'):
        qsimcirq.QSimSimulator().run(parsed.circuit, repetitions=1)


@pytest.mark.parametrize('statement', [
    'creg c[1];', 'measure q[0];', 'reset q[0];',
    'creg c[1]; if(c==1) x q[0];',
])
def test_clifft_qasm2_classical_or_nonunitary_statements_rejected(statement):
    with pytest.raises(clifft.ParseError, match='Non-unitary and classical statements'):
        clifft.compile(QASM2 + 'qreg q[1]; ' + statement, input_format='qasm2')


def test_clifft_custom_gate_declaration_rejected():
    with pytest.raises(clifft.ParseError, match='Custom gate and opaque declarations'):
        clifft.compile(QASM2 + 'gate f(a) t { rx(a) t; } qreg q[1]; f(pi) q[0];',
                       input_format='qasm2')


def test_clifft_qasm3_not_an_available_import_format():
    with pytest.raises(ValueError, match='input_format'):
        clifft.compile(QASM3 + 'qubit[1] q; x q[0];', input_format='qasm3')
    with pytest.raises(clifft.ParseError, match='OpenQASM 3 is not supported'):
        clifft.compile(QASM3 + 'qubit[1] q; x q[0];', input_format='qasm2')
