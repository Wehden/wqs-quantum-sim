"""Coherent mounted-volume WQS snapshot under a parent's existing release lock.

Run only in an approved capture environment with access to the current PVC and
runner state. Does not stop workloads, alter admission, rotate keys or acquire
another flock. Kubernetes reads are explicitly named and never logged.
"""
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import io
import os
from pathlib import Path
import re
import sqlite3
import socket
import subprocess
from urllib.parse import quote

from cryptography.fernet import Fernet

from .capture import file_digest, regular_file
from .metadata import canonical, digest, require, validate_record, verify_manifest


class SecretReader:
    def __init__(self, namespace, name, key, budget):
        require(all(re.fullmatch('[a-z0-9][a-z0-9.-]*', x) for x in (namespace, name)), 'invalid secret reference')
        self.namespace, self.name, self.key, self.budget = namespace, name, key, budget

    def read(self):
        raw = subprocess.run(['kubectl', '-n', self.namespace, 'get', 'secret', self.name, '-o', 'json'],
                             capture_output=True, check=True, timeout=self.budget.remaining(15)).stdout
        value = json.loads(raw)
        return ((value['metadata']['uid'], value['metadata']['resourceVersion']),
                base64.b64decode(value['data'][self.key], validate=True))


class MountedVolume:
    """Bind the executing capture pod's mount to an independently expected PVC."""
    def __init__(self, namespace, pod, container, budget):
        require(all(re.fullmatch('[a-z0-9][a-z0-9.-]*', x) for x in (namespace, pod, container)), 'invalid source workload reference')
        self.namespace, self.pod, self.container, self.budget = namespace, pod, container, budget

    def get(self, kind, name):
        raw = subprocess.run(['kubectl', '-n', self.namespace, 'get', kind, name, '-o', 'json'],
                             capture_output=True, check=True, timeout=self.budget.remaining(15)).stdout
        return json.loads(raw)

    def verify(self, context, data):
        pod = self.get('pod', self.pod)
        require(socket.gethostname() == pod['spec'].get('hostname', self.pod), 'capture must execute in configured source pod')
        container = next(c for c in pod['spec']['containers'] if c['name'] == self.container)
        mount = next(m for m in container['volumeMounts'] if m['mountPath'] == str(data))
        require(not mount.get('subPath') and not mount.get('subPathExpr'), 'whole PVC mount required')
        volume = next(v for v in pod['spec']['volumes'] if v['name'] == mount['name'])
        pvc_name = volume['persistentVolumeClaim']['claimName']
        pvc = self.get('pvc', pvc_name)
        require(pvc['status']['phase'] == 'Bound', 'source PVC not bound')
        require(context['source']['storage'] == f"{self.namespace}/{pvc_name}@{pvc['metadata']['uid']}"
                and context['source']['workload'] == f"{self.namespace}/{self.pod}@{pod['metadata']['uid']}", 'source identity mismatch')
        image = container['image']
        require(re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}', image)
                and context['source']['image_or_executable_digest'] == image.rsplit(':', 1)[1], 'source image mismatch')
        require(context['source']['revision'] == pod['metadata'].get('annotations', {}).get('wqsim-release-sha'), 'source revision mismatch')
        return True


def read_private(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'trusted absolute source path required')
    root = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with regular_file(root, path.name) as source:
            return source.read()
    finally:
        os.close(root)


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as out:
        out.write(value)
        out.flush()
        os.fsync(out.fileno())


def held(db, sha):
    row = db.execute('SELECT release,smoke_owner FROM release_control WHERE singleton=1').fetchone()
    require(row is not None and tuple(row) == (sha, ''), 'matching nonsmoke admission hold required')
    require(db.execute('SELECT COUNT(*) FROM jobs WHERE cleaned=0').fetchone()[0] == 0, 'jobs not drained')
    require(db.execute("SELECT COUNT(*) FROM key_requests WHERE status='APPROVING' OR notification_state='SENDING' OR delivery_state='SENDING' OR notification_lease IS NOT NULL OR delivery_lease IS NOT NULL").fetchone()[0] == 0, 'approval or delivery in flight')


def validate_key(db, registry, key):
    users = json.loads(registry)
    require(isinstance(users, list) and len({u['id'] for u in users}) == len(users), 'invalid registry')
    for user in users:
        require(isinstance(user['id'], str) and re.fullmatch('[a-f0-9]{64}', user['sha256'])
                and type(user['active']) is bool, 'invalid registry entry')
    cipher = Fernet(key.strip())
    for row in db.execute('SELECT id,email,identity,token_hash,key_cipher FROM key_requests WHERE key_cipher IS NOT NULL'):
        payload = json.loads(cipher.decrypt(row['key_cipher'].encode()))
        require(payload['request_id'] == row['id'] and payload['email'] == row['email']
                and payload['identity'] == row['identity']
                and hashlib.sha256(payload['key'].encode()).hexdigest() == row['token_hash'], 'delivery material mismatch')
        require(any(u['id'] == row['identity'] and u['sha256'] == row['token_hash'] for u in users), 'pending delivery absent from registry')


def images(ledger):
    refs = set(ledger['images'].values())
    for role in ledger['previous'].values():
        refs.add(role['image'])
        if role.get('worker_image'):
            refs.add(role['worker_image'])
    require(all(isinstance(ref, str) and re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}', ref) for ref in refs), 'immutable image dependencies required')
    return sorted(refs)


def require_parent_lock(fd, state):
    current = os.fstat(fd)
    expected = (state / 'lock').stat()
    require((current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino), 'wrong parent lock descriptor')
    for row in Path('/proc/locks').read_text().splitlines():
        fields = row.split()
        if len(fields) < 6 or fields[1:4] != ['FLOCK', 'ADVISORY', 'WRITE']:
            continue
        device = fields[5].split(':')
        if (int(fields[4]) in (os.getpid(), os.getppid()) and len(device) == 3
                and (int(device[0], 16), int(device[1], 16), int(device[2])) ==
                (os.major(current.st_dev), os.minor(current.st_dev), current.st_ino)):
            return
    raise ValueError('parent release lock is not held')


class Snapshot:
    def __init__(self, data, state, registry, delivery_key, registry_access, context, retention_ref, budget, parent_lock_fd, volume, max_bytes=512 * 1024 * 1024):
        self.data, self.state = Path(data), Path(state)
        self.registry, self.delivery_key = registry, delivery_key
        self.registry_access, self.context, self.retention_ref, self.budget = Path(registry_access), context, retention_ref, budget
        self.lock_fd, self.max_bytes, self.volume = parent_lock_fd, max_bytes, volume
        self.expected = None
        self.ledger_bytes = None
        self.source_versions = None
        self.database_digest = None

    def create(self, destination):
        """Parent must keep its lock throughout this call and subsequent capture."""
        require_parent_lock(self.lock_fd, self.state)
        require(self.volume.verify(self.context, self.data) is True, 'current source volume not verified')
        root = Path(destination)
        require(root.is_dir() and not list(root.iterdir()) and root.resolve() == root, 'empty private staging directory required')
        root.chmod(0o700)
        sha = self.context['scope']['release_sha']
        require(re.fullmatch('[a-f0-9]{40}', sha), 'release SHA required')
        self.ledger_bytes = read_private(self.state / 'deployment.json')
        ledger = json.loads(self.ledger_bytes)
        require(ledger['sha'] == sha and ledger['status'] == 'holding' and 'backup_id' not in ledger, 'contemporaneous holding ledger required')
        inventory = []
        total_bytes = 0
        deps = {'kind': 'wqs', 'pvc_identity': self.context['source']['storage'], 'parent_coordinated': True,
                'ledger_state': 'holding', 'admission_held': True, 'cd_enabled': False, 'results': [], 'release_receipts': []}

        def add_stream(role, path, source):
            nonlocal total_bytes
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            hasher, size = hashlib.sha256(), 0
            with open(destination, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as out:
                while True:
                    self.budget.remaining()
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    total_bytes += len(block)
                    require(total_bytes <= self.max_bytes, 'snapshot exceeds size policy')
                    hasher.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            inventory.append({'path': path, 'kind': 'regular', 'role': role, 'sha256': hasher.hexdigest(), 'size': size})
            if role in ('results', 'release_receipts'):
                deps[role].append(path)
            else:
                deps[role] = path

        def add(role, path, value):
            add_stream(role, path, io.BytesIO(value))

        database = self.data / 'jobs.sqlite3'
        require(database.is_absolute() and database.resolve() == database and database.is_file(), 'current mounted database required')
        uri = 'file:' + quote(str(database), safe='/')
        with closing(sqlite3.connect(uri + '?mode=rw', uri=True, timeout=self.budget.remaining(5))) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            try:
                held(db, sha)
                registry_before, key_before = self.registry.read(), self.delivery_key.read()
                validate_key(db, registry_before[1], key_before[1])
                snapshot = root / 'database.sqlite3'
                with closing(sqlite3.connect(uri + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
                    page_size = source.execute('PRAGMA page_size').fetchone()[0]
                    def progress(status, remaining, total):
                        self.budget.remaining()
                        require(total * page_size <= self.max_bytes, 'database exceeds size policy')
                    source.backup(target, pages=256, progress=progress)
                    require(target.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'database integrity check failed')
                snapshot.chmod(0o600)
                total_bytes += snapshot.stat().st_size
                require(total_bytes <= self.max_bytes, 'database exceeds size policy')
                deps['database'] = snapshot.name
                inventory.append({'path': snapshot.name, 'kind': 'regular', 'role': 'database', **file_digest(snapshot)})
                data_fd = os.open(self.data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    for row in db.execute("SELECT id FROM jobs WHERE status='SUCCEEDED' ORDER BY id"):
                        require(re.fullmatch('[a-f0-9]{32}', row['id']), 'invalid stored job ID')
                        with regular_file(data_fd, row['id'] + '/result.npz') as artifact:
                            require(os.fstat(artifact.fileno()).st_size <= self.max_bytes - total_bytes, 'artifact exceeds size policy')
                            add_stream('results', 'results/' + row['id'] + '.npz', artifact)
                finally:
                    os.close(data_fd)
                add('invitation_registry', 'registry.json', registry_before[1])
                add('pending_delivery_key', 'delivery-key', key_before[1])
                add('runner_ledger', 'runner/deployment.json', self.ledger_bytes)
                for receipt in sorted(self.state.glob('*.json')):
                    if receipt.name == 'deployment.json':
                        continue
                    # Only immutable per-release success receipts; acceptance diagnostics
                    # and other operational files are not successful release receipts.
                    if not re.fullmatch(r'[a-f0-9]{40}\.json', receipt.name):
                        continue
                    raw = read_private(receipt)
                    value = json.loads(raw)
                    require(value['status'] == 'verified' and value['sha'] + '.json' == receipt.name, 'invalid retained receipt')
                    add('release_receipts', 'runner/' + receipt.name, raw)
                require(isinstance(self.retention_ref, str) and self.retention_ref, 'approved image retention reference required')
                add('image_inventory', 'images.json', canonical({'images': images(ledger), 'retention_ref': self.retention_ref}))
                access = read_private(self.registry_access)
                require(isinstance(json.loads(access), dict), 'registry access configuration required')
                add('registry_access', 'registry-access.json', access)
                require(self.registry.read() == registry_before and self.delivery_key.read() == key_before, 'secret changed across snapshot')
                require(read_private(self.state / 'deployment.json') == self.ledger_bytes, 'parent ledger changed')
                held(db, sha)
                self.source_versions = (registry_before, key_before)
                self.database_digest = self.database_fingerprint(db)
                source_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            finally:
                db.rollback()
        # Bind the actual complete snapshot bytes and source versions, not a count
        # or arbitrary clock. Only the hash leaves the encrypted manifest.
        cursor = digest(canonical({'inventory': inventory, 'registry_version': list(registry_before[0]),
                                   'key_version': list(key_before[0])}))['sha256']
        context = {**self.context, 'boundary': {'method': 'wqs-sqlite-write-lock-v1', 'cursor': cursor, 'source_at': source_at}}
        manifest = {'schema_id': 'backup-recovery-set-v1', 'contract_version': 1, 'canonical_profile': 'backup-json-v1',
                    'record_id': 'manifest-' + context['set_id'], 'evidence_type': 'manifest', 'context': context,
                    'created_at': source_at, 'inventory': inventory, 'dependencies': deps}
        validate_record(manifest)
        verify_manifest(manifest)
        self.expected = canonical(manifest)
        return manifest

    def database_fingerprint(self, db):
        fingerprint = hashlib.sha256()
        for statement in db.iterdump():
            self.budget.remaining()
            fingerprint.update(statement.encode())
            fingerprint.update(b'\n')
        return fingerprint.digest()

    def validate_source(self):
        uri = 'file:' + quote(str(self.data / 'jobs.sqlite3'), safe='/')
        with closing(sqlite3.connect(uri + '?mode=rw', uri=True, timeout=self.budget.remaining(5))) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                held(db, self.context['scope']['release_sha'])
                require(self.database_fingerprint(db) == self.database_digest, 'database changed across capture')
                require((self.registry.read(), self.delivery_key.read()) == self.source_versions,
                        'secret changed across capture')
            finally:
                db.rollback()

    def validate(self, root, manifest):
        require_parent_lock(self.lock_fd, self.state)
        require(self.volume.verify(self.context, self.data) is True, 'source volume changed')
        require(self.expected is not None and canonical(manifest) == self.expected, 'snapshot manifest changed')
        require(read_private(self.state / 'deployment.json') == self.ledger_bytes, 'parent ledger changed')
        for entry in manifest['inventory']:
            with regular_file(root, entry['path']) as source:
                hasher, size = hashlib.sha256(), 0
                while True:
                    self.budget.remaining()
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    require(size <= entry['size'], 'staged snapshot grew')
                    hasher.update(block)
                require({'sha256': hasher.hexdigest(), 'size': size} == {k: entry[k] for k in ('sha256', 'size')}, 'staged snapshot changed')
        self.validate_source()
        self.budget.remaining()
        return True
