"""Configured v2 creation hook. Outputs one sanitized result; never restores."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .coordinator import capture_new_set
from .metadata import canonical, digest, require
from .providers import AgeEncryption, Budget, S3Storage, s3_client
from .remote_snapshot import KubernetesSource, RemoteSnapshot
from .snapshot import SecretReader, read_private, require_parent_lock


class RecordedStorage(S3Storage):
    def put(self, ciphertext):
        ref = super().put(ciphertext)
        version = self.objects[ref][1]
        require(isinstance(version, str) and version.strip() not in ('', 'null'),
                'versioned object required for durable recovery locator')
        return ref

    def readback(self, ref, destination):
        super().readback(ref, destination)
        with destination.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        key, version, size = self.objects[ref]
        self.receipt = {'bucket': self.bucket, 'key': key, 'version': version, 'size': size, 'sha256': sha}


def persist_locator(state, set_id, receipt):
    """Commit a new private locator durably before reporting created:true."""
    import re
    require(isinstance(set_id, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', set_id), 'invalid locator ID')
    directory = Path(state) / 'backup-locators'
    directory.mkdir(mode=0o700, exist_ok=True)
    require(directory.resolve() == directory and directory.stat().st_mode & 0o077 == 0, 'private locator directory required')
    target = directory / (set_id + '.json')
    fd, temporary = tempfile.mkstemp(prefix='.locator-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical({'backup_id': set_id, **receipt}))
            stream.flush(); os.fsync(stream.fileno())
        # No overwrite, even for a retry with a reused ID.
        os.link(temporary, target)
        os.unlink(temporary)
        parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        # Persist creation of the locator directory itself as well.
        parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configured_capture(config, set_id):
    import re
    require(isinstance(set_id, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', set_id), 'invalid requested ID')
    require(set(config) == {'namespace', 'deployment', 'container', 'data',
                           'registry_secret', 'delivery_secret', 'state_dir', 'registry_access',
                           'work_dir', 'cd_enabled', 'recipients_file', 'age', 'plugin', 'storage',
                           'retention_ref', 'app_id', 'org_id'}, 'complete backup configuration required')
    state = Path(config['state_dir'])
    require(state.is_absolute() and state.resolve() == state, 'absolute state directory required')
    # Open the actual parent's lock inode without taking a second flock.
    lock = os.open(state / 'lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require_parent_lock(lock, state)
        ledger = json.loads(read_private(state / 'deployment.json'))
        budget = Budget()
        source = KubernetesSource(config['namespace'], config['deployment'], config['container'],
                                  config['data'], None, budget)
        # Kubernetes Secret projections use atomic version symlinks. Resolve the
        # explicitly configured input once, then read that immutable version.
        recipients = json.loads(read_private(Path(config['recipients_file']).resolve(strict=True)))
        context = {'set_id': set_id, 'set_type': 'wqs', 'format_version': 1,
                   'scope': {'kind': 'application', 'app_id': config['app_id'], 'org_id': config['org_id'],
                             'release_sha': ledger['sha']}, 'source': source.identity(),
                   'recipients': recipients, 'recipients_digest': digest(canonical(recipients))}
        def secret(key):
            record = config[key]
            require(set(record) == {'name', 'key'}, 'named Secret key required')
            return SecretReader(config['namespace'], record['name'], record['key'], budget)
        snapshot = RemoteSnapshot(source, state, secret('registry_secret'), secret('delivery_secret'),
                                  Path(config['registry_access']).resolve(strict=True), context, config['retention_ref'], budget, lock,
                                  cd_enabled=config['cd_enabled'])
        age = config['age']
        require(set(age) == {'path', 'sha256'}, 'pinned age configuration required')
        encryption = AgeEncryption(recipients, age['path'], age['sha256'], budget, config['plugin'])
        storage = config['storage']
        require(set(storage) == {'endpoint', 'region', 'credential_file', 'bucket', 'prefix'}, 'complete storage configuration required')
        client = s3_client(storage['endpoint'], storage['region'], storage['credential_file'])
        destination = RecordedStorage(client, storage['bucket'], storage['prefix'], budget)
        result = capture_new_set(snapshot, encryption, destination, config['work_dir'], set_id)
        require_parent_lock(lock, state)
        persist_locator(state, set_id, {'endpoint': storage['endpoint'], **destination.receipt})
        return result
    finally:
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = configured_capture(json.loads(read_private(args.config.resolve(strict=True))), os.environ.get('WQS_BACKUP_SET_ID'))
    except Exception as error:
        failure = 'timeout' if isinstance(error, TimeoutError) else 'unavailable'
        print(json.dumps({'evidence_type': 'creation_result', 'created': False, 'backup_id': None, 'failure': failure}))
        raise SystemExit(1)
    print(canonical(result).decode())


if __name__ == '__main__':
    main()
