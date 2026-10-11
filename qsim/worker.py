"""Fixed remote worker. Circuit text is data, never executable Python or shell."""
import base64
import collections
import io
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def simulate(source: Path, output: Path):
    # Bound native compiler/sampler allocations as well as Python allocations.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(300)
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (300, 301))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2, 12 * 1024**2))
    request = json.loads(source.read_text())
    if request.get("engine", "clifft") == "qsim":
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
        try:
            process = subprocess.run([sys.executable, __file__, "simulate", str(source), str(output)],
                                     env=env, capture_output=True, timeout=315)
            if process.returncode == -signal.SIGALRM:
                payload = {"status": "FAILED", "error": "Simulation exceeded the 300 second limit"}
            elif process.returncode:
                print(process.stderr.decode(errors="replace"), file=sys.stderr)
                message = "Circuit simulation failed; check the circuit and service limits"
                if request['input_format']=='qasm2':
                    message = "OpenQASM 2 simulation failed; use unitary qelib1.inc circuits without classical registers, measurement, reset or conditions, and bitstrings matching the qubit count. Check service limits."
                payload = {"status": "FAILED", "error": message}
            else:
                payload = json.loads(output.read_text())
        except subprocess.TimeoutExpired:
            payload = {"status": "FAILED", "error": "Simulation exceeded the 300 second limit"}
        call(payload)
    return 0 if payload["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "simulate":
        simulate(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit(remote())
