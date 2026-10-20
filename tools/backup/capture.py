"""Capture-only adapter for an owner-prepared coherent WQS snapshot.

Providers are explicit integration boundaries, not implemented trust services.
This module cannot produce a successful CD backup result.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import uuid

from .metadata import binding, canonical, digest, require, safe_path, timestamp, validate_record, verify_manifest


@contextmanager
def regular_file(root, relative):
    """Open beneath a pinned directory without following links at any component."""
    safe_path(relative)
    parent = os.dup(root)
    try:
        parts = relative.split('/')
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'regular unlinked-alias file required')
            yield stream
    finally:
        os.close(parent)


class CheckedReader:
    def __init__(self, stream):
        self.stream, self.hash, self.size = stream, hashlib.sha256(), 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.hash.update(data)
        self.size += len(data)
        return data

    def identity(self):
        return {'sha256': self.hash.hexdigest(), 'size': self.size}


def payload(root, manifest, destination):
    """Archive exactly inventoried bytes; reject content changes during copying."""
    with tarfile.open(destination, 'w', format=tarfile.PAX_FORMAT) as archive:
        data = canonical(manifest)
        info = tarfile.TarInfo('manifest.json')
        info.mode, info.size = 0o600, len(data)
        archive.addfile(info, io.BytesIO(data))
        for entry in manifest['inventory']:
            require(entry['path'] != 'manifest.json', 'reserved manifest path')
            with regular_file(root, entry['path']) as source:
                reader = CheckedReader(source)
                info = tarfile.TarInfo(entry['path'])
                info.mode, info.size = 0o600, entry['size']
                archive.addfile(info, reader)
                require(not source.read(1), 'file grew beyond inventory')
                require(reader.identity() == {k: entry[k] for k in ('sha256', 'size')}, 'snapshot content mismatch')


def file_digest(path):
    with open(path, 'rb') as source:
        reader = CheckedReader(source)
        while reader.read(1024 * 1024):
            pass
        return reader.identity()


def capture(source, manifest, expected_context, providers, work_dir, *, now=None):
    """Return only capture_readback after byte-exact storage readback.

    The coordinator supplies expected_context independently and owns the held,
    drained source boundary. Providers must implement validate_recipients,
    validate_boundary, encrypt, put and readback; production providers require
    separate review. No defaults, authority assertions or environment discovery.
    encrypt must encrypt the complete payload to all approved recipients. put
    must create a new object and return its opaque reference, never overwrite.
    """
    validator = validate_record
    if manifest.get('contract_version') == 2:
        from .v2 import validate_record as validator
    validator(manifest)
    verify_manifest(manifest)
    context = manifest['context']
    require(manifest['evidence_type'] == 'manifest' and context['set_type'] == 'wqs', 'WQS manifest required')
    require(canonical(context) == canonical(expected_context), 'independent context mismatch')
    deps = manifest['dependencies']
    if manifest['contract_version'] == 1:
        require(deps['parent_coordinated'] is True and deps['admission_held'] is True
                and deps['cd_enabled'] is False and deps['ledger_state'] == 'holding', 'held release required')
    # V2 flags describe the source. Actual source consistency is checked by the
    # configured provider below, not inferred from metadata flag values.
    members = context['recipients']
    require(members == sorted(members, key=lambda m: (m['type'], m['value'])), 'recipient ordering')
    for key in ('key_id', 'value', 'fingerprint'):
        require(len({m[key] for m in members}) == len(members), 'duplicate recipient')
    require(context['recipients_digest'] == digest(canonical(members)), 'recipient digest mismatch')
    for member in members:
        require(member['fingerprint'] == hashlib.sha256(member['value'].encode('ascii')).hexdigest(), 'recipient fingerprint')
    # Decoded public-key identity, checksums, plugins and independently approved
    # custody membership are the provider's responsibility; schema is insufficient.
    require(providers.validate_recipients(members) is True, 'recipient validation unavailable')
    root = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Coordinator validates database hold/drain, ledger, current PVC, complete
        # dependencies and source cursor. No flock is acquired by this child.
        require(providers.validate_boundary(root, manifest) is True, 'source boundary unverified')
        with tempfile.TemporaryDirectory(prefix='wqs-capture-', dir=work_dir) as temporary:
            private = Path(temporary)
            archive, encrypted, readback = (private / name for name in ('payload.tar', 'ciphertext.age', 'readback.age'))
            payload(root, manifest, archive)
            archive.chmod(0o600)
            providers.encrypt(archive, encrypted, members)
            require(encrypted.is_file() and not encrypted.is_symlink(), 'ciphertext unavailable')
            ciphertext = file_digest(encrypted)
            require(ciphertext['size'] > 0, 'empty ciphertext')
            object_ref = providers.put(encrypted)
            providers.readback(object_ref, readback)
            require(readback.is_file() and not readback.is_symlink(), 'readback unavailable')
            require(file_digest(readback) == ciphertext, 'ciphertext readback mismatch')
            require(providers.validate_boundary(root, manifest) is True, 'source boundary changed')
            created = (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')
            require(timestamp(context['boundary']['source_at']) <= timestamp(manifest['created_at']) <= timestamp(created), 'capture time ordering')
            receipt = {k: manifest[k] for k in ('schema_id', 'contract_version', 'canonical_profile', 'context')}
            receipt.update(record_id=uuid.uuid4().hex, evidence_type='capture_readback', created_at=created,
                           manifest_ref=binding(manifest), ciphertext=ciphertext, object_ref=object_ref,
                           outcome='readback_verified')
            validator(receipt)
            return receipt
    finally:
        os.close(root)


def unavailable_result():
    """Only supported final hook result until independent verification exists."""
    return {'evidence_type': 'final_result', 'verified': False, 'backup_id': None, 'failure': 'unavailable'}


if __name__ == '__main__':
    print(canonical(unavailable_result()).decode())
    raise SystemExit(1)
