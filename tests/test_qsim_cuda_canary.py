import json
import subprocess

import pytest

from qsim import qsim_cuda_canary as canary


def test_frozen_canary_accepts_api_metadata_but_rejects_changed_work():
    body = canary.fixture("26q")
    canary.verify_fixture({**body, "name": "canary", "seed": 42, "shots": 1000})
    for changed in (
        {**body, "bit_order": "little"},
        {**body, "circuit": body["circuit"] + "h q[0];"},
        {**body, "bitstrings": body["bitstrings"][::-1]},
        {**body, "backend": "cpu"},
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
