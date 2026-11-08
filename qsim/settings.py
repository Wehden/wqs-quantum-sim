import os
from pathlib import Path

DATA = Path(os.environ.get("QSIM_DATA", "var"))
TOKEN_FILE = Path(os.environ.get("QSIM_TOKEN_FILE", str(DATA / "users.json")))
CALLBACK_URL = os.environ.get("QSIM_CALLBACK_URL", "http://127.0.0.1:8080")
SKY_CONFIG = os.environ.get("QSIM_SKY_CONFIG", "deploy/sky-config.yaml")
WORKER_IMAGE = os.environ.get("QSIM_WORKER_IMAGE", "worker:local")
MAX_BODY = 16 * 1024 * 1024
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")


# Placement certification is separate from public numerical admission.
_certified_physics_profiles = {
    'operations': {'scqubits': {
        'spectrum': 'scqubits-cpu-evr-v1',
        'matrix_elements': 'scqubits-cpu-evr-v1',
    }},
    'worker': {
        'version': 1, 'id': 'physics-cpu-v1', 'backend': 'cpu',
        'policy_revision': 'physics-placement-v1',
        'algorithm_set_revision': 'physics-cpu-algorithms-v1',
        'resources': {'cpus': 2, 'memory': 4},
        'isolation': {'address_space_bytes': 3*1024**3, 'file_bytes': 12*1024**2,
                      'native_threads': 1, 'uid': 1000},
        'precision': ['float64', 'complex128'],
    },
}


def physics_execution_profile(engine: str, operation: str, *, admission_policy_revision: str):
    """Resolve a fresh server-owned profile without native imports or scheduling."""
    import copy
    import re
    if (_certified_physics_profiles['operations'].get(engine, {}).get(operation)
            != admission_policy_revision or not admission_policy_revision):
        raise ValueError('execution_policy_mismatch: uncertified physics operation')
    if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', WORKER_IMAGE) or WORKER_IMAGE.startswith('docker:'):
        raise ValueError('execution_policy_mismatch: immutable worker image required')
    infra = os.environ.get('QSIM_SKY_INFRA', 'k8s')
    if not re.fullmatch(r'[a-zA-Z0-9_./:-]{1,128}', infra):
        raise ValueError('execution_policy_mismatch: invalid scheduling infrastructure')
    profile = copy.deepcopy(_certified_physics_profiles['worker'])
    profile['image'] = WORKER_IMAGE
    profile['resources']['infra'] = infra
    return profile
