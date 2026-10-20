"""Direct native filesystem load for an offline WQS destination.

No service is started and no credentials are generated. Stop destination writers
and checkpoint/remove SQLite sidecars before invoking this explicit restore.
"""
from contextlib import closing
import hashlib
import json
import io
import math
import zipfile
import os
from pathlib import Path
import re
import sqlite3

from cryptography.fernet import Fernet

from .metadata import require


MAX_NPZ_BYTES = 512 * 1024 * 1024


def validate_wqs(manifest, files):
    deps = manifest['dependencies']
    require(deps['kind'] == 'wqs', 'not a WQS backup')
    registry = json.loads(files[deps['invitation_registry']])
    require(isinstance(registry, list), 'invalid registry')
    ids = set()
    for user in registry:
        require(isinstance(user, dict) and isinstance(user.get('id'), str)
                and user['id'] not in ids and type(user.get('active')) is bool
                and isinstance(user.get('sha256'), str)
                and re.fullmatch('[a-f0-9]{64}', user['sha256']), 'invalid registry entry')
        ids.add(user['id'])
    cipher = Fernet(files[deps['pending_delivery_key']].strip())
    with closing(sqlite3.connect(':memory:')) as db:
        database = files[deps['database']]
        require(database.startswith(b'SQLite format 3\x00') and len(database) >= 100, 'invalid SQLite header')
        # SQLite documents this in-memory WAL deserialization workaround:
        # https://sqlite.org/c3ref/deserialize.html . Retained/restored bytes stay exact.
        memory_database = database[:18] + b'\x01\x01' + database[20:]
        db.deserialize(memory_database)
        db.row_factory = sqlite3.Row
        require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'invalid SQLite database')
        release = db.execute('SELECT release,smoke_owner FROM release_control WHERE singleton=1').fetchone()
        require(release is not None and isinstance(release['release'], str)
                and isinstance(release['smoke_owner'], str), 'invalid archived release state')
        results = {Path(p).stem for p in deps['results']}
        jobs = {row[0] for row in db.execute("SELECT id FROM jobs WHERE status='SUCCEEDED'")}
        require(results == jobs and all(re.fullmatch('[a-f0-9]{32}', job) for job in jobs),
                'result inventory does not match database')
        for row in db.execute('SELECT id,email,identity,token_hash,key_cipher FROM key_requests WHERE key_cipher IS NOT NULL'):
            value = json.loads(cipher.decrypt(row['key_cipher'].encode()))
            require(value['request_id'] == row['id'] and value['email'] == row['email']
                    and value['identity'] == row['identity']
                    and hashlib.sha256(value['key'].encode()).hexdigest() == row['token_hash']
                    and any(u['id'] == row['identity'] and u['sha256'] == row['token_hash'] for u in registry),
                    'pending delivery material mismatch')
    import numpy as np
    for path in deps['results']:
        value = files[path]
        with zipfile.ZipFile(io.BytesIO(value)) as archive:
            members = archive.infolist()
            require(bool(members) and len({m.filename for m in members}) == len(members)
                    and all(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*\\.npy', m.filename)
                            and not (m.flag_bits & 1) for m in members)
                    and sum(m.file_size for m in members) <= MAX_NPZ_BYTES,
                    'invalid or oversized NPZ members')
            for member in members:
                with archive.open(member) as array:
                    version = np.lib.format.read_magic(array)
                    require(version in ((1, 0), (2, 0)), 'unsupported native array format')
                    reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                              else np.lib.format.read_array_header_2_0)
                    shape, _, dtype = reader(array)
                    require(not dtype.hasobject and all(type(n) is int and n >= 0 for n in shape),
                            'unsafe native array dtype or shape')
                    require(math.prod(shape) * dtype.itemsize == member.file_size - array.tell(),
                            'native array shape exceeds member size')
        with np.load(io.BytesIO(value), allow_pickle=False) as result:
            require(bool(result.files), 'empty NPZ result')
            for name in result.files:
                require(isinstance(result[name], np.ndarray), 'invalid native result array')
    ledger = json.loads(files[deps['runner_ledger']])
    require(isinstance(ledger, dict) and ledger.get('status') == deps['ledger_state']
            and ledger.get('sha') == manifest['context']['scope']['release_sha'], 'invalid runner ledger')
    inventory = json.loads(files[deps['image_inventory']])
    require(isinstance(inventory, dict) and isinstance(inventory.get('images'), list)
            and bool(inventory['images']) and all(isinstance(ref, str) and
            re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}', ref) for ref in inventory['images']), 'invalid image inventory')
    require(isinstance(json.loads(files[deps['registry_access']]), dict), 'invalid registry access data')
    for path in deps['release_receipts']:
        receipt = json.loads(files[path])
        require(isinstance(receipt, dict) and receipt.get('status') == 'verified'
                and re.fullmatch('[a-f0-9]{40}', receipt.get('sha', ''))
                and Path(path).name == receipt['sha'] + '.json', 'invalid release receipt')


class NativeLoad:
    """Write and reopen the actual configured data/auth/runner destinations."""
    FIELDS = {'data_dir', 'state_dir', 'registry_file', 'delivery_key_file',
              'image_inventory_file', 'registry_access_file'}

    def __init__(self, config):
        require(isinstance(config, dict) and set(config) == self.FIELDS, 'explicit native destinations required')
        self.config = {name: Path(value) for name, value in config.items()}
        require(all(p.is_absolute() and p.resolve() == p for p in self.config.values()),
                'absolute nonsymlink destinations required')

    def destinations(self, manifest):
        deps, cfg = manifest['dependencies'], self.config
        result = {
            deps['database']: cfg['data_dir'] / 'jobs.sqlite3',
            deps['runner_ledger']: cfg['state_dir'] / 'deployment.json',
            deps['invitation_registry']: cfg['registry_file'],
            deps['pending_delivery_key']: cfg['delivery_key_file'],
            deps['image_inventory']: cfg['image_inventory_file'],
            deps['registry_access']: cfg['registry_access_file'],
        }
        for path in deps['results']:
            result[path] = cfg['data_dir'] / Path(path).stem / 'result.npz'
        for path in deps['release_receipts']:
            result[path] = cfg['state_dir'] / Path(path).name
        require(len(set(result.values())) == len(result), 'overlapping restore files')
        for path in result.values():
            require(path.resolve() == path, 'symlink destination')
            require(not any(other in path.parents for other in result.values()), 'overlapping restore paths')
            if path.exists():
                info = path.stat()
                require(path.is_file() and info.st_nlink == 1, 'nonregular destination')
        return result

    def load(self, manifest, files):
        validate_wqs(manifest, files)
        mapping = self.destinations(manifest)
        database = mapping[manifest['dependencies']['database']]
        require(not any(Path(str(database) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')),
                'destination SQLite sidecars present; stop writers and checkpoint first')
        # Validation and path preflight precede writes. A failed write may leave a
        # partial direct load; the caller must report failure, never success.
        for name, path in mapping.items():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            require(path.parent.resolve() == path.parent, 'symlink destination parent')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(fd, 'wb') as out:
                import stat
                info = os.fstat(out.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'nonregular destination')
                out.truncate(0)
                os.fchmod(out.fileno(), 0o600)
                out.write(files[name]); out.flush(); os.fsync(out.fileno())
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        loaded = {name: path.read_bytes() for name, path in mapping.items()}
        require(loaded == files, 'native load readback mismatch')
        validate_wqs(manifest, loaded)
        # Open the actual database in its native SQLite form; do not claim load
        # based solely on parsing the archive's in-memory copy.
        from urllib.parse import quote
        with closing(sqlite3.connect('file:' + quote(str(database), safe='/') + '?mode=ro', uri=True)) as db:
            require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'loaded SQLite integrity failure')
            db.execute('SELECT id FROM jobs LIMIT 1').fetchall()
