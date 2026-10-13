"""Pinned native QASM import inside the credential-free simulation child."""

# Admission remains disabled until API and result integration are verified together.
SUPPORTED_COMBINATIONS = {}


def _preflight(source, shots):
    import math
    from cirq.contrib.qasm_import._lexer import QasmLexer

    if not isinstance(source, str) or len(source) > 65536:
        raise ValueError('resource_limit: circuit size')
    lexer = QasmLexer()
    lexer.input(source)
    tokens = list(iter(lexer.token, None))
    quantum = classical = 0
    for i, token in enumerate(tokens):
        if token.type in ('IF', '^'):
            raise ValueError('unsupported_feature: conditions or exponent expressions')
        if token.type in ('QELIBINC', 'STDGATESINC'):
            name = token.value.split('"')[1]
            if name not in ('qelib1.inc', 'stdgates.inc'):
                raise ValueError('unsupported_feature: include name')
        if token.type in ('NUMBER', 'NATURAL_NUMBER'):
            if not math.isfinite(token.value) or abs(token.value) > 1e100:
                raise ValueError('invalid_circuit: numeric literal')
        if token.type not in ('QREG', 'CREG', 'QUBIT', 'BIT'):
            continue
        tail = tokens[i + 1:i + 6]
        if token.type in ('QREG', 'CREG'):
            if [t.type for t in tail] != ['ID', '[', 'NATURAL_NUMBER', ']', ';']:
                raise ValueError('invalid_circuit: register declaration')
            size = tail[2].value
        elif len(tail) >= 2 and [t.type for t in tail[:2]] == ['ID', ';']:
            size = 1
        elif [t.type for t in tail] == ['[', 'NATURAL_NUMBER', ']', 'ID', ';']:
            size = tail[1].value
        else:
            raise ValueError('invalid_circuit: register declaration')
        if size < 1:
            raise ValueError('invalid_circuit: empty register')
        if token.type in ('QREG', 'QUBIT'):
            quantum += size
        else:
            classical += size
        if quantum > 26 or classical * shots > 1_000_000:
            raise ValueError('resource_limit: register allocation')
    if not 1 <= quantum <= 26:
        raise ValueError('resource_limit: declared qubits')
    return quantum, classical


def _check_expansion(circuit, cache=None, depth=0):
    import cirq
    import numpy as np
    from qsimcirq.qsim_circuit import _has_cirq_gate_kind

    if depth > 32:
        raise ValueError('resource_limit: gate nesting')
    cache = {} if cache is None else cache
    if id(circuit) in cache:
        count, height = cache[id(circuit)]
        if depth + height > 32:
            raise ValueError('resource_limit: gate nesting')
        return count, height
    count = height = 0
    for op in circuit.all_operations():
        if isinstance(op, cirq.CircuitOperation):
            if op.repetitions != 1:
                raise ValueError('unsupported_feature: gate repetitions')
            child_count, child_height = _check_expansion(op.circuit, cache, depth + 1)
            count += child_count
            height = max(height, child_height + 1)
        else:
            if isinstance(op, cirq.ClassicallyControlledOperation):
                raise ValueError('unsupported_feature: classical conditions')
            if len(op.qubits) > 5:
                raise ValueError('unsupported_feature: gate width')
            if cirq.is_parameterized(op):
                raise ValueError('invalid_binding: unresolved parameter')
            # Builtin decompositions are bounded independently of custom graphs.
            if isinstance(op.gate, (cirq.MeasurementGate, cirq.ResetChannel)):
                count += 1
            else:
                matrix = cirq.unitary(op, default=None)
                if matrix is None or not np.all(np.isfinite(matrix)):
                    raise ValueError('invalid_circuit: nonfinite or unsupported gate')
                count += len(cirq.decompose(op, keep=_has_cirq_gate_kind))
        if count > 65536:
            raise ValueError('resource_limit: expanded operations')
    cache[id(circuit)] = count, height
    return count, height


def prepare(request):
    """Return the native circuit and explicit register metadata, without execution."""
    import importlib.metadata
    import cirq
    from cirq.contrib.qasm_import._parser import QasmParser

    try:
        if request.get('engine') != 'qsim' or request.get('input_format') != 'qasm2':
            raise ValueError('unsupported_feature: engine or input format')
        shots = request.get('shots', 1)
        if type(shots) is not int or not 1 <= shots <= 10000:
            raise ValueError('resource_limit: shots')
        quantum, classical = _preflight(request['circuit'], shots)
        parsed = QasmParser().parse(request['circuit'])
        if parsed.input_params or request.get('bindings'):
            raise ValueError('unsupported_feature: parameter bindings')
        if not request['circuit'].lstrip().startswith('OPENQASM 2.0;'):
            # The lexer validates grammar; format must also match the envelope.
            from cirq.contrib.qasm_import._lexer import QasmLexer
            lexer = QasmLexer()
            lexer.input(request['circuit'])
            first = lexer.token()
            if first is None or first.type != 'FORMAT_SPEC' or first.value != '2.0':
                raise ValueError('invalid_circuit: format mismatch')
        _check_expansion(parsed.circuit)
        qubits = [cirq.NamedQubit(f'{name}_{i}') for name, size in parsed.qregs.items() for i in range(size)]
        registers, offset = [], 0
        for name, size in parsed.cregs.items():
            registers.append(dict(name=name, size=size, offset=offset))
            offset += size
        if len(qubits) != quantum or offset != classical:
            raise ValueError('invalid_circuit: register metadata')
        events = {}
        nonunitary = False
        for op in parsed.circuit.all_operations():
            if cirq.is_measurement(op):
                key = cirq.measurement_key_name(op)
                events[key] = events.get(key, 0) + 1
            if not cirq.has_unitary(op):
                nonunitary = True
        if (classical + sum(events.values())) * shots > 1_000_000:
            raise ValueError('resource_limit: classical memory and measurement records')
        operation = request.get('operation', 'basis_probabilities')
        if operation == 'basis_probabilities' and nonunitary:
            raise ValueError('unsupported_feature: nonunitary probabilities')
        if operation == 'sample' and not events:
            raise ValueError('invalid_circuit: sampling requires measurement')
        if operation not in ('sample', 'basis_probabilities'):
            raise ValueError('unsupported_feature: operation')
        return parsed.circuit, dict(registers=registers, qubits=qubits, events=events,
            classical_bits=classical, input_format='qasm2',
            engine_version=importlib.metadata.version('qsimcirq'),
            importer_version=importlib.metadata.version('cirq-core'))
    except Exception as error:
        message = str(error)
        if isinstance(error, ValueError) and message.startswith((
                'resource_limit:', 'unsupported_feature:', 'invalid_circuit:', 'invalid_binding:')):
            raise ValueError(message) from None
        raise ValueError('invalid_circuit: unsupported or malformed QASM') from None


def final_memory(native_result, metadata, shots):
    """Map native measurement events to complete final classical memory."""
    import numpy as np

    memory = np.zeros((shots, metadata['classical_bits']), dtype=np.uint8)
    records = native_result.records
    if set(records) != set(metadata['events']):
        raise ValueError('execution_failed: measurement record keys')
    for register in metadata['registers']:
        for index in range(register['size']):
            key = f"{register['name']}_{index}"
            if key not in records:
                continue
            values = np.asarray(records[key])
            if values.shape != (shots, metadata['events'][key], 1) or not np.all((values == 0) | (values == 1)):
                raise ValueError('execution_failed: incomplete measurement records')
            memory[:, register['offset'] + index] = values[:, -1, 0]
    return memory
