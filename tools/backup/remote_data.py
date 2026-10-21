"""Fixed, standard-library-only application exporter for Kubernetes exec.

This file is sent verbatim to the pinned API container. It never claims to hold
the release parent's flock and never reads recovery identities or storage keys.
"""
from contextlib import closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time
from urllib.parse import quote

_cancelled = False


class DeadlineWriter:
    """Prevent TAR finalization from blocking again after timeout/disconnect."""
    def __init__(self, fd, deadline):
        self.fd, self.deadline = fd, deadline
        os.set_blocking(fd, False)

    def write(self, value):
        remaining = memoryview(value)
        while remaining:
            wait = self.deadline - time.monotonic()
            if _cancelled or wait <= 0 or not select.select([], [self.fd], [], wait)[1]:
                raise TimeoutError('export output deadline')
            try:
                count = os.write(self.fd, remaining)
            except BlockingIOError:
                continue
            remaining = remaining[count:]
        return len(value)

    def flush(self):
        pass


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def held(db, sha):
    row = db.execute('SELECT release,smoke_owner FROM release_control WHERE singleton=1').fetchone()
    require(row is not None and tuple(row) == (sha, ''), 'matching admission hold required')
    require(db.execute('SELECT COUNT(*) FROM jobs WHERE cleaned=0').fetchone()[0] == 0, 'jobs not drained')
    require(db.execute("SELECT COUNT(*) FROM key_requests WHERE status='APPROVING' OR notification_state='SENDING' OR delivery_state='SENDING' OR notification_lease IS NOT NULL OR delivery_lease IS NOT NULL").fetchone()[0] == 0,
            'approval or delivery in flight')


def fingerprint(db):
    value = hashlib.sha256()
    for statement in db.iterdump():
        value.update(statement.encode())
        value.update(b'\n')
    return value.hexdigest()


@contextmanager
def result_file(data, job_id):
    require(re.fullmatch('[a-f0-9]{32}', job_id), 'invalid job ID')
    root = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = None
    try:
        directory = os.open(job_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        fd = os.open('result.npz', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'nonregular result')
            yield stream
    finally:
        if directory is not None:
            os.close(directory)
        os.close(root)


def source_state(db, data, max_bytes):
    results, total = [], 0
    for (job_id,) in db.execute("SELECT id FROM jobs WHERE status='SUCCEEDED' ORDER BY id"):
        digest, size = hashlib.sha256(), 0
        with result_file(data, job_id) as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk); total += len(chunk)
                require(total <= max_bytes, 'results exceed limit')
                digest.update(chunk)
        results.append([job_id, size, digest.hexdigest()])
    return hashlib.sha256(canonical({'database': fingerprint(db), 'results': results})).hexdigest()


def export(data, sha, destination, max_bytes):
    """Create data-only archive or return the current held database fingerprint."""
    data = Path(data)
    database = data / 'jobs.sqlite3'
    require(data.is_absolute() and data.resolve() == data and database.resolve() == database,
            'absolute non-symlink database required')
    require(re.fullmatch('[a-f0-9]{40}', sha), 'release SHA required')
    uri = 'file:' + quote(str(database), safe='/')
    with closing(sqlite3.connect(uri + '?mode=rw', uri=True, timeout=5)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            held(db, sha)
            require(db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
                    <= max_bytes, 'database exceeds limit')
            identity = source_state(db, data, max_bytes)
            if destination is None:
                return {'fingerprint': identity, 'data': str(data)}
            with tempfile.TemporaryDirectory(prefix='wqs-export-') as temporary:
                root = Path(temporary)
                snapshot = root / 'database.sqlite3'
                with closing(sqlite3.connect(uri + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
                    page_size = source.execute('PRAGMA page_size').fetchone()[0]
                    def progress(status, remaining, total):
                        require(total * page_size <= max_bytes, 'database exceeds limit')
                    source.backup(target, pages=256, progress=progress)
                    require(target.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'invalid database')
                snapshot.chmod(0o600)
                inventory = []
                total = 0
                def capture(path, role, stream):
                    nonlocal total
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    digest = hashlib.sha256()
                    size = 0
                    with target.open('xb') as out:
                        os.chmod(target, 0o600)
                        while chunk := stream.read(1024 * 1024):
                            size += len(chunk)
                            total += len(chunk)
                            require(total <= max_bytes, 'snapshot exceeds limit')
                            digest.update(chunk)
                            out.write(chunk)
                    inventory.append({'path': path, 'kind': 'regular', 'role': role,
                                      'size': size, 'sha256': digest.hexdigest()})
                # Move through the same bounded inventory path as result files.
                database_copy = root / 'sqlite-copy'
                snapshot.rename(database_copy)
                with database_copy.open('rb') as stream:
                    capture('database.sqlite3', 'database', stream)
                database_copy.unlink()
                for (job_id,) in db.execute("SELECT id FROM jobs WHERE status='SUCCEEDED' ORDER BY id"):
                    with result_file(data, job_id) as stream:
                        capture('results/' + job_id + '.npz', 'results', stream)
                require(source_state(db, data, max_bytes) == identity, 'source changed during export')
                metadata = canonical({'fingerprint': identity, 'inventory': inventory, 'data': str(data)})
                with tarfile.open(fileobj=destination, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                    info = tarfile.TarInfo('source.json'); info.size = len(metadata); info.mode = 0o600
                    archive.addfile(info, io.BytesIO(metadata))
                    for entry in inventory:
                        with (root / entry['path']).open('rb') as stream:
                            info = tarfile.TarInfo(entry['path']); info.size = entry['size']; info.mode = 0o600
                            archive.addfile(info, stream)
                held(db, sha)
                return {'fingerprint': identity, 'data': str(data)}
        finally:
            db.rollback()


def main():
    def timeout(*_):
        global _cancelled
        _cancelled = True
        raise TimeoutError('export deadline')
    signal.signal(signal.SIGALRM, timeout)
    signal.signal(signal.SIGTERM, timeout)
    signal.alarm(60)  # Also bound reading the small request.
    # The parent derives our absolute deadline from this monotonic timestamp,
    # after exec startup. No synchronized wall clocks or startup grace assumption.
    print(json.dumps({'ready': int(time.monotonic() * 1000)}), flush=True)
    request = json.loads(sys.stdin.buffer.read(4097))
    require(set(request) == {'action', 'sha', 'deadline', 'max_bytes'}, 'invalid export request')
    require(request['action'] in ('export', 'validate'), 'invalid action')
    remaining = request['deadline'] / 1000 - time.monotonic()
    require(type(request['deadline']) is int and 0 < remaining <= 480, 'invalid deadline')
    require(type(request['max_bytes']) is int and 1 <= request['max_bytes'] <= 512 * 1024 * 1024, 'invalid size limit')
    signal.setitimer(signal.ITIMER_REAL, remaining)
    from qsim import settings
    if request['action'] == 'export':
        export(settings.DATA, request['sha'], DeadlineWriter(sys.stdout.fileno(), request['deadline'] / 1000), request['max_bytes'])
    else:
        print(json.dumps(export(settings.DATA, request['sha'], None, request['max_bytes'])))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # No traceback containing source paths, database values or private data.
        raise SystemExit(1)
