"""Bounded, data-only QASM import for the explicitly selected Aer engine.

Native dependencies are imported only inside isolated-child functions. This
module validates structure; Qiskit/Aer owns quantum and classical execution.
"""

# Explicit standard gate subset. Values are (quantum operands, numeric arguments).
_GATES = {name: (1, 0) for name in ('id', 'x', 'y', 'z', 'h', 's', 'sdg', 't', 'tdg', 'sx', 'sxdg')}
_GATES.update({name: (1, 1) for name in ('rx', 'ry', 'rz', 'p', 'u1')})
_GATES.update({'u2': (1, 2), 'u3': (1, 3), 'U': (1, 3), 'u': (1, 3)})
_GATES.update({name: (2, 0) for name in ('cx', 'CX', 'cy', 'cz', 'ch', 'swap')})
_GATES.update({name: (2, 1) for name in ('cp', 'crx', 'cry', 'crz', 'rxx', 'ryy', 'rzz')})
_GATES.update({'ccx': (3, 0), 'cswap': (3, 0)})


def _tokens(source):
    """Bound numbers and nesting before the maintained parser constructs ASTs."""
    from antlr4 import InputStream
    from antlr4.error.ErrorListener import ErrorListener
    from openqasm3.parser import qasm3Lexer

    class RejectErrors(ErrorListener):
        def syntaxError(self, *args):
            raise ValueError('invalid_circuit: token syntax')

    lexer = qasm3Lexer(InputStream(source))
    lexer.removeErrorListeners()
    lexer.addErrorListener(RejectErrors())
    depth = 0
    for token in lexer.getAllTokens():
        kind = lexer.symbolicNames[token.type]
        if 'Literal' in kind and kind not in ('StringLiteral', 'BooleanLiteral'):
            if len(token.text) > 64:
                raise ValueError('resource_limit: numeric literal length')
        if kind in ('LPAREN', 'LBRACKET', 'LBRACE'):
            depth += 1
            if depth > 32:
                raise ValueError('resource_limit: source nesting')
        elif kind in ('RPAREN', 'RBRACKET', 'RBRACE'):
            depth -= 1
            if depth < 0:
                raise ValueError('invalid_circuit: source nesting')
    if depth:
        raise ValueError('invalid_circuit: source nesting')


def _number(node, depth=0):
    """Validate a small constant-expression AST; never evaluate it here."""
    import math
    from openqasm3 import ast
    if depth > 32:
        raise ValueError('resource_limit: expression depth')
    if isinstance(node, (ast.IntegerLiteral, ast.FloatLiteral)):
        if not math.isfinite(node.value) or abs(node.value) > 1e100:
            raise ValueError('invalid_circuit: numeric literal')
    elif isinstance(node, ast.Identifier) and node.name in ('pi', 'tau', 'e'):
        pass
    elif isinstance(node, ast.UnaryExpression) and node.op.name in ('-', '+'):
        _number(node.expression, depth + 1)
    elif isinstance(node, ast.BinaryExpression) and node.op.name in ('+', '-', '*', '/'):
        _number(node.lhs, depth + 1)
        _number(node.rhs, depth + 1)
    else:
        raise ValueError('unsupported_feature: numeric expression')


def _operand(node, declarations):
    from openqasm3 import ast
    if isinstance(node, ast.Identifier):
        if node.name not in declarations:
            raise ValueError('invalid_circuit: undeclared operand')
        return declarations[node.name]
    if isinstance(node, ast.IndexedIdentifier) and node.name.name in declarations:
        if len(node.indices) == 1 and isinstance(node.indices[0], list) and len(node.indices[0]) == 1:
            index = node.indices[0][0]
            if isinstance(index, ast.IntegerLiteral) and 0 <= index.value < declarations[node.name.name]:
                return 1
    raise ValueError('unsupported_feature: indexed operand')


def _condition(node, classical, scalar, fmt):
    from openqasm3 import ast
    if not isinstance(node, ast.BinaryExpression) or node.op.name != '==':
        raise ValueError('unsupported_feature: condition equality')
    if not isinstance(node.lhs, ast.Identifier) or node.lhs.name not in classical:
        raise ValueError('unsupported_feature: whole-register condition')
    name = node.lhs.name
    if fmt == 'qasm3' and name in scalar:
        if not isinstance(node.rhs, ast.BooleanLiteral):
            raise ValueError('unsupported_feature: compare scalar bit to true or false')
    elif not isinstance(node.rhs, ast.IntegerLiteral) or not 0 <= node.rhs.value < (1 << classical[name]):
        raise ValueError('invalid_circuit: condition value outside register')


def _body(statements, quantum, classical, scalar, fmt, *, branch=False):
    from openqasm3 import ast
    operations = measurements = 0
    for node in statements:
        if getattr(node, 'annotations', []):
            raise ValueError('unsupported_feature: annotations')
        if isinstance(node, ast.QuantumGate):
            contract = _GATES.get(node.name.name)
            if contract is None or node.modifiers or node.duration is not None:
                raise ValueError('unsupported_feature: standard gate')
            if (len(node.qubits), len(node.arguments)) != contract:
                raise ValueError('invalid_circuit: gate arity')
            widths = [_operand(q, quantum) for q in node.qubits]
            width = max(widths)
            if any(w not in (1, width) for w in widths):
                raise ValueError('invalid_circuit: gate broadcast')
            for argument in node.arguments:
                _number(argument)
            operations += width
        elif isinstance(node, ast.QuantumMeasurementStatement):
            if branch and fmt == 'qasm2':
                raise ValueError('unsupported_feature: QASM2 conditional measurement')
            width = _operand(node.measure.qubit, quantum)
            if node.target is None or width != _operand(node.target, classical):
                raise ValueError('invalid_circuit: measurement destination')
            operations += width
            measurements += width
        elif isinstance(node, ast.QuantumReset):
            if branch and fmt == 'qasm2':
                raise ValueError('unsupported_feature: QASM2 conditional reset')
            operations += _operand(node.qubits, quantum)
        elif isinstance(node, ast.BranchingStatement) and not branch:
            _condition(node.condition, classical, scalar, fmt)
            if fmt == 'qasm2' and (len(node.if_block) != 1 or node.else_block):
                raise ValueError('unsupported_feature: QASM2 branch body')
            for block in (node.if_block, node.else_block):
                count, events = _body(block, quantum, classical, scalar, fmt, branch=True)
                operations += count
                measurements += events
        else:
            raise ValueError('unsupported_feature: statement')
        if operations > 65536:
            raise ValueError('resource_limit: operation count')
    return operations, measurements


def _preflight(request):
    import contextlib
    import io
    import openqasm3
    from openqasm3 import ast
    source = request.get('circuit')
    fmt = request.get('input_format')
    shots = request.get('shots', 1000)
    if fmt not in ('qasm2', 'qasm3') or request.get('operation') != 'sample' or request.get('engine') != 'aer':
        raise ValueError('unsupported_feature: Aer sample combination')
    if request.get('bindings'):
        raise ValueError('unsupported_feature: Aer bindings')
    if type(shots) is not int or not 1 <= shots <= 10000:
        raise ValueError('resource_limit: shots')
    seed = request.get('seed', 42)
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError('invalid_circuit: seed')
    if not isinstance(source, str) or not 1 <= len(source) <= 65536:
        raise ValueError('resource_limit: source length')
    _tokens(source)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            program = openqasm3.parse(source)
    except Exception:
        raise ValueError('invalid_circuit: QASM syntax') from None
    if program.version != ('2.0' if fmt == 'qasm2' else '3.0'):
        raise ValueError('invalid_circuit: QASM version')
    quantum, classical, scalar, registers, body = {}, {}, set(), [], []
    quantum_bits = classical_bits = 0
    seen_body = False
    for node in program.statements:
        if getattr(node, 'annotations', []):
            raise ValueError('unsupported_feature: annotations')
        if isinstance(node, ast.Include):
            if seen_body or node.filename != ('qelib1.inc' if fmt == 'qasm2' else 'stdgates.inc'):
                raise ValueError('unsupported_feature: include')
        elif isinstance(node, (ast.QubitDeclaration, ast.ClassicalDeclaration)):
            if seen_body:
                raise ValueError('unsupported_feature: declarations after operations')
            is_quantum = isinstance(node, ast.QubitDeclaration)
            if not is_quantum and (not isinstance(node.type, ast.BitType) or node.init_expression is not None):
                raise ValueError('unsupported_feature: classical declaration')
            size = node.size if is_quantum else node.type.size
            if size is not None and not isinstance(size, ast.IntegerLiteral):
                raise ValueError('unsupported_feature: literal register size required')
            width = 1 if size is None else size.value
            name = node.qubit.name if is_quantum else node.identifier.name
            if width < 1 or name in quantum or name in classical:
                raise ValueError('invalid_circuit: register declaration')
            if is_quantum:
                quantum_bits += width
                quantum[name] = width
            else:
                registers.append({'name': name, 'size': width, 'offset': classical_bits})
                classical_bits += width
                classical[name] = width
                if size is None:
                    scalar.add(name)
            if quantum_bits > 26 or classical_bits * shots > 1_000_000:
                raise ValueError('resource_limit: register allocation')
        else:
            seen_body = True
            body.append(node)
    if quantum_bits < 1 or classical_bits < 1:
        raise ValueError('invalid_circuit: quantum and classical declarations required')
    operations, events = _body(body, quantum, classical, scalar, fmt)
    if not events:
        raise ValueError('invalid_circuit: sampling requires measurement')
    if (classical_bits + events) * shots > 1_000_000:
        raise ValueError('resource_limit: sample cells')
    return program, {'registers': registers, 'classical_bits': classical_bits,
                     'quantum_bits': quantum_bits, 'scalar': scalar, 'operations': operations}


def _validate_native(circuit, *, branch=False):
    import math
    operations = events = 0
    for instruction in circuit.data:
        op = instruction.operation
        if op.name == 'if_else' and not branch:
            for block in op.blocks:
                count, measured = _validate_native(block, branch=True)
                operations += count
                events += measured
        elif op.name in _GATES or op.name in ('measure', 'reset'):
            for parameter in op.params:
                if not math.isfinite(float(parameter)):
                    raise ValueError('invalid_circuit: nonfinite gate parameter')
            operations += 1
            events += op.name == 'measure'
        else:
            raise ValueError('unsupported_feature: native instruction')
        if operations > 65536:
            raise ValueError('resource_limit: native operation count')
    return operations, events


def _prepare(request):
    import contextlib
    import io
    import numpy as np
    from qiskit import qasm2
    from qiskit_qasm3_import import convert
    program, metadata = _preflight(request)
    try:
        with contextlib.redirect_stderr(io.StringIO()), np.errstate(all='raise'):
            circuit = (qasm2.loads(request['circuit'], include_path=())
                       if request['input_format'] == 'qasm2' else convert(program))
    except Exception:
        raise ValueError('invalid_circuit: native QASM import') from None
    if circuit.num_qubits != metadata['quantum_bits'] or circuit.num_clbits != metadata['classical_bits']:
        raise ValueError('invalid_circuit: native register shape')
    # The maintained importer emits scalar bits in declaration order. Confirm all
    # register-backed bits and require scalar slots to be unaliased standalone bits.
    registers = {r.name: r for r in circuit.cregs}
    for register in metadata['registers']:
        for i in range(register['size']):
            bit = circuit.clbits[register['offset'] + i]
            location = circuit.find_bit(bit)
            if register['name'] in metadata['scalar']:
                if location.registers:
                    raise ValueError('invalid_circuit: scalar mapping')
            else:
                native = registers.get(register['name'])
                if native is None or len(native) != register['size'] or native[i] != bit:
                    raise ValueError('invalid_circuit: register mapping')
            if location.index != register['offset'] + i:
                raise ValueError('invalid_circuit: classical ordering')
    _, events = _validate_native(circuit)
    if (circuit.num_clbits + events) * request['shots'] > 1_000_000:
        raise ValueError('resource_limit: native sample cells')
    return circuit, metadata
