"""Bounded capture orchestration for an approved mounted-source environment.

This returns capture evidence only. The caller still requires independent recovery
acceptance, and the CD-facing entry point remains unavailable/fail-closed.
"""
from pathlib import Path
import tempfile

from .capture import capture
from .metadata import require
from .providers import Providers


def capture_new_set(snapshot, encryption, storage, private_work_dir):
    budget = snapshot.budget
    require(encryption.budget is budget and storage.budget is budget, 'shared deadline required')
    work = Path(private_work_dir)
    require(work.is_absolute() and work.resolve() == work and work.is_dir(), 'private working directory required')
    require(work.stat().st_mode & 0o077 == 0, 'private working directory permissions required')
    with budget.enforced(), tempfile.TemporaryDirectory(prefix='wqs-snapshot-', dir=work) as staging:
        manifest = snapshot.create(Path(staging))
        providers = Providers(snapshot, encryption, storage)
        return capture(staging, manifest, manifest['context'], providers, work)
