"""Protected release-verifier entry point for a single fixed CUDA job."""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import time

from . import release_control, settings, store
from .qsim_cuda_canary import GIB, fixture

HEX = r"[0-9a-f]{64}"
IMAGE = r"[^\s@]+@sha256:" + HEX
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
PROFILE = {"accelerators": "RTX3060:1", "cpus": 8, "memory_gb": 16,
           "ephemeral_storage_gb": 30, "use_spot": False}


def fixed_request_sha():
    return hashlib.sha256(json.dumps(fixture("26q"), sort_keys=True).encode()).hexdigest()


def verify_result(output, native_sha):
    import numpy as np

    prefix = "WQS_CUDA_RELEASE "
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    records = [line.split(prefix, 1)[1] for line in plain.splitlines() if prefix in line]
    if len(records) != 1 or len(records[0]) > 16384:
        raise ValueError("Expected one bounded CUDA result")
    result = json.loads(records[0])
    if (result.get("case") != "26q" or result.get("arm") != "cuda"
            or result.get("native_module") != "qsimcirq.qsim_cuda"
            or result.get("native_sha256") != native_sha
            or result.get("fixture_sha256") != fixed_request_sha()
            or not 0 < result.get("peak_vram_mib", 0) <= 9216
            or not 0.95 * 12 * GIB <= result["cuda_total_bytes"] <= 12 * GIB
            or not 0.90 * result["cuda_total_bytes"] <= result["cuda_free_bytes"] <= result["cuda_total_bytes"]
            or not 0 < result["fresh_s"] < 180 or not math.isfinite(result["fresh_s"])):
        raise ValueError("CUDA device, fixture, module or resource evidence mismatch")
    artifact = base64.b64decode(result.pop("artifact"), validate=True)
    if hashlib.sha256(artifact).hexdigest() != result["artifact_sha256"]:
        raise ValueError("CUDA artifact digest mismatch")
    reference = json.loads(Path(__file__).with_name("cuda_release_reference.json").read_text())
    with np.load(io.BytesIO(artifact), allow_pickle=False) as npz:
        if set(npz.files) != {"bitstrings", "probabilities"}:
            raise ValueError("CUDA artifact fields mismatch")
        np.testing.assert_array_equal(npz["bitstrings"], fixture("26q")["bitstrings"])
        p = npz["probabilities"]
        if p.shape != (64,) or not np.all(np.isfinite(p) & (p >= 0) & (p <= 1)):
            raise ValueError("Invalid CUDA probabilities")
        np.testing.assert_allclose(p * 2**26, reference["probabilities_scale_2_26"],
                                   rtol=3e-3, atol=1e-3)
    result["reference_sha256"] = reference["source_artifact_sha256"]
    result["mathematics_verified"] = True
    return result


def task_config(image, infra):
    if not re.fullmatch(r"k8s(?:/[A-Za-z0-9_.-]+)?", infra):
        raise ValueError("The CUDA canary requires Kubernetes through SkyPilot")
    return {
        "name": "wqs-held-cuda",
        "resources": {"accelerators": PROFILE["accelerators"], "cpus": PROFILE["cpus"],
                      "memory": PROFILE["memory_gb"], "use_spot": False,
                      "infra": infra, "image_id": "docker:" + image},
        "config": {"kubernetes": {"set_pod_resource_limits": True, "provision_timeout": 240,
            "pod_config": {"spec": {"automountServiceAccountToken": False, "containers": [
                {"name": "ray-node", "imagePullPolicy": "IfNotPresent",
                 "resources": {"requests": {"ephemeral-storage": "30G"},
                               "limits": {"ephemeral-storage": "30G"}},
                 "env": [{"name": "UV_EXCLUDE_NEWER", "value": "2026-08-01"}]},
            ]}}}},
        "run": ("cd /opt/qsim && timeout --kill-after=15s 3m /usr/bin/sudo -n -H -u qsim "
                "--preserve-env=CUDA_VISIBLE_DEVICES,LD_LIBRARY_PATH "
                "/opt/qsim-venv/bin/python /opt/qsim/qualify.py --held-release"),
    }


def status_value(value):
    return getattr(value, "value", value)


def wait_request(sky, request, deadline):
    while time.monotonic() < deadline:
        rows = sky.api_status(request_ids=[str(request)], fields=["request_id", "status"])
        if len(rows) == 1 and status_value(rows[0].status) in TERMINAL:
            return status_value(rows[0].status)
        time.sleep(3)
    raise TimeoutError("SkyPilot request deadline exceeded")


def cluster_status(sky, cluster, deadline):
    from sky.utils.common import StatusRefreshMode

    request = sky.status(cluster_names=[cluster], refresh=StatusRefreshMode.FORCE)
    if wait_request(sky, request, deadline) != "SUCCEEDED":
        raise RuntimeError("SkyPilot allocation inventory unavailable")
    return [row for row in sky.get(request) if row["name"] == cluster]


def native_success(sky, cluster, job, deadline):
    while time.monotonic() < deadline:
        request = sky.queue(cluster, skip_finished=False)
        if wait_request(sky, request, deadline) != "SUCCEEDED":
            raise RuntimeError("SkyPilot native queue unavailable")
        jobs = [row for row in sky.get(request) if row["job_id"] == job]
        if len(jobs) != 1:
            raise ValueError("Native canary job identity mismatch")
        status = status_value(jobs[0]["status"])
        if status == "SUCCEEDED":
            return
        if status not in {"INIT", "PENDING", "SETTING_UP", "RUNNING"}:
            raise RuntimeError("Native canary job failed")
        time.sleep(3)
    raise TimeoutError("Native canary job deadline exceeded")


def verify_capacity(sky, infra, deadline):
    from sky.client.sdk import kubernetes_node_info

    request = kubernetes_node_info(context=infra.partition("/")[2] or None)
    if wait_request(sky, request, deadline) != "SUCCEEDED":
        raise RuntimeError("SkyPilot resource inventory unavailable")
    nodes = sky.get(request).node_info_dict.values()
    if not any(node.is_ready and not node.is_cordoned and node.accelerator_type == "RTX3060"
               and node.free >= 1 and node.cpu_free >= 8 and node.memory_free_gb >= 16 for node in nodes):
        raise RuntimeError("No RTX 3060 capacity with eight CPU cores and 16 GB host memory")


def persist(path, record):
    temp = path.with_suffix(".tmp")
    with temp.open("w") as stream:
        json.dump(record, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def retire(sky, record, journal):
    deadline = time.monotonic() + 150
    cluster = record["cluster"]
    if "launch_request" not in record:
        matches = [
            row for row in sky.api_status(cluster_name=cluster, all_status=True)
            if row.name == "sky.launch" and row.cluster_name == cluster
            and row.created_at >= record["started"]
        ]
        if len(matches) != 1:
            raise RuntimeError("Uncertain launch requires reconciliation")
        record["launch_request"] = str(matches[0].request_id)
        persist(journal, record)
    request = record["launch_request"]
    rows = sky.api_status(request_ids=[request], fields=["request_id", "status"])
    if len(rows) != 1:
        raise RuntimeError("Uncertain canary request requires reconciliation")
    if status_value(rows[0].status) not in TERMINAL:
        cancel = sky.api_cancel([request], silent=True)
        wait_request(sky, cancel, deadline)
        wait_request(sky, request, deadline)
    down = sky.down(cluster)
    record["down_request"] = str(down)
    persist(journal, record)
    wait_request(sky, down, deadline)
    try:
        sky.get(down)
    except sky.exceptions.ClusterDoesNotExist:
        pass
    if cluster_status(sky, cluster, deadline):
        raise ValueError("Canary worker retirement unverified")
    record["retired"] = True


def execute(sky, sha, owner, image, native_sha, catalog_sha):
    if (not re.fullmatch(r"[0-9a-f]{40}", sha) or not owner
            or not re.fullmatch(IMAGE, image) or not re.fullmatch(HEX, native_sha)
            or not re.fullmatch(HEX, catalog_sha)):
        raise ValueError("Invalid held CUDA release identity")
    with store.connect() as db:
        state = release_control.snapshot(db)
    if state["release"] != sha or state["smoke_owner"] != owner or state["pending"]:
        raise ValueError("Matching drained smoke admission required")
    context = {"sha": sha, "owner": owner, "worker_image": image, "native_sha256": native_sha,
               "fixture_sha256": fixed_request_sha(), "profile": PROFILE}
    cluster = "wqs-cuda-" + sha[:20]
    journal = settings.DATA / ("held-cuda-" + sha + ".json")
    with journal.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if journal.exists():
            record = json.loads(journal.read_text())
            if record["context"] == context and record.get("status") == "verified":
                return record
            raise ValueError("Existing CUDA attempt requires reconciliation")
        deadline = time.monotonic() + 450
        if cluster_status(sky, cluster, deadline):
            raise ValueError("Canary allocation is already present")
        infra = os.environ.get("QSIM_SKY_INFRA", "k8s")
        task = sky.Task.from_yaml_config(task_config(image, infra))
        verify_capacity(sky, infra, deadline)
        record = {"context": context, "cluster": cluster, "started": time.time(),
                  "catalog_sha256": catalog_sha, "status": "launch_intent"}
        persist(journal, record)
        try:
            request = sky.launch(task, cluster_name=cluster, idle_minutes_to_autostop=5, down=True)
            record["launch_request"] = str(request)
            persist(journal, record)
            if wait_request(sky, request, deadline) != "SUCCEEDED":
                raise RuntimeError("Canary placement failed")
            job, _ = sky.get(request)
            if type(job) is not int or job < 1:
                raise ValueError("Invalid native canary job ID")
            record["native_job_id"] = job
            persist(journal, record)
            matches = cluster_status(sky, cluster, deadline)
            if len(matches) != 1 or status_value(matches[0]["status"]) != "UP":
                raise ValueError("Native canary allocation unavailable")
            handle = matches[0]["handle"]
            placed = handle.launched_resources
            if (handle.launched_nodes != 1 or placed.extract_docker_image() != image
                    or placed.accelerators != {"RTX3060": 1}
                    or float(str(placed.cpus).rstrip("+")) < 8
                    or float(str(placed.memory).rstrip("+")) < 16):
                raise ValueError("Native canary placement profile mismatch")
            record["placed"] = {"accelerators": placed.accelerators, "nodes": handle.launched_nodes,
                                "cpus": float(str(placed.cpus).rstrip("+")),
                                "memory_gb": float(str(placed.memory).rstrip("+"))}
            native_success(sky, cluster, job, deadline)
            output = io.StringIO()
            if sky.tail_logs(cluster, job, follow=False, tail=30, output_stream=output) != 0:
                raise ValueError("Native canary log unavailable")
            record["result"] = verify_result(output.getvalue(), native_sha)
            record["native_status"] = "SUCCEEDED"
        finally:
            record["status"] = "retiring"
            persist(journal, record)
            try:
                retire(sky, record, journal)
            finally:
                record["status"] = ("verified" if record.get("retired")
                                    and record.get("native_status") == "SUCCEEDED" else "failed")
                persist(journal, record)
        return record


def main():
    import signal
    import sky

    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("sha", "owner", "image", "native-sha", "catalog-sha"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    os.umask(0o077)

    def terminate(_signum, _frame):
        raise TimeoutError("Held CUDA verifier interrupted")

    signal.signal(signal.SIGTERM, terminate)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            record = execute(sky, args.sha, args.owner, args.image, args.native_sha, args.catalog_sha)
        print(json.dumps(record))
    except Exception as exc:
        raise SystemExit("Held CUDA canary failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
