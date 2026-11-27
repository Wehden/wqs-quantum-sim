"""Private exact-image qsim qualification. Never imported by the service API."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import random
import resource
import subprocess
import sys

from .worker import remote, simulate_qsim

GIB = 1024**3
CASES = {"bit-order": (8, 0), "24q": (24, 20), "26q": (26, 60)}


def fixture(case):
    """Use the discovery matrix's first seed and a common QASM 2 dialect."""
    width, depth = CASES[case]
    rng = random.Random(481516 + width + depth)
    program = [f'OPENQASM 2.0; include "qelib1.inc"; qreg q[{width}];']
    if not depth:
        program.extend(("x q[0];", "x q[3];"))
    for layer in range(depth):
        for qubit in range(width):
            gate = ("rx", "ry", "rz")[(layer + qubit) % 3]
            angle = rng.uniform(-2.8, 2.8)
            program.append(f"{gate}({angle:.14g}) q[{qubit}];")
        for qubit in range(layer % 2, width - 1, 2):
            program.append(f"cx q[{qubit}],q[{qubit + 1}];")
    indices = [0, (1 << width) - 1, (1 << (width - 1)) | (1 << (width - 4))]
    while len(indices) < 64:
        value = rng.randrange(1 << width)
        if value not in indices:
            indices.append(value)
    order = "big" if depth else "little"
    bitstrings = [format(index, f"0{width}b") for index in indices]
    if order == "little":
        bitstrings = [bits[::-1] for bits in bitstrings]
    return {
        "engine": "qsim", "input_format": "qasm2", "operation": "basis_probabilities",
        "circuit": "".join(program), "bit_order": order, "bitstrings": bitstrings,
    }


def verify_fixture(request):
    keys = ("engine", "input_format", "operation", "circuit", "bit_order", "bitstrings")
    selected = {key: request.get(key) for key in keys}
    if (any(selected == fixture(case) for case in CASES)
            and not request.get("bindings", {})
            and set(request) <= set(keys) | {"name", "shots", "seed", "bindings"}):
        return
    raise ValueError("The canary requires one of its frozen probability fixtures")


def cuda_memory():
    """Inspect usable memory using the NVIDIA driver injected by the scheduler."""
    driver = ctypes.CDLL("libcuda.so.1")

    def call(name, *args):
        result = getattr(driver, name)(*args)
        if result:
            raise RuntimeError(f"{name} failed with CUDA driver status {result}")

    count, device = ctypes.c_int(), ctypes.c_int()
    context = ctypes.c_void_p()
    previous = ctypes.c_void_p()
    free, total = ctypes.c_size_t(), ctypes.c_size_t()
    call("cuInit", 0)
    call("cuDeviceGetCount", ctypes.byref(count))
    if count.value != 1:
        raise RuntimeError("The canary requires exactly one visible CUDA device")
    call("cuDeviceGet", ctypes.byref(device), 0)
    call("cuCtxGetCurrent", ctypes.byref(previous))
    call("cuDevicePrimaryCtxRetain", ctypes.byref(context), device)
    try:
        call("cuCtxSetCurrent", context)
        call("cuMemGetInfo_v2", ctypes.byref(free), ctypes.byref(total))
    finally:
        call("cuCtxSetCurrent", previous)
        call("cuDevicePrimaryCtxRelease_v2", device)
    if total.value < 12 * GIB * 0.95 or free.value < total.value * 0.90:
        raise RuntimeError("The assigned device has insufficient usable or free memory")
    return free.value, total.value


def check_device():
    def query(argument):
        return subprocess.check_output(
            ["/usr/bin/nvidia-smi", argument, "--format=csv,noheader,nounits"],
            text=True, timeout=15,
        ).strip()

    rows = query("--query-gpu=name,memory.total,memory.used").splitlines()
    if len(rows) != 1:
        raise RuntimeError("The canary requires exactly one physical GPU")
    name, total, used = map(str.strip, rows[0].split(","))
    if "RTX 3060" not in name or int(total) != 12 * 1024 or int(used) > int(total) * 0.10:
        raise RuntimeError("The assigned physical GPU did not meet the RTX 3060 profile")
    if query("--query-compute-apps=pid"):
        raise RuntimeError("The assigned GPU already has a compute process")


def simulate(source, output, arm):
    gpu = arm == "cuda"
    request = json.loads(source.read_text())
    verify_fixture(request)
    limit = (12 if gpu else 3) * GIB
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    proof = {}
    if gpu:
        check_device()
        proof["cuda_free_bytes"], proof["cuda_total_bytes"] = cuda_memory()

    def simulator_factory(qsimcirq):
        simulator = qsimcirq.QSimSimulator(qsim_options=qsimcirq.QSimOptions(
            cpu_threads=1 if gpu else 2, use_gpu=gpu, gpu_mode=0,
            **({"max_fused_gate_size": 4} if gpu else {}),
        ))
        native = simulator._sim_module
        if gpu and native.__name__ != "qsimcirq.qsim_cuda":
            raise RuntimeError("qsim CUDA module was not selected")
        if not gpu and native.__name__ in ("qsimcirq.qsim_cuda", "qsimcirq.qsim_custatevec"):
            raise RuntimeError("CPU comparison selected a GPU module")
        proof["native_module"] = native.__name__
        proof["native_sha256"] = hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest()
        return simulator

    simulate_qsim(request, output, simulator_factory=simulator_factory)
    proof["rss_peak_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    response = json.loads(output.read_text())
    response["summary"]["canary_native"] = proof
    output.write_text(json.dumps(response, allow_nan=False))
    print(json.dumps(proof, sort_keys=True), flush=True)


def main():
    if sys.argv[1:] == ["--remote"]:
        raise SystemExit(remote(
            simulator_module="qsim.qsim_cuda_canary", simulator_args=("cuda",),
            device_env=("CUDA_VISIBLE_DEVICES", "LD_LIBRARY_PATH"),
        ))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm", choices=("cpu", "cuda"))
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    simulate(args.source, args.output, args.arm)


if __name__ == "__main__":
    main()
