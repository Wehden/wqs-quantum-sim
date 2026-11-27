import json
import sqlite3
import subprocess
import time
from types import SimpleNamespace

import pytest

from qsim import qsim_cuda_canary as canary
from qsim.models import Submission
from tools.canary.qsim_api_timings import Study


def test_frozen_canary_accepts_api_metadata_but_rejects_changed_work():
    body = canary.fixture("26q")
    canary.verify_fixture(json.loads(Submission.model_validate(body).model_dump_json()))
    canary.verify_fixture({**body, "name": "canary", "seed": 42, "shots": 1000})
    for changed in (
        {**body, "bit_order": "little"},
        {**body, "circuit": body["circuit"] + "h q[0];"},
        {**body, "bitstrings": body["bitstrings"][::-1]},
        {**body, "backend": "cpu"},
        {**body, "bindings": {"x": 1.0}},
    ):
        with pytest.raises(ValueError, match="frozen"):
            canary.verify_fixture(changed)


def test_canary_rejects_mismatched_work_before_driver_or_limits(tmp_path, monkeypatch):
    def must_not_run(*args, **kwargs):
        pytest.fail("Rejected request reached device or resource initialization")

    monkeypatch.setattr(canary, "check_device", must_not_run)
    monkeypatch.setattr(canary.resource, "setrlimit", must_not_run)
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"engine": "qsim", "input_format": "qasm3"}))
    with pytest.raises(ValueError, match="frozen"):
        canary.simulate(source, tmp_path / "result.json", "cuda")


@pytest.mark.parametrize(
    "device,process,valid",
    [
        ("NVIDIA GeForce RTX 3060, 12288, 0", "", True),
        ("NVIDIA GeForce RTX 3060, 12288, 0", "2104", False),
        ("NVIDIA GeForce RTX 3060, 12288, 4000", "", False),
        ("NVIDIA GeForce RTX 3060, 8192, 0", "", False),
        ("NVIDIA A100, 40960, 0", "", False),
        ("NVIDIA GeForce RTX 3060, 12288, 0\nNVIDIA GeForce RTX 3060, 12288, 0", "", False),
    ],
)
def test_canary_requires_one_empty_twelve_gib_3060(monkeypatch, device, process, valid):
    def read_query(argv, **kwargs):
        return process if argv[1] == "--query-compute-apps=pid" else device

    monkeypatch.setattr(subprocess, "check_output", read_query)
    if valid:
        canary.check_device()
    else:
        with pytest.raises(RuntimeError):
            canary.check_device()


def test_timing_dispatch_defers_second_job_while_worker_is_occupied():
    request = json.dumps(canary.fixture("24q"))
    row = {"cleaned": 0, "status": "QUEUED", "cluster": None, "request": request}
    advanced = []
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE worker_pool("
            "cluster TEXT, job_id TEXT, released REAL, retired INTEGER, image TEXT)"
        )
        db.execute("INSERT INTO worker_pool VALUES('worker-one','first',NULL,0,'image')")
        study = Study.__new__(Study)
        study.row = lambda _job: row
        study.owned = {"worker-one"}
        study.settings = SimpleNamespace(WORKER_IMAGE="image")
        study.store = SimpleNamespace(connect=lambda: db)
        study.controller = SimpleNamespace(
            cluster_name=lambda job: "worker-" + job,
            advance=lambda driver, job_row: advanced.append((driver, job_row)),
        )

        assert study.advance("second", "driver") is False
        assert advanced == []

        db.execute("UPDATE worker_pool SET job_id=NULL,released=?", (time.time(),))
        assert study.advance("second", "driver") is True
        assert advanced == [("driver", row)]
