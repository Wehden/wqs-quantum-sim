"""Fixed remote worker. Circuit text is data, never executable Python or shell."""
import base64
import collections
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def simulate(source: Path, output: Path):
    # Bound native compiler/sampler allocations as well as Python allocations.
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2, 12 * 1024**2))
    request = json.loads(source.read_text())
    if request.get("engine", "clifft") == "qsim":
        if request.get('operation') == 'sample' or request.get('input_format') == 'qasm3':
            return simulate_qasm_sample(request, output)
        return simulate_qsim(request, output)
    import clifft
    import numpy as np
    started = time.monotonic()
    program = clifft.compile(request["circuit"], input_format=request["input_format"])
    if program.peak_active_width > 22:
        raise ValueError("Active width exceeds the CPU service limit of 22")
    if program.num_qubits > 4096:
        raise ValueError("Circuit exceeds the service limit of 4096 qubits")
    if request.get('operation','sample') == 'basis_probabilities':
        bitstrings=request['bitstrings']
        if not 1<=len(bitstrings)<=256 or any(len(value)!=program.num_qubits for value in bitstrings):
            raise ValueError('Provide 1–256 bitstrings, each matching the circuit qubit count')
        bit_order=request.get('bit_order','big')
        probabilities=clifft.basis_probabilities(program,bitstrings,bit_order=bit_order)
        buffer=io.BytesIO()
        np.savez_compressed(buffer,bitstrings=np.asarray(bitstrings),probabilities=probabilities)
        summary={'engine':f'clifft {clifft.__version__}','operation':'basis_probabilities',
                 'qubits':program.num_qubits,'bitstrings':bitstrings,'bit_order':bit_order,
                 'probabilities':probabilities.tolist(),'peak_active_width':program.peak_active_width,
                 'elapsed_seconds':round(time.monotonic()-started,6)}
        output.write_text(json.dumps({'status':'SUCCEEDED','summary':summary,'artifact':base64.b64encode(buffer.getvalue()).decode()}))
        return
    cells = request["shots"] * (program.num_measurements + program.num_detectors + program.num_observables + program.num_exp_vals)
    if cells > 1_000_000:
        raise ValueError("Output exceeds 1,000,000 cells; reduce shots or circuit measurements")
    if program.has_postselection:
        raise ValueError("Post-selection is not supported by this fixed-shot service")
    result = clifft.sample(program, shots=request["shots"], seed=request["seed"], threads=1)
    arrays = {name: getattr(result, name) for name in ("measurements", "detectors", "observables", "exp_vals")}
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    counts = collections.Counter("".join(map(str, row)) for row in result.measurements)
    summary = {
        "engine": f"clifft {clifft.__version__}", "operation": "sample", "shots": request["shots"], "seed": request["seed"],
        "qubits": program.num_qubits, "measurements": program.num_measurements,
        "detectors": program.num_detectors, "observables": program.num_observables,
        "peak_active_width": program.peak_active_width,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "counts": dict(counts.most_common(256)), "counts_truncated": len(counts) > 256,
        "preview": result.measurements[:20].tolist(),
    }
    output.write_text(json.dumps({"status": "SUCCEEDED", "summary": summary, "artifact": base64.b64encode(buffer.getvalue()).decode()}))


def simulate_qsim(request, output):
    """Translate data-only QASM to Cirq and run Google's CPU state-vector engine."""
    import re
    import importlib.metadata
    started = time.monotonic()
    if request['input_format'] != 'qasm2' or request['operation'] != 'basis_probabilities':
        raise ValueError('qsim supports OpenQASM 2 basis probabilities')
    text = re.sub(r'//[^\n]*', '', request['circuit'])
    if not re.match(r'^\s*OPENQASM\s+2\.0\s*;', text):
        raise ValueError('Expected OpenQASM 2.0')
    if re.search(r'\b(creg|measure|reset|if|gate|opaque)\b', text):
        raise ValueError('Only unitary QASM without classical registers or custom gates is supported')
    declared = re.findall(r'\bqreg\s+[a-zA-Z_]\w*\s*\[\s*(\d+)\s*\]\s*;', text)
    if not declared or not 1 <= sum(map(int, declared)) <= 26:
        raise ValueError('qsim supports 1–26 declared qubits on this CPU profile')
    import cirq
    import qsimcirq
    import numpy as np
    from cirq.contrib.qasm_import._parser import QasmParser
    parsed = QasmParser().parse(text)
    # Explicit numeric register order includes idle qubits and avoids lexicographic q_10 ordering.
    qubits = [cirq.NamedQubit(f'{name}_{index}') for name, size in parsed.qregs.items() for index in range(size)]
    if not 1 <= len(qubits) <= 26 or parsed.cregs:
        raise ValueError('Invalid register declarations')
    circuit = parsed.circuit
    if not all(cirq.has_unitary(op) for op in circuit.all_operations()):
        raise ValueError('Only unitary operations are supported')
    circuit += cirq.Circuit(cirq.I(q) for q in qubits)
    bitstrings = request['bitstrings']
    if not 1 <= len(bitstrings) <= 256 or any(len(bits) != len(qubits) or set(bits)-{'0','1'} for bits in bitstrings):
        raise ValueError('Bitstrings must match the declared qubit count')
    order = request.get('bit_order', 'big')
    indices = [int(bits if order == 'big' else bits[::-1], 2) for bits in bitstrings]
    simulator = qsimcirq.QSimSimulator(qsim_options=qsimcirq.QSimOptions(cpu_threads=2))
    amplitudes = simulator.compute_amplitudes(circuit, indices, qubit_order=qubits)
    probabilities = np.abs(np.asarray(amplitudes, dtype=np.complex128)) ** 2
    buffer = io.BytesIO()
    np.savez_compressed(buffer, bitstrings=np.asarray(bitstrings), probabilities=probabilities)
    summary = {'engine': 'qsim ' + importlib.metadata.version('qsimcirq'),
               'operation':'basis_probabilities', 'qubits':len(qubits), 'bitstrings':bitstrings,
               'bit_order':order, 'probabilities':probabilities.tolist(), 'precision':'single',
               'elapsed_seconds':round(time.monotonic()-started,6)}
    output.write_text(json.dumps({'status':'SUCCEEDED','summary':summary,
                                 'artifact':base64.b64encode(buffer.getvalue()).decode()}))


def qasm_sample_preview(memory, counts):
    """Keep complete rows/keys within separate JSON budgets; artifacts stay complete."""
    budget = 512 * 1024
    selected = {}
    used = 2
    for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:256]:
        cost = len(json.dumps(key).encode()) + len(str(count)) + 4
        if used + cost > budget:
            break
        selected[key] = count
        used += cost
    preview = []
    used = 2
    for row in memory[:20]:
        value = row.tolist()
        cost = len(json.dumps(value).encode()) + 2
        if used + cost > budget:
            break
        preview.append(value)
        used += cost
    return dict(counts=selected, counts_truncated=len(selected) < len(counts),
                preview=preview, preview_truncated=len(preview) < len(memory))


def simulate_qasm_sample(request, output):
    """Run the verified QASM subset and produce a complete result artifact."""
    from qsim.qasm_adapter import SUPPORTED_COMBINATIONS, final_memory, prepare
    started = time.monotonic()
    try:
        import numpy as np
        import qsimcirq
        if request.get('operation') not in SUPPORTED_COMBINATIONS.get(
                request.get('engine'), {}).get(request.get('input_format'), ()):
            raise ValueError('unsupported_feature: engine or input format')
        circuit, metadata = prepare(request)
        compiled = time.monotonic()
        seed = request.get('seed', 42)
        # Preserve native small-seed sequences and retain both words of wider API seeds.
        native_seed = seed if seed < 2**32 else np.random.RandomState([seed & 0xffffffff, seed >> 32])
        simulator = qsimcirq.QSimSimulator(seed=native_seed,
            qsim_options=qsimcirq.QSimOptions(cpu_threads=2))
        if request['operation'] == 'basis_probabilities':
            import cirq
            qubits = metadata['qubits']
            bitstrings = request['bitstrings']
            if not 1 <= len(bitstrings) <= 256 or any(len(bits) != len(qubits) or set(bits)-{'0','1'} for bits in bitstrings):
                raise ValueError('invalid_circuit: bitstrings must match declared qubits')
            order = request.get('bit_order', 'big')
            if order not in ('big', 'little'):
                raise ValueError('invalid_circuit: bit order')
            circuit += cirq.Circuit(cirq.I(q) for q in qubits)
            indices = [int(bits if order == 'big' else bits[::-1], 2) for bits in bitstrings]
            amplitudes = simulator.compute_amplitudes(circuit, indices, qubit_order=qubits)
            probabilities = np.abs(np.asarray(amplitudes, dtype=np.complex128)) ** 2
            if probabilities.shape != (len(bitstrings),) or not np.all(np.isfinite(probabilities)):
                raise ValueError('execution_failed: incomplete probability result')
            buffer = io.BytesIO()
            np.savez_compressed(buffer, bitstrings=np.asarray(bitstrings), probabilities=probabilities)
            summary = {'engine': 'qsim ' + metadata['engine_version'],
                'importer': 'cirq ' + metadata['importer_version'], 'input_format': request['input_format'],
                'operation': 'basis_probabilities', 'qubits': len(qubits), 'bitstrings': bitstrings,
                'bit_order': order, 'probabilities': probabilities.tolist(), 'precision': 'single',
                'compile_seconds': round(compiled-started, 6),
                'execution_seconds': round(time.monotonic()-compiled, 6),
                'elapsed_seconds': round(time.monotonic()-started, 6)}
        else:
            result = simulator.run(circuit, repetitions=request['shots'])
            executed = time.monotonic()
            memory = final_memory(result, metadata, request['shots'])
            if memory.dtype != np.uint8 or memory.shape != (request['shots'], metadata['classical_bits']):
                raise ValueError('execution_failed: classical memory shape')
            counts = collections.Counter(''.join(map(str, row)) for row in memory)
            buffer = io.BytesIO()
            np.savez_compressed(buffer, classical_memory=memory)
            summary = {'result_schema': 'qasm-samples/1',
                'engine': 'qsim ' + metadata['engine_version'],
                'importer': 'cirq ' + metadata['importer_version'],
                'input_format': request['input_format'], 'operation': 'sample',
                'shots': request['shots'], 'seed': request['seed'], 'qubits': len(metadata['qubits']),
                'registers': metadata['registers'],
                **qasm_sample_preview(memory, counts),
                'compile_seconds': round(compiled - started, 6),
                'execution_seconds': round(executed - compiled, 6),
                'elapsed_seconds': round(time.monotonic() - started, 6)}
        if len(json.dumps(summary).encode()) > 2 * 1024 * 1024:
            raise ValueError('resource_limit: result metadata exceeds callback limit')
        payload = {'status': 'SUCCEEDED', 'summary': summary,
                   'artifact': base64.b64encode(buffer.getvalue()).decode()}
    except Exception as error:
        category = str(error).partition(':')[0] if isinstance(error, ValueError) else 'execution_failed'
        messages = {
            'invalid_circuit': 'check the documented QASM subset',
            'unsupported_feature': 'use supported QASM operations',
            'invalid_binding': 'parameters must be resolved finite numbers',
            'resource_limit': 'reduce circuit or sample output size',
            'execution_failed': 'simulation did not produce a complete result',
        }
        if category not in messages:
            category = 'execution_failed'
        detail = {
            'unsupported_feature: custom gate declarations': 'expand custom gates with your circuit library before submission',
            'unsupported_feature: conditions or exponent expressions': 'remove classical conditions; evaluate exponent expressions in the client',
            'unsupported_feature: engine or input format': 'select engine qsim and a documented QASM format and operation',
            'invalid_circuit: sampling requires measurement': 'add a measurement into a declared classical register',
        }.get(str(error), messages[category])
        payload = {'status': 'FAILED', 'error': category + ': ' + detail}
    output.write_text(json.dumps(payload))


def remote():
    url = os.environ["QSIM_JOB_URL"]
    headers = {"Authorization": "Bearer " + os.environ["QSIM_JOB_TOKEN"], "Content-Type": "application/json"}

    def call(payload=None):
        encoded = None if payload is None else json.dumps(payload).encode()
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, data=encoded, headers=headers)
                with urllib.request.urlopen(req, timeout=20) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code < 500:
                    raise
            except (OSError, TimeoutError):
                pass
            time.sleep(min(2**attempt, 10))
        raise RuntimeError("Result service unreachable")

    request = call()
    call({"status": "RUNNING"})
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / "input.json", Path(directory) / "output.json"
        source.write_text(json.dumps(request))
        # Do not give the native simulator job credentials or controller environment.
        env = {"PATH": os.environ["PATH"], "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
        process = subprocess.run([sys.executable, "-m", "qsim.worker", "simulate", str(source), str(output)],
                                 cwd=Path(__file__).resolve().parent.parent,
                                 env=env, capture_output=True)
        if process.returncode:
            message = "Circuit simulation failed; check the circuit and service limits"
            if request['input_format'] in ('qasm2', 'qasm3') and request.get('operation') == 'sample':
                version = '2' if request['input_format'] == 'qasm2' else '3'
                message = f"execution_failed: OpenQASM {version} sampling failed; check the supported sampling circuit syntax and service limits, including sample output size."
                if request['input_format'] == 'qasm3':
                    message += " Check declared input bindings."
            elif request['input_format'] == 'qasm2':
                message = "OpenQASM 2 simulation failed; use unitary qelib1.inc circuits without classical registers, measurement, reset or conditions, and bitstrings matching the qubit count. Check service limits."
            elif request['input_format'] == 'qasm3':
                message = "execution_failed: OpenQASM 3 probability simulation failed; check the supported unitary circuit syntax, input bindings, bitstrings matching the declared qubit count, and service limits."
            payload = {"status": "FAILED", "error": message}
        else:
            payload = json.loads(output.read_text())
        call(payload)
    return 0 if payload["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "simulate":
        simulate(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit(remote())
