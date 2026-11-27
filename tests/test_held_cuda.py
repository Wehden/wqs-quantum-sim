import base64
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pytest

from qsim import held_cuda

IMAGE = 'registry.example.invalid/worker@sha256:' + 'a' * 64
NATIVE = 'b' * 64


def worker_output(*, change=None, all_zero=False):
    bits = held_cuda.fixture('26q')['bitstrings']
    reference = json.loads(Path(held_cuda.__file__).with_name('cuda_release_reference.json').read_text())
    p = np.asarray(reference['probabilities_scale_2_26']) / 2**26
    artifact = io.BytesIO()
    np.savez_compressed(artifact, bitstrings=bits, probabilities=np.zeros_like(p) if all_zero else p)
    raw = artifact.getvalue()
    value = {
        'arm': 'cuda', 'case': '26q', 'fixture_sha256': held_cuda.fixed_request_sha(),
        'native_module': 'qsimcirq.qsim_cuda', 'native_sha256': NATIVE,
        'cuda_free_bytes': 12_000_000_000, 'cuda_total_bytes': 12_500_000_000,
        'peak_vram_mib': 627, 'fresh_s': 7.65,
        'artifact': base64.b64encode(raw).decode(), 'artifact_sha256': hashlib.sha256(raw).hexdigest(),
    }
    return 'WQS_CUDA_RELEASE ' + json.dumps({**value, **(change or {})})


def test_worker_receipt_requires_correct_cuda_and_probabilities():
    result = held_cuda.verify_result(worker_output(), NATIVE)
    assert result['mathematics_verified'] and 'artifact' not in result
    with pytest.raises(AssertionError):
        held_cuda.verify_result(worker_output(all_zero=True), NATIVE)
    for change in ({'arm': 'cpu'}, {'native_sha256': 'c' * 64}, {'fixture_sha256': 'd' * 64},
                   {'cuda_free_bytes': 1}, {'peak_vram_mib': 0}, {'artifact_sha256': 'e' * 64}):
        with pytest.raises(ValueError):
            held_cuda.verify_result(worker_output(change=change), NATIVE)


def test_task_requests_only_the_measured_gpu_profile():
    config = held_cuda.task_config(IMAGE, 'k8s/context')
    assert config['resources'] == {'infra': 'k8s/context', 'accelerators': 'RTX3060:1',
                                   'cpus': 8, 'memory': 16, 'use_spot': False,
                                   'image_id': 'docker:' + IMAGE}
    spec = config['config']['kubernetes']['pod_config']['spec']
    assert spec['automountServiceAccountToken'] is False
    assert spec['containers'][0]['resources']['requests'] == {'ephemeral-storage': '30G'}
    assert 'nodeSelector' not in spec and '--held-release' in config['run']
    with pytest.raises(ValueError):
        held_cuda.task_config(IMAGE, 'runpod')


def test_held_verifier_checks_smoke_identity_before_any_sky_operation(monkeypatch, tmp_path):
    from qsim import release_control, settings, store

    monkeypatch.setattr(settings, 'DATA', tmp_path)
    store.init()
    sha = 'c' * 40
    release_control.control('hold', sha)
    release_control.control('smoke', sha, 'release-verifier')
    with pytest.raises(ValueError, match='smoke admission'):
        held_cuda.execute(None, sha, 'another-user', IMAGE, NATIVE, 'd' * 64)


def test_capacity_requires_host_resources_as_well_as_gpu_slot(monkeypatch):
    from types import SimpleNamespace
    import time
    from sky.client import sdk

    monkeypatch.setattr(sdk, 'kubernetes_node_info', lambda context: 'inventory')
    node = SimpleNamespace(is_ready=True, is_cordoned=False, accelerator_type='RTX3060',
                           free={'accelerators_available': 2}, cpu_free=1, memory_free_gb=4)
    sky = SimpleNamespace(
        api_status=lambda **kw: [SimpleNamespace(status='SUCCEEDED')],
        get=lambda request: SimpleNamespace(node_info_dict={'node': node}),
    )
    deadline = time.monotonic() + 10
    with pytest.raises(RuntimeError, match='No RTX 3060 capacity'):
        held_cuda.verify_capacity(sky, 'k8s', deadline)
    node.cpu_free, node.memory_free_gb = 9, 20
    held_cuda.verify_capacity(sky, 'k8s', deadline)
