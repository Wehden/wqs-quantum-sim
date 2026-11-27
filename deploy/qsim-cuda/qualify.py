"""Run the fixed matrix from the actual candidate image in a SkyPilot job."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np

from qsim.qsim_cuda_canary import CASES, check_device, fixture


def solve(case, arm, directory):
    source = directory / "input.json"
    output = directory / "result.json"
    source.write_text(json.dumps(fixture(case), sort_keys=True))
    output.unlink(missing_ok=True)
    env = {"PATH": "/usr/bin:/bin", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
    for key in ("CUDA_VISIBLE_DEVICES", "LD_LIBRARY_PATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+") as samples:
        monitor = None
        if arm == "cuda":
            monitor = subprocess.Popen([
                "/usr/bin/nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                "--format=csv,noheader,nounits", "--loop-ms=100",
            ], stdout=samples, stderr=subprocess.STDOUT)
        try:
            process = subprocess.run(
                [sys.executable, "-m", "qsim.qsim_cuda_canary", arm, str(source), str(output)],
                check=True, stdout=subprocess.PIPE, text=True, env=env, timeout=120,
            )
        finally:
            if monitor is not None:
                monitor.terminate()
                monitor.wait(timeout=5)
        samples.seek(0)
        telemetry = [tuple(map(int, raw.split(","))) for raw in samples]
    elapsed = time.monotonic() - started
    native = json.loads(process.stdout)
    response = json.loads(output.read_text())
    if response["status"] != "SUCCEEDED":
        raise RuntimeError("The worker did not produce a complete result")
    artifact = base64.b64decode(response["artifact"], validate=True)
    with np.load(io.BytesIO(artifact), allow_pickle=False) as data:
        probabilities = data["probabilities"]
        np.testing.assert_array_equal(data["bitstrings"], fixture(case)["bitstrings"])
        if set(data.files) != {"probabilities", "bitstrings"}:
            raise ValueError("Unexpected NPZ fields")
    if probabilities.shape != (64,) or not np.all(
        np.isfinite(probabilities) & (probabilities >= 0) & (probabilities <= 1.000002)
    ):
        raise ValueError("Invalid probabilities")
    np.testing.assert_array_equal(probabilities, response["summary"]["probabilities"])
    if case == "bit-order":
        expected = np.zeros(64)
        expected[2] = 1
        np.testing.assert_allclose(probabilities, expected, atol=2e-6, rtol=0)
    peak_vram_mib = max((memory for memory, _ in telemetry), default=0)
    peak_utilization = max((utilization for _, utilization in telemetry), default=0)
    print(json.dumps({
        "case": case, "arm": arm, "fresh_s": elapsed,
        "worker_s": response["summary"]["elapsed_seconds"],
        "peak_vram_mib": peak_vram_mib, "peak_utilization": peak_utilization,
        "fixture_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(), **native,
    }, sort_keys=True), flush=True)
    # NVML reports utilization over a device-dependent sampling interval.
    # A short circuit can finish between samples. Use native identity and the
    # completed, CPU-matched result as the execution proof, and retain both
    # telemetry readings for diagnostics.
    if arm == "cuda" and not 0 < peak_vram_mib <= 12 * 1024 * 0.75:
        raise ValueError("The CUDA run did not meet its sampled VRAM checks")
    return probabilities


def main():
    if os.getuid() != 1000:
        raise RuntimeError("Run the qualification as the image's qsim user")
    if sys.argv[1:] == ["--check-package"]:
        import qsimcirq

        if qsimcirq.qsim_gpu.__name__ != "qsimcirq.qsim_cuda" or qsimcirq.qsim_custatevec is not None:
            raise RuntimeError("Unexpected native CUDA backend")
        with tempfile.TemporaryDirectory() as directory:
            solve("bit-order", "cpu", Path(directory))
        return
    check_device()
    with tempfile.TemporaryDirectory() as directory:
        for case, (width, _) in CASES.items():
            for trial in range(1 if case == "bit-order" else 3):
                arms = ("cpu", "cuda") if trial % 2 == 0 else ("cuda", "cpu")
                results = {arm: solve(case, arm, Path(directory)) for arm in arms}
                scale = 1 if case == "bit-order" else 2**width
                np.testing.assert_allclose(
                    results["cuda"] * scale, results["cpu"] * scale, atol=1e-3, rtol=3e-3,
                )
                print(json.dumps({"case": case, "trial": trial + 1, "paired": "pass"}), flush=True)


if __name__ == "__main__":
    main()
