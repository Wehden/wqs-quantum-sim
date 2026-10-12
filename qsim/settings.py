import os
from pathlib import Path

DATA = Path(os.environ.get("QSIM_DATA", "var"))
TOKEN_FILE = Path(os.environ.get("QSIM_TOKEN_FILE", str(DATA / "users.json")))
CALLBACK_URL = os.environ.get("QSIM_CALLBACK_URL", "http://127.0.0.1:8080")
SKY_CONFIG = os.environ.get("QSIM_SKY_CONFIG", "deploy/sky-config.yaml")
WORKER_IMAGE = os.environ.get("QSIM_WORKER_IMAGE", "worker:local")
MAX_BODY = 16 * 1024 * 1024
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")
