import asyncio
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

import pytest

from qsim import adminweb, settings, store


def readonly_select(directory):
    # The subprocess cannot create WAL sidecars, including when tests run as root.
    def drop_root():
        if os.getuid() == 0:
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)

    code = """import json, sqlite3, sys
db = sqlite3.connect('file:' + sys.argv[1] + '/jobs.sqlite3?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
try:
    print(json.dumps({'rows': db.execute('SELECT value FROM sample').fetchall()}))
except sqlite3.OperationalError as error:
    print(json.dumps({'error': error.sqlite_errorname}))
finally:
    db.close()
"""
    # venv interpreters can live beneath private directories; use the same base Python.
    python = getattr(sys, '_base_executable', sys.executable)
    result = subprocess.run([python, '-c', code, str(directory)], check=True,
                            capture_output=True, text=True, preexec_fn=drop_root)
    return json.loads(result.stdout)


@pytest.fixture
def database(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='wqs-wal-test-') as name:
        path = Path(name)
        path.chmod(0o755)
        monkeypatch.setattr(settings, 'DATA', path)
        with contextlib.closing(sqlite3.connect(path / 'jobs.sqlite3')) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE sample(value INTEGER)')
            db.commit()
        (path / 'jobs.sqlite3').chmod(0o644)
        try:
            yield path
        finally:
            path.chmod(0o755)


def sidecars(path):
    return [Path(str(path / 'jobs.sqlite3') + suffix) for suffix in ('-wal', '-shm')]


def test_anchor_retains_live_readonly_access_across_writes_and_checkpoint(database):
    assert not any(p.exists() for p in sidecars(database))
    database.chmod(0o555)
    assert readonly_select(database)['error'] in {'SQLITE_CANTOPEN', 'SQLITE_READONLY_DIRECTORY'}
    database.chmod(0o755)
    with store.keep_wal_available():
        assert all(p.exists() for p in sidecars(database))
        for p in sidecars(database):
            p.chmod(0o644)
        database.chmod(0o555)
        assert readonly_select(database) == {'rows': []}
        with contextlib.closing(sqlite3.connect(database / 'jobs.sqlite3')) as writer:
            writer.execute('INSERT INTO sample VALUES(42)')
            writer.commit()
            checkpoint = writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            assert checkpoint[0] == 0  # The anchor does not hold a read transaction.
        assert readonly_select(database) == {'rows': [[42]]}
        assert all(p.exists() for p in sidecars(database))
        database.chmod(0o755)
    assert not any(p.exists() for p in sidecars(database))


def test_anchor_is_query_only_without_transaction_and_closes_on_error(database, monkeypatch):
    original = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        connections.append(db)
        return db

    monkeypatch.setattr(store.sqlite3, 'connect', connect)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with store.keep_wal_available():
            db = connections[-1]
            assert db.in_transaction is False
            assert db.execute('PRAGMA query_only').fetchone() == (1,)
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                db.execute('INSERT INTO sample VALUES(99)')
            raise RuntimeError('synthetic failure')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
    assert not any(p.exists() for p in sidecars(database))


@pytest.mark.parametrize('fail', [False, True])
@pytest.mark.parametrize('notify', ['0', '1'])
def test_admin_lifespan_retains_anchor_and_cleans_single_worker(database, monkeypatch, fail, notify):
    monkeypatch.setattr(store, 'init', lambda: None)
    monkeypatch.setattr(adminweb, 'signer', lambda: None)
    monkeypatch.setattr(adminweb, 'cipher', lambda: None)
    monkeypatch.setenv('QSIM_NOTIFY', notify)
    worker = []

    async def notification_loop():
        worker.append('started')
        try:
            await asyncio.Event().wait()
        finally:
            worker.append('stopped')

    monkeypatch.setattr(adminweb, 'notification_loop', notification_loop)

    async def exercise():
        async with adminweb.lifespan(adminweb.app):
            await asyncio.sleep(0)
            assert all(p.exists() for p in sidecars(database))
            assert worker == (['started'] if notify == '1' else [])
            if fail:
                raise RuntimeError('lifespan failure')

    if fail:
        with pytest.raises(RuntimeError, match='lifespan failure'):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert worker == (['started', 'stopped'] if notify == '1' else [])
    assert not any(p.exists() for p in sidecars(database))
