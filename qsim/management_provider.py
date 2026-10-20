"""Private, create-only management of the existing WQS bootstrap registry."""
import argparse
import base64
from contextlib import asynccontextmanager, closing
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
import uvicorn

from . import adminweb, settings, store
from .welcome_evidence import ProviderConfig, EvidenceReader, Grant, StrictModel, ID, canonical, read_json

PREFIX = '/internal/admin/v1'
COMMAND_ID = r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'
OPERATIONS = ['identities', 'requests', 'invitation', 'approve', 'reject', 'notify', 'retry-delivery', 'command']
REQUEST_FIELDS = ('id', 'name', 'email', 'purpose', 'created', 'status', 'identity', 'reviewed',
                  'notification_state', 'notification_attempts', 'notified', 'delivery_state',
                  'delivery_attempts', 'delivered')
COMMAND_FIELDS = ('command_id', 'app_instance_id', 'org_id', 'actor', 'operation', 'status',
                  'request_id', 'identity', 'error_code', 'created_at', 'updated_at')


class ManagementConfig(ProviderConfig):
    @classmethod
    def from_env(cls):
        return cls(*(Path(os.environ['WQS_ADMIN_' + key]) for key in
                     ('BINDING_FILE', 'AUTH_FILE', 'INTEGRITY_KEY_FILE')),
                   Path(os.environ.get('QSIM_DATA', '/data')) / 'jobs.sqlite3',
                   *(Path(os.environ['WQS_ADMIN_' + key]) for key in ('TLS_CERT_FILE', 'TLS_KEY_FILE')))

    def grants(self):
        value = read_json(self.auth_file)
        if not isinstance(value, list) or not 1 <= len(value) <= 2:
            raise ValueError('invalid_grants')
        grants = [Grant.model_validate(g) for g in value]
        if any(set(g.scopes) - {'admin:read', 'admin:write'} for g in grants):
            raise ValueError('invalid_scopes')
        if any(len({getattr(g,k) for g in grants}) != len(grants) for k in ('credential_id','sha256')):
            raise ValueError('duplicate_grants')
        return grants


class Command(StrictModel):
    command_id: str = Field(pattern=COMMAND_ID)
    app_instance_id: str = Field(pattern=ID)
    org_id: str = Field(pattern=ID)
    actor: str = Field(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')


class Invitation(Command):
    identity: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=100, pattern=r'^[^\x00-\x1f\x7f]+$')
    email: str = Field(max_length=254, pattern=r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$')
    purpose: str = Field(min_length=10, max_length=2000, pattern=r'^[^\x00-\x1f\x7f]+$')

    @field_validator('name', 'purpose', mode='before')
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value,str) else value

    @field_validator('email')
    @classmethod
    def canonical_email(cls, value):
        if value != value.strip().lower():
            raise ValueError('noncanonical_email')
        return value


class Action(Command):
    expected_revision: str = Field(min_length=1, max_length=128)
    identity: str | None = Field(default=None, pattern=ID)


class Management:
    def __init__(self, config, evidence=None):
        self.config = config
        self.evidence = evidence or EvidenceReader(config)
        binding = self.evidence.binding_identity
        # Native approval currently owns exactly this deployed bootstrap registry.
        if (binding.namespace, binding.secret_name) != ('qsim', 'qsim-users'):
            raise ValueError('unsupported_bootstrap_registry')
        if config.database.resolve() != (settings.DATA / 'jobs.sqlite3').resolve():
            raise ValueError('owner_database_mismatch')

    def scope(self):
        b = self.evidence.binding()
        return {'app_instance_id': b.app_instance_id, 'org_id': b.org_id}

    def revision(self, row):
        return 'r1.' + self.evidence.sign('admin-request', dict(row))

    def request(self, row):
        value = {k: row[k] for k in REQUEST_FIELDS}
        actions = []
        if row['status'] in ('PENDING', 'APPROVING'):
            actions.append('approve')
        if row['status'] == 'PENDING':
            actions.append('reject')
            if row['notification_state'] in ('FAILED', 'RETRY'):
                actions.append('notify')
        if (row['status'] == 'APPROVED' and row['key_cipher'] is not None
                and row['delivery_state'] in ('FAILED', 'RETRY', 'UNCERTAIN')):
            actions.append('retry-delivery')
        return {**value, 'revision': self.revision(row), 'actions': actions}

    def snapshot(self):
        binding = self.evidence.binding()
        with closing(sqlite3.connect('file:' + str(self.config.database.resolve()) + '?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            rows = db.execute('SELECT * FROM key_requests ORDER BY created DESC,id').fetchall()
            if len(rows) > 10000:
                raise HTTPException(503, 'inventory_limit')
            version, users = self.evidence.registry(self.evidence.registry_get(binding, 5))
            if len(users) > 10000:
                raise HTTPException(503, 'inventory_limit')
            requests = [self.request(row) for row in rows]
            final_version, _ = self.evidence.registry(self.evidence.registry_get(binding, 5))
            if final_version != version or binding != self.evidence.binding():
                raise HTTPException(409, 'snapshot_changed')
        histories = {}
        for request in requests:
            histories.setdefault(request['identity'], []).append(request)
        identities = [{'identity': user['id'], 'active': user['active'],
                       'requests': histories.get(user['id'], [])}
                      for user in sorted(users.values(), key=lambda item: item['id'])]
        snapshot = self.evidence.sign('admin-snapshot', {'registry': version,
            'requests': [r['revision'] for r in requests], 'binding': binding.model_dump()})
        return identities, requests, snapshot

    def page(self, kind, limit, cursor):
        identities, requests, snapshot = self.snapshot()
        rows = identities if kind == 'identities' else requests
        offset = 0
        if cursor:
            try:
                body, signature = cursor.split('.')
                value = json.loads(base64.urlsafe_b64decode(body))
                if not hmac.compare_digest(signature, self.evidence.sign('admin-cursor', value)) or value['kind'] != kind:
                    raise ValueError()
                if value['snapshot'] != snapshot:
                    raise HTTPException(409, 'snapshot_changed')
                offset = value['offset']
                if type(offset) is not int or not 0 <= offset < len(rows):
                    raise ValueError()
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(422, 'invalid_cursor') from None
        next_cursor = None
        if offset + limit < len(rows):
            value = {'kind': kind, 'snapshot': snapshot, 'offset': offset + limit}
            next_cursor = base64.urlsafe_b64encode(canonical(value)).decode() + '.' + self.evidence.sign('admin-cursor', value)
        return {**self.scope(), 'snapshot_revision': snapshot, 'next_cursor': next_cursor, kind: rows[offset:offset+limit]}

    def outcome(self, command_id):
        with store.connect() as db:
            row = db.execute('SELECT * FROM admin_commands WHERE id=?', (command_id,)).fetchone()
        if row is None or any(row[key] != value for key,value in self.scope().items()):
            raise HTTPException(404, 'command_not_found')
        return {key: row['id'] if key == 'command_id' else row[key] for key in COMMAND_FIELDS}

    def finish(self, db, command_id, status, error=None):
        db.execute('UPDATE admin_commands SET status=?,error_code=?,updated_at=? WHERE id=?',
                   (status, error, time.time(), command_id))

    def execute(self, operation, body, request_id=None):
        scope = self.scope()
        if any(getattr(body, key) != value for key, value in scope.items()):
            raise HTTPException(404, 'scope_not_found')
        if isinstance(body, Action) and ((operation == 'approve') != (body.identity is not None)):
            raise HTTPException(422, 'invalid_identity_field')
        payload = {'operation': operation, 'request_id': request_id, **body.model_dump()}
        digest = hashlib.sha256(canonical(payload)).hexdigest()
        # Lock only one command; the kernel releases it after crashes. Durable
        # intent plus the native resumable approval cover post-effect crashes.
        directory = settings.DATA / '.admin-command-locks'
        directory.mkdir(mode=0o700, exist_ok=True)
        with open(directory / body.command_id, 'a', opener=lambda p,f: os.open(p,f,0o600)) as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                existing = self.outcome(body.command_id)
                with store.connect() as db:
                    saved = db.execute('SELECT payload_hash FROM admin_commands WHERE id=?',(body.command_id,)).fetchone()
                if saved['payload_hash'] != digest:
                    raise HTTPException(409,'command_payload_conflict')
                return existing
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT * FROM admin_commands WHERE id=?', (body.command_id,)).fetchone()
                if existing and existing['payload_hash'] != digest:
                    raise HTTPException(409, 'command_payload_conflict')
                if existing and existing['status'] in ('SUCCEEDED', 'FAILED'):
                    return {key: existing['id'] if key == 'command_id' else existing[key] for key in COMMAND_FIELDS}
                if not existing:
                    now = time.time()
                    db.execute('INSERT INTO admin_commands(id,payload,payload_hash,app_instance_id,org_id,actor,operation,request_id,identity,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                               (body.command_id,canonical(payload).decode(),digest,body.app_instance_id,body.org_id,body.actor,operation,request_id,body.identity if hasattr(body,'identity') else None,'ACCEPTED',now,now))
            try:
                if operation == 'invitation':
                    request_id = self.prepare_invitation(body)
                def guard(db, row):
                    command = db.execute('SELECT * FROM admin_commands WHERE id=?', (body.command_id,)).fetchone()
                    if row is None:
                        self.finish(db, body.command_id, 'SUCCEEDED')
                    elif command['phase'] == 'READY':
                        if operation != 'invitation' and not hmac.compare_digest(body.expected_revision, self.revision(row)):
                            raise HTTPException(409, 'stale_revision')
                        db.execute("UPDATE admin_commands SET phase='RUNNING',updated_at=? WHERE id=?",(time.time(),body.command_id))
                if operation in ('invitation', 'approve'):
                    adminweb.approve_request(request_id, body.identity, guard=guard)
                    with store.connect() as db:
                        self.finish(db, body.command_id, 'SUCCEEDED')
                else:
                    adminweb.request_action(request_id, operation, guard=guard)
            except HTTPException as error:
                with store.connect() as db:
                    row = db.execute('SELECT status,identity FROM key_requests WHERE id=?',(request_id,)).fetchone()
                    retryable = error.status_code >= 500 or (error.status_code == 409 and operation in ('invitation','approve')
                        and error.detail != 'stale_revision' and row and row['status'] == 'APPROVING'
                        and row['identity'] == body.identity)
                    code = error.detail if error.detail in ('stale_revision','identity_exists','email_pending','identity_reserved') else ('owner_temporarily_unavailable' if retryable else 'owner_conflict')
                    self.finish(db, body.command_id, 'RETRYABLE' if retryable else 'FAILED', code)
            except Exception:
                # Outcome may already exist in native approval; same-command retry
                # resumes it. Never disclose transport diagnostics or ciphertext.
                with store.connect() as db:
                    self.finish(db, body.command_id, 'RETRYABLE', 'owner_temporarily_unavailable')
            return self.outcome(body.command_id)

    def prepare_invitation(self, body):
        binding = self.evidence.binding()
        _, users = self.evidence.registry(self.evidence.registry_get(binding, 5))
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            command = db.execute('SELECT request_id FROM admin_commands WHERE id=?',(body.command_id,)).fetchone()
            if command['request_id']:
                return command['request_id']
            if body.identity in users or body.identity in adminweb.ADMIN_IDS:
                raise HTTPException(409, 'identity_exists')
            if db.execute('SELECT 1 FROM admin_identity_reservations WHERE identity=?',(body.identity,)).fetchone() or db.execute("SELECT 1 FROM key_requests WHERE identity=? AND status IN ('APPROVING','APPROVED')",(body.identity,)).fetchone():
                raise HTTPException(409, 'identity_reserved')
            if db.execute("SELECT 1 FROM key_requests WHERE email=? AND status IN ('PENDING','APPROVING')",(body.email,)).fetchone():
                raise HTTPException(409, 'email_pending')
            request_id = uuid.uuid4().hex
            db.execute("INSERT INTO key_requests(id,name,email,purpose,created,notification_state) VALUES(?,?,?,?,?,'NONE')",
                       (request_id,body.name,body.email,body.purpose,time.time()))
            db.execute('INSERT INTO admin_identity_reservations VALUES(?,?,?)',(body.identity,request_id,body.command_id))
            db.execute('UPDATE admin_commands SET request_id=? WHERE id=?',(request_id,body.command_id))
            return request_id


def create_app(config, evidence=None):
    management = Management(config, evidence)
    @asynccontextmanager
    async def lifespan(app):
        store.init()
        if any('admin:write' in grant.scopes and grant.active for grant in config.grants()):
            adminweb.cipher()  # Refuse command readiness without the owner's delivery envelope key.
        yield
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.state.management = management

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return JSONResponse({'detail':'invalid_request'},status_code=422)

    @app.middleware('http')
    async def boundary(request, call_next):
        try:
            binding = management.evidence.binding()
            credential = request.headers.get('authorization','')
            if request.url.scheme != 'https' or not credential.startswith('Bearer ') or not 1 <= len(credential[7:]) <= 4096:
                raise HTTPException(401,'service_authentication_required')
            hashed = hashlib.sha256(credential[7:].encode()).hexdigest()
            grant = next((g for g in config.grants() if g.active and hmac.compare_digest(g.sha256,hashed)),None)
            if not grant or grant.audience != 'wqs-admin:'+binding.app_instance_id or grant.org_id != binding.org_id:
                raise HTTPException(401,'service_authentication_required')
            request.state.management_scopes = grant.scopes
            if ('admin:read' if request.method=='GET' else 'admin:write') not in grant.scopes:
                raise HTTPException(403,'operation_not_permitted')
            if request.method == 'GET' and request.query_params and request.url.path not in (PREFIX+'/identities', PREFIX+'/requests'):
                raise HTTPException(422,'invalid_query')
            data=bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data)>16384: raise HTTPException(413,'request_too_large')
            request._body=bytes(data)
            response=await call_next(request)
            raw=bytearray()
            async for chunk in response.body_iterator:
                raw.extend(chunk)
                if len(raw)>256*1024: raise HTTPException(503,'response_limit')
            response=JSONResponse(json.loads(raw),status_code=response.status_code)
        except HTTPException as error:
            response=JSONResponse({'detail':error.detail},status_code=error.status_code)
        except Exception:
            response=JSONResponse({'detail':'owner_unavailable'},status_code=503)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.get(PREFIX+'/capabilities')
    def capabilities(request:Request):
        operations = OPERATIONS if 'admin:write' in request.state.management_scopes else ['identities','requests','command']
        return {'contract_version':'wqs-admin/1',**management.scope(),'auth_mode':'api_tokens',
                'operations':operations,'create_only':True,'organization_enforcement':False,
                'additional_organizations':False,'first_use_tracking':False}

    def listing(request, kind):
        if any(k not in ('limit','cursor') or len(request.query_params.getlist(k))!=1 for k in request.query_params):
            raise HTTPException(422,'invalid_query')
        try:
            limit=int(request.query_params.get('limit','50'))
            if not 1<=limit<=100 or len(request.query_params.get('cursor',''))>4096: raise ValueError()
        except ValueError: raise HTTPException(422,'invalid_query') from None
        return management.page(kind,limit,request.query_params.get('cursor'))

    @app.get(PREFIX+'/identities')
    def identities(request:Request):return listing(request,'identities')
    @app.get(PREFIX+'/requests')
    def requests(request:Request):return listing(request,'requests')
    @app.get(PREFIX+'/requests/{request_id}')
    def detail(request_id:str):
        _,rows,_=management.snapshot()
        row=next((r for r in rows if r['id']==request_id),None)
        if row is None:raise HTTPException(404,'request_not_found')
        return row
    @app.get(PREFIX+'/commands/{command_id}')
    def outcome(command_id:str):return management.outcome(command_id)

    def result(value):
        status=200 if value['status']=='SUCCEEDED' else 409 if value['status']=='FAILED' else 202
        return JSONResponse(value,status_code=status)
    @app.post(PREFIX+'/invitations')
    def invite(body:Invitation):return result(management.execute('invitation',body))
    @app.post(PREFIX+'/requests/{request_id}/{operation}')
    def action(request_id:str,operation:str,body:Action):
        if operation not in ('approve','reject','notify','retry-delivery') or not re.fullmatch('[a-f0-9]{32}',request_id):
            raise HTTPException(404,'operation_not_found')
        return result(management.execute(operation,body,request_id))
    return app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    try:
        config=ManagementConfig.from_env()
        config.grants()
        app=create_app(config)
        if args.check:
            if any('admin:write' in grant.scopes and grant.active for grant in config.grants()):
                adminweb.cipher()
            app.state.management.snapshot()
            return
    except Exception:
        raise SystemExit('Invalid management provider configuration') from None
    uvicorn.run(app,host='0.0.0.0',port=8443,proxy_headers=False,access_log=False,
                ssl_certfile=str(config.tls_cert_file),ssl_keyfile=str(config.tls_key_file))


if __name__=='__main__':main()
