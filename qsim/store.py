import contextlib
import json
import sqlite3
from . import settings


def init():
    settings.DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("CREATE TABLE IF NOT EXISTS release_control (singleton INTEGER PRIMARY KEY CHECK(singleton=1), release TEXT NOT NULL, smoke_owner TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO release_control VALUES (1,'','')")
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
            status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
            request TEXT NOT NULL, callback_hash TEXT NOT NULL, started REAL,
            error TEXT, summary TEXT, cleaned INTEGER NOT NULL DEFAULT 0)''')
        db.execute("""CREATE TABLE IF NOT EXISTS job_idempotency (
            owner TEXT NOT NULL, key_hash TEXT NOT NULL, request_hash TEXT NOT NULL,
            job_id TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY(owner,key_hash))""")
        db.execute("CREATE INDEX IF NOT EXISTS job_idempotency_job ON job_idempotency(job_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS job_events (
            job_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(job_id,sequence))""")
        payload = "json_object('id',NEW.id,'name',NEW.name,'status',NEW.status,'created',NEW.created,'updated',NEW.updated,'error',NEW.error,'summary',json(NEW.summary))"
        for trigger, condition in (("insert", "AFTER INSERT ON jobs"),
                                   ("update", "AFTER UPDATE OF status ON jobs WHEN OLD.status != NEW.status")):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS jobs_event_{trigger} {condition}
                BEGIN INSERT INTO job_events(job_id,sequence,payload)
                VALUES (NEW.id,(SELECT COALESCE(MAX(sequence),0)+1 FROM job_events WHERE job_id=NEW.id),{payload}); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_event_delete AFTER DELETE ON jobs
            BEGIN DELETE FROM job_events WHERE job_id=OLD.id; END""")
        # Older jobs have a current-state snapshot; only future transitions are replayable.
        db.execute("""INSERT INTO job_events(job_id,sequence,payload)
            SELECT id,1,json_object('id',id,'name',name,'status',status,'created',created,
            'updated',updated,'error',error,'summary',json(summary)) FROM jobs
            WHERE NOT EXISTS (SELECT 1 FROM job_events WHERE job_id=jobs.id)""")
        db.execute("CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner, created)")
        db.execute("""CREATE TABLE IF NOT EXISTS job_dispatch (
            job_id TEXT PRIMARY KEY, launch_id TEXT, down_id TEXT, cancel_id TEXT,
            intent TEXT, intent_at REAL, retry_pending INTEGER NOT NULL DEFAULT 0,
            retry_after REAL NOT NULL DEFAULT 0)""")
        columns = {r['name'] for r in db.execute('PRAGMA table_info(job_dispatch)')}
        for name, declaration in {'cluster':'TEXT','method':"TEXT NOT NULL DEFAULT 'launch'"}.items():
            if name not in columns:
                db.execute(f'ALTER TABLE job_dispatch ADD COLUMN {name} {declaration}')
        db.execute("""CREATE TABLE IF NOT EXISTS worker_pool (
            cluster TEXT PRIMARY KEY, job_id TEXT UNIQUE, created REAL NOT NULL,
            released REAL, retired INTEGER NOT NULL DEFAULT 0)""")
        columns = {r["name"] for r in db.execute("PRAGMA table_info(worker_pool)")}
        if "image" not in columns:
            db.execute("ALTER TABLE worker_pool ADD COLUMN image TEXT")
        db.execute("""CREATE TABLE IF NOT EXISTS key_requests (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
            purpose TEXT NOT NULL, created REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING', identity TEXT,
            token_hash TEXT, reviewed REAL, key_cipher TEXT, revealed REAL,
            notification_state TEXT NOT NULL DEFAULT 'PENDING',
            notification_attempts INTEGER NOT NULL DEFAULT 0,
            notification_next REAL NOT NULL DEFAULT 0,
            notification_lease REAL, notification_error TEXT, notified REAL,
            delivery_state TEXT NOT NULL DEFAULT 'NONE', delivery_attempts INTEGER NOT NULL DEFAULT 0,
            delivery_next REAL NOT NULL DEFAULT 0, delivery_lease REAL, delivery_error TEXT, delivered REAL)""")
        columns = {r['name'] for r in db.execute('PRAGMA table_info(key_requests)')}
        additions = {'identity':'TEXT','token_hash':'TEXT','reviewed':'REAL','key_cipher':'TEXT','revealed':'REAL'}
        for prefix, default in (('notification','PENDING'),('delivery','NONE')):
            additions.update({prefix+'_state':f"TEXT NOT NULL DEFAULT '{default}'", prefix+'_attempts':'INTEGER NOT NULL DEFAULT 0',prefix+'_next':'REAL NOT NULL DEFAULT 0',prefix+'_lease':'REAL',prefix+'_error':'TEXT'})
        additions.update({'notified':'REAL','delivered':'REAL'})
        for name, declaration in additions.items():
            if name not in columns:
                db.execute(f'ALTER TABLE key_requests ADD COLUMN {name} {declaration}')
        db.execute("CREATE TABLE IF NOT EXISTS request_throttle (ip_hash TEXT NOT NULL, created REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS notification_attempts (request_id TEXT NOT NULL, created REAL NOT NULL)")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS key_requests_pending_email
            ON key_requests(email) WHERE status IN ('PENDING','APPROVING')""")


@contextlib.contextmanager
def connect():
    db = sqlite3.connect(settings.DATA / "jobs.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        with db:
            yield db
    finally:
        db.close()


def public(row):
    return {k: json.loads(row[k]) if k == "summary" and row[k] else row[k]
            for k in ("id", "name", "status", "created", "updated", "error", "summary")}
