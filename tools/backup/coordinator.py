"""Bounded capture orchestration for an approved mounted-source environment.

Returns v2 creation results after encrypted storage readback. Direct restore is
a separate explicit operation; this coordinator never loads a destination.
"""
from pathlib import Path
import tempfile

from .create import create
from .metadata import require
from .providers import Providers


def capture_new_set(snapshot, encryption, storage, private_work_dir, requested_id):
    # The configured caller supplies the CD hook's WQS_BACKUP_SET_ID value.
    require(isinstance(requested_id, str) and bool(requested_id), 'requested backup ID required')
    snapshot.context = {**snapshot.context, 'set_id': requested_id}
    budget = snapshot.budget
    require(encryption.budget is budget and storage.budget is budget, 'shared deadline required')
    work = Path(private_work_dir)
    require(work.is_absolute() and work.resolve() == work and work.is_dir(), 'private working directory required')
    require(work.stat().st_mode & 0o077 == 0, 'private working directory permissions required')
    with budget.enforced(), tempfile.TemporaryDirectory(prefix='wqs-snapshot-', dir=work) as staging:
        manifest = snapshot.create(Path(staging))
        providers = Providers(snapshot, encryption, storage)
        return create(staging, manifest, providers, work, requested_id)
