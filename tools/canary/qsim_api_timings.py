"""Drive the normal WQS API and dispatcher against disposable, private state."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from threading import Event
import time

import httpx
import numpy as np

from qsim.qsim_cuda_canary import fixture, verify_fixture

REPO = Path(__file__).resolve().parents[2]
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}$")
STAGE_OWNER = "qsim-cuda-timing"


def emit(kind, **record):
    print(json.dumps({"kind": kind, **record}, allow_nan=False), flush=True)


def eventually(fetch, seconds, description):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fetch()
        if value is not None:
            return value
        time.sleep(1)
    raise TimeoutError(description)


class StageSky:
    """Apply local-only launch options before using normal SkyPilot SDK calls."""

    def __init__(self, sky, arm):
        self.sky = sky
        self.arm = arm
        self.Task = self

    def __getattr__(self, name):
        return getattr(self.sky, name)

    def from_yaml_config(self, config):
        config["config"] = {"kubernetes": {
            "set_pod_resource_limits": True,
            "provision_timeout": 240,
            "pod_config": {"spec": {
                "automountServiceAccountToken": False,
                "containers": [{
                    "name": "ray-node",
                    "imagePullPolicy": "IfNotPresent",
                    "env": [{"name": "UV_EXCLUDE_NEWER", "value": "2026-08-01"}],
                }],
            }},
        }}
        if self.arm == "cuda":
            config["resources"].update({
                "accelerators": "RTX3060:1", "cpus": 8, "memory": "16Gi",
                "ephemeral_storage": "30Gi", "use_spot": False,
            })
            config["run"] = (
                "cd /opt/qsim && timeout --kill-after=15s 3m "
                "/usr/bin/sudo -n -H -u qsim "
                "--preserve-env=QSIM_JOB_URL,QSIM_JOB_TOKEN,CUDA_VISIBLE_DEVICES,LD_LIBRARY_PATH "
                "/opt/qsim-venv/bin/python -m qsim.qsim_cuda_canary --remote"
            )
        return self.sky.Task.from_yaml_config(config)


class Study:
    def __init__(self, args, api):
        from qsim import controller, settings, store
        import sky

        self.args = args
        self.api = api
        self.controller = controller
        self.settings = settings
        self.store = store
        self.sky = sky
        self.owned = set()
        self.deadline = time.monotonic() + args.minutes * 60

    def submit(self, case, key):
        body = fixture(case)
        verify_fixture(body)
        start = time.monotonic()
        reply = self.api.post("/api/jobs", json=body, headers={"Idempotency-Key": key})
        reply.raise_for_status()
        if reply.status_code != 202:
            raise ValueError("Unexpected submission status")
        return reply.json()["id"], start, time.monotonic(), reply.headers["Idempotency-Replayed"]

    def row(self, job):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT jobs.*,job_dispatch.* FROM jobs JOIN job_dispatch "
                "ON jobs.id=job_dispatch.job_id WHERE jobs.id=?", (job,),
            ).fetchone()
        if row["owner"] != STAGE_OWNER:
            raise RuntimeError("The timing study does not own this job")
        return row

    def advance(self, job, task_driver):
        row = self.row(job)
        if row["cleaned"]:
            return
        if row["status"] == "QUEUED" and row["cluster"] is None:
            verify_fixture(json.loads(row["request"]))
            if not self.owned:
                cluster = self.controller.cluster_name(job)
                if self.sky.get(self.sky.status([cluster])):
                    raise RuntimeError("The new timing worker name is already used")
                self.owned.add(cluster)
            else:
                with self.store.connect() as db:
                    available = db.execute(
                        "SELECT cluster FROM worker_pool WHERE job_id IS NULL AND retired=0 "
                        "AND image=? AND released>?",
                        (self.settings.WORKER_IMAGE, time.time() - 530),
                    ).fetchall()
                if len(available) != 1 or available[0]["cluster"] not in self.owned:
                    raise RuntimeError("The ready timing trial lost its compatible worker")
        self.controller.advance(task_driver, row)

    def event_frames(self, job, after):
        with self.api.stream("GET", f"/api/jobs/{job}/events", headers={"Last-Event-ID": str(after)}) as reply:
            reply.raise_for_status()
            fields = {}
            for line in reply.iter_lines():
                if not line:
                    if "id" in fields and "data" in fields:
                        yield int(fields["id"]), json.loads(fields["data"])
                    fields.clear()
                elif not line.startswith(":"):
                    key, _, value = line.partition(":")
                    fields[key] = value.lstrip()

    def worker_image_receipt(self, cluster):
        commands = ["kubectl", "--context", self.args.kube_context, "get", "pods",
                    "-n", self.args.sky_namespace, "-o", "json"]
        pods = json.loads(subprocess.check_output(commands, timeout=12))["items"]
        matches = [pod for pod in pods if pod["metadata"]["name"].startswith(cluster + "-")
                   and pod["status"]["phase"] == "Running"]
        if len(matches) != 1:
            raise RuntimeError("Could not verify the exact SkyPilot worker pod")
        pod = matches[0]
        native = [row for row in pod["status"]["containerStatuses"] if row["name"] == "ray-node"]
        if len(native) != 1 or native[0]["imageID"].split("@")[-1] != self.settings.WORKER_IMAGE.split("@")[-1]:
            raise RuntimeError("The realized worker image digest differs")
        return {"host": pod["spec"]["nodeName"], "pod_uid": pod["metadata"]["uid"],
                "digest": native[0]["imageID"].split("@")[-1]}

    def retire(self):
        for cluster in sorted(self.owned):
            requests = self.sky.api_status(cluster_name=cluster, all_status=True)
            for request in requests:
                status = getattr(request.status, "value", request.status)
                if request.cluster_name == cluster and request.name in ("sky.launch", "sky.exec") and status in ("PENDING", "RUNNING"):
                    self.sky.get(self.sky.api_cancel(request.request_id, silent=True))
            try:
                self.sky.get(self.sky.down(cluster))
            except self.sky.exceptions.ClusterDoesNotExist:
                pass
            if self.sky.get(self.sky.status([cluster], refresh=True)):
                raise RuntimeError("Timing worker still present after retirement")
            with self.store.connect() as db:
                db.execute("DELETE FROM worker_pool WHERE cluster=?", (cluster,))
            emit("retired", cluster=cluster)
        self.owned.clear()

    def sample(self, arm, driver, case, expected, cluster=None, replay=False, *, kind="sample"):
        if self.deadline - time.monotonic() < 600:
            raise TimeoutError("The study reached its last safe start time")
        key = secrets.token_hex(16)
        job, start, admitted, replayed = self.submit(case, key)
        if replayed != "false":
            raise RuntimeError("An initial job was unexpectedly replayed")
        with self.store.connect() as db:
            db.execute("INSERT INTO job_dispatch(job_id) VALUES(?)", (job,))
        try:
            return self.measure(driver, case, job, key, start, admitted, expected, cluster, replay, kind=kind)
        except BaseException as error:
            emit("failed", arm=arm, case=case, expected=expected, job=job,
                 seconds=time.monotonic() - start, error=type(error).__name__)
            raise

    def measure(self, driver, case, job, key, start, admitted, expected, cluster, replay, *, kind="sample"):
        # Capture the first SSE frame before dispatch. Polling only drives the
        # controller; client timings use the actual SSE frames and result GET.
        streaming = Event()

        def read_events():
            seen = []
            after = 0
            deadline = start + 480
            while time.monotonic() < deadline:
                frames = self.event_frames(job, after)
                with closing(frames):
                    for after, event in frames:
                        seen.append({"id": after, "status": event["status"],
                                     "received_s": time.monotonic() - start,
                                     "persisted_epoch": event["updated"]})
                        streaming.set()
                        if replay and len(seen) == 1:
                            frames.close()
                            other, _, _, flag = self.submit(case, key)
                            if other != job or flag != "true":
                                raise RuntimeError("Interrupted stream did not replay the same job")
                            break
                        if event["status"] in TERMINAL:
                            return seen, time.monotonic()
            raise TimeoutError("SSE did not deliver a terminal result")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read_events)
            try:
                if not streaming.wait(10):
                    raise TimeoutError("The client did not receive its initial queued event")

                def completed():
                    self.advance(job, driver)
                    return future.result() if future.done() else None

                seen, terminal = eventually(completed, 460, "timing job did not complete")
            except BaseException:
                self.api.post(f"/api/jobs/{job}/cancel")
                raise
        row = self.row(job)
        if row["status"] != "SUCCEEDED" or row["method"] != expected or (cluster and cluster != row["cluster"]):
            raise RuntimeError("Job status or provisioning method differs from the expected trial")
        response = self.api.get(f"/api/jobs/{job}/result")
        finished = time.monotonic()
        response.raise_for_status()
        with np.load(io.BytesIO(response.content), allow_pickle=False) as result:
            probability = result["probabilities"]
            if probability.shape != (64,) or not np.isfinite(probability).all():
                raise RuntimeError("Invalid probability artifact")
            np.testing.assert_array_equal(result["bitstrings"], fixture(case)["bitstrings"])
        summary = json.loads(row["summary"])
        np.testing.assert_array_equal(summary["probabilities"], probability)
        if driver.arm == "cuda" and summary["canary_native"]["native_module"] != "qsimcirq.qsim_cuda":
            raise RuntimeError("GPU arm lacked native CUDA proof")

        def native_complete():
            self.advance(job, driver)
            row = self.row(job)
            if not row["cleaned"]:
                return None
            launch = self.sky.get(row["launch_id"])
            queue = self.sky.get(self.sky.queue(row["cluster"]))
            for entry in queue:
                state = getattr(entry["status"], "value", entry["status"])
                if entry["job_id"] == launch[0] and state == "SUCCEEDED":
                    return entry
            return None

        native = eventually(native_complete, 60, "native job did not complete")
        placement = self.worker_image_receipt(row["cluster"])
        request = [item for item in self.sky.api_status(request_ids=[row["launch_id"]])
                   if str(item.request_id) == row["launch_id"] and item.name == f"sky.{expected}"]
        if len(request) != 1:
            raise RuntimeError("The native dispatch request did not match WQS state")
        emit(kind, arm=driver.arm, case=case, job=job, dispatch=expected,
             cluster=row["cluster"], request=row["launch_id"], native_job=native["job_id"],
             native_times={key: native[key] for key in ("submitted_at", "start_at", "end_at")},
             sky_request_created=request[0].created_at, sky_request_finished=request[0].finished_at,
             admission_s=admitted-start, callback_to_result_s=finished-terminal,
             total_s=finished-start, events=seen, solver_s=summary["elapsed_seconds"],
             result_sha256=hashlib.sha256(response.content).hexdigest(), **placement)
        return row["cluster"], probability

    def run(self):
        for case in self.args.cases:
            for block in range(self.args.cold):
                arms = ("cpu", "cuda") if block % 2 == 0 else ("cuda", "cpu")
                reference = {}
                for arm in arms:
                    if arm not in self.args.arms:
                        continue
                    self.retire()
                    subprocess.run([str(self.args.preflight), arm], check=True, timeout=90)
                    self.settings.WORKER_IMAGE = self.args.cpu_image if arm == "cpu" else self.args.cuda_image
                    driver = StageSky(self.sky, arm)
                    cluster, values = self.sample(arm, driver, case, "launch", replay=block == 0)
                    reference[arm] = values
                    count, remainder = divmod(self.args.ready, self.args.cold)
                    for _ in range(count + (block < remainder)):
                        _, repeated = self.sample(arm, driver, case, "exec", cluster=cluster)
                        np.testing.assert_allclose(repeated, values, atol=1e-10, rtol=2e-4)
                if len(reference) == 2:
                    scale = 2 ** len(fixture(case)["bitstrings"][0])
                    np.testing.assert_allclose(
                        reference["cuda"] * scale, reference["cpu"] * scale, atol=1e-3, rtol=3e-3,
                    )

    def check_serial_queue(self, arm, case):
        """Observe a second durable job while the study owns one busy worker."""
        self.retire()
        subprocess.run([str(self.args.preflight), arm], check=True, timeout=90)
        self.settings.WORKER_IMAGE = self.args.cpu_image if arm == "cpu" else self.args.cuda_image
        driver = StageSky(self.sky, arm)
        cluster, _ = self.sample(arm, driver, case, "launch", kind="queue_sample")
        first, start, accepted, _ = self.submit(case, secrets.token_hex(16))
        second = None
        try:
            with self.store.connect() as db:
                db.execute("INSERT INTO job_dispatch(job_id) VALUES(?)", (first,))
            self.advance(first, driver)
            key = secrets.token_hex(16)
            queued, queued_at, admitted, _ = self.submit(case, key)
            second = queued
            with self.store.connect() as db:
                db.execute("INSERT INTO job_dispatch(job_id) VALUES(?)", (queued,))
            pending = self.row(queued)
            if pending["status"] != "QUEUED" or pending["launch_id"] is not None:
                raise RuntimeError("The second timing job bypassed the single-worker study queue")
            self.measure(driver, case, first, "", start, accepted, "exec", cluster, False, kind="queue_sample")
            self.measure(driver, case, queued, key, queued_at, admitted, "exec", cluster, False, kind="queue_sample")
            emit("serial_queue", arm=arm, cluster=cluster, first=first, second=queued)
        except BaseException:
            for job in (first, second):
                if job:
                    self.api.post(f"/api/jobs/{job}/cancel")
            raise


def private_timing_app():
    from qsim.api import app

    local = os.environ["WQS_TIMING_LISTEN"]

    async def private_routes(scope, receive, send):
        from starlette.responses import PlainTextResponse

        if (scope["type"] == "http" and scope["client"][0] != local
                and not re.fullmatch(r"/internal/jobs/[0-9a-f]{32}", scope["path"])):
            await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
            return
        await app(scope, receive, send)

    return private_routes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="new private scratch directory")
    parser.add_argument("--listen", type=ipaddress.IPv4Address, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--cpu-image", required=True)
    parser.add_argument("--cuda-image", required=True)
    parser.add_argument("--kube-context", required=True)
    parser.add_argument("--sky-namespace", required=True)
    parser.add_argument("--preflight", type=Path, required=True,
                        help="workload-policy and current capacity check before each cold cohort")
    parser.add_argument("--api-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--minutes", type=int, choices=range(11, 301), default=100)
    parser.add_argument("--serial-queue-check", action="store_true")
    parser.add_argument("--cases", nargs="+", choices=("24q", "26q"), default=("24q", "26q"))
    parser.add_argument("--arms", nargs="+", choices=("cpu", "cuda"), default=("cpu", "cuda"))
    parser.add_argument("--cold", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--ready", type=int, default=10, choices=range(11))
    args = parser.parse_args()
    if not all(IMAGE.fullmatch(ref) for ref in (args.cpu_image, args.cuda_image)):
        parser.error("Both worker images must have explicit SHA-256 digests")
    if not any(args.listen in ipaddress.ip_network(network) for network in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    )):
        parser.error("Bind only to a private network")
    if not os.environ.get("SKYPILOT_API_SERVER_ENDPOINT", "").startswith(("http://", "https://")):
        parser.error("Set the approved remote SkyPilot authority")
    args.state = args.state.resolve()
    args.state.mkdir(mode=0o700)
    url = f"http://{args.listen}:{args.port}"
    os.umask(0o077)
    token = "wqs-stage-" + secrets.token_urlsafe(32)
    user = {"id": STAGE_OWNER, "active": True, "sha256": hashlib.sha256(token.encode()).hexdigest()}
    (args.state / "users.json").write_text(json.dumps([user]))
    os.environ.update(QSIM_DATA=str(args.state), QSIM_CALLBACK_URL=url, QSIM_SKY_INFRA="k8s",
                      QSIM_TOKEN_FILE=str(args.state / "users.json"),
                      WQS_TIMING_LISTEN=str(args.listen))
    api_env = {key: os.environ[key] for key in ("PATH", "QSIM_DATA", "QSIM_TOKEN_FILE", "WQS_TIMING_LISTEN")}
    api_env["HOME"] = str(args.state)
    with (args.state / "api.log").open("w") as log:
        api_server = subprocess.Popen([
            str(args.api_python.absolute()), "-m", "uvicorn", "tools.canary.qsim_api_timings:private_timing_app",
            "--factory", "--host", str(args.listen),
            "--port", str(args.port), "--no-access-log",
        ], cwd=REPO, env=api_env, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=url, timeout=70, headers={"Authorization": "Bearer " + token},
                              trust_env=False) as api:
                def ready():
                    try:
                        return True if api.get("/readyz").status_code == 200 else None
                    except httpx.TransportError:
                        return None

                eventually(ready, 20, "Staging API did not become ready")
                study = Study(args, api)
                try:
                    study.run()
                    if args.serial_queue_check:
                        study.check_serial_queue(args.arms[-1], args.cases[0])
                finally:
                    study.retire()
        finally:
            api_server.terminate()
            api_server.wait(timeout=15)


if __name__ == "__main__":
    main()
