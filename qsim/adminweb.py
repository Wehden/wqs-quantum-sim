"""LAN-trusted internal approval service. Never mounted in the public app."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import time
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
import httpx
from . import settings, store, notifications
from .auth import digest

COOKIE = 'qsim_admin'
COOKIE_PATH = '/qsimconsole'
ORIGINS = set(os.environ.get('QSIM_ADMIN_ORIGINS', 'http://admin.example.invalid,http://admin-alt.example.invalid').split(','))
HOSTS = {origin.split('://',1)[-1] for origin in ORIGINS}
ADMIN_IDS = set(os.environ.get('QSIM_ADMIN_IDENTITIES','owner').split(','))
SECRETS = Path(os.environ.get('QSIM_ADMIN_SECRET_DIR','/run/qsim-admin'))


def signer():
    return (SECRETS/'session-key').read_bytes()


def cipher():
    return Fernet((SECRETS/'encryption-key').read_bytes().strip())


def signed(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode()
    return body+'.'+hmac.new(signer(),body.encode(),hashlib.sha256).hexdigest()


def host(request):
    if request.headers.get('host','').lower() not in HOSTS | {h+':80' for h in HOSTS}:
        raise HTTPException(403,'Internal host required')


def session(request):
    host(request)
    value = request.cookies.get(COOKIE,'')
    try:
        if len(value)>4096:
            raise ValueError()
        body,signature = value.split('.')
        if not hmac.compare_digest(signature,hmac.new(signer(),body.encode(),hashlib.sha256).hexdigest()):
            raise ValueError()
        claim=json.loads(base64.urlsafe_b64decode(body))
        if claim['exp']<=time.time() or claim['id']!='internal-operator':
            raise ValueError()
        return claim
    except (ValueError,KeyError,TypeError,OSError):
        raise HTTPException(401,'Internal session required')


def origin(request):
    host(request)
    if request.headers.get('origin') not in ORIGINS:
        raise HTTPException(403,'Origin not allowed')


def mutation(request):
    origin(request)
    claim=session(request)
    if not hmac.compare_digest(request.headers.get('x-qsim-csrf',''),claim['csrf']):
        raise HTTPException(403,'Invalid CSRF token')
    return claim


class Registry:
    def ensure(self, account, hashed):
        token=Path('/var/run/secrets/kubernetes.io/serviceaccount/token').read_text().strip()
        ca='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
        url=f'https://{os.environ["KUBERNETES_SERVICE_HOST"]}:{os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS","443")}/api/v1/namespaces/qsim/secrets/qsim-users'
        with httpx.Client(verify=ssl.create_default_context(cafile=ca),headers={'Authorization':'Bearer '+token},timeout=10) as client:
            response=client.get(url);response.raise_for_status();secret=response.json()
            users=json.loads(base64.b64decode(secret['data']['users.json']))
            existing=next((u for u in users if u['id']==account),None)
            if existing:
                if existing['sha256']!=hashed or not existing.get('active'):
                    raise HTTPException(409,'Identity already exists; choose a new identity or resolve the reserved approval privately')
                return
            users.append({'id':account,'sha256':hashed,'active':True})
            secret['data']['users.json']=base64.b64encode(json.dumps(users).encode()).decode()
            secret['metadata'].pop('managedFields',None)
            secret['metadata'].get('annotations',{}).pop('kubectl.kubernetes.io/last-applied-configuration',None)
            response=client.put(url,json=secret)
            if response.status_code==409:
                raise HTTPException(409,'Invitation registry changed concurrently; retry this approval')
            response.raise_for_status()


registry=Registry()


def approve_request(request_id, account, guard=None):
    if account in ADMIN_IDS:
        raise HTTPException(409,'Approval cannot create or rotate an operator identity')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM key_requests WHERE id=?',(request_id,)).fetchone()
        if row is None:
            raise HTTPException(404,'Request not found')
        if guard:
            guard(db, row)
        reserved=db.execute('SELECT request_id FROM admin_identity_reservations WHERE identity=?',(account,)).fetchone()
        if reserved and reserved['request_id'] != request_id:
            raise HTTPException(409,'Identity is reserved by another invitation')
        if row['status']=='APPROVED' and row['identity']==account:
            return {'status':'APPROVED','identity':account}
        if row['status']=='PENDING':
            # Early conflict checks improve feedback; the registry's CAS is authoritative.
            users=json.loads(settings.TOKEN_FILE.read_text())
            if any(u['id']==account for u in users):
                raise HTTPException(409,'Identity already exists; this action cannot rotate an existing key')
            if db.execute("SELECT 1 FROM key_requests WHERE identity=? AND status IN ('APPROVING','APPROVED')",(account,)).fetchone():
                raise HTTPException(409,'Identity is already reserved')
            raw='qsim_'+secrets.token_urlsafe(32)
            encrypted=cipher().encrypt(json.dumps({'key':raw,'email':row['email'],'identity':account,'request_id':request_id}).encode()).decode();hashed=digest(raw)
            db.execute("UPDATE key_requests SET status='APPROVING',identity=?,token_hash=?,key_cipher=?,reviewed=? WHERE id=?",(account,hashed,encrypted,time.time(),request_id))
        elif row['status']=='APPROVING' and row['identity']==account and row['key_cipher']:
            payload=json.loads(cipher().decrypt(row['key_cipher'].encode()));raw=payload['key'];hashed=digest(raw)
            if payload['email']!=row['email'] or payload['identity']!=account or payload['request_id']!=request_id:
                raise HTTPException(409,'Approval envelope does not match the request')
        else:
            raise HTTPException(409,'Request cannot be approved in its current state')
    try:
        registry.ensure(account,hashed)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503,'Registry update failed; retry this approval to resume with the same key')
    with store.connect() as db:
        db.execute("UPDATE key_requests SET status='APPROVED',delivery_state='PENDING',reviewed=? WHERE id=? AND status='APPROVING' AND token_hash=?",(time.time(),request_id,hashed))
    return {'status':'APPROVED','identity':account}


async def notification_loop():
    while True:
        try:
            await asyncio.to_thread(notifications.deliver_one, decrypt=lambda value: json.loads(cipher().decrypt(value.encode())))
        except Exception:
            # No recipient data, credentials, or transport traces in process logs.
            pass
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    store.init()
    signer();cipher()  # Fail startup if the private key material is missing/invalid.
    task=asyncio.create_task(notification_loop()) if os.environ.get('QSIM_NOTIFY','1')=='1' else None
    yield
    if task:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass


app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)


@app.middleware('http')
async def boundary(request,call_next):
    size=0;chunks=[]
    async for chunk in request.stream():
        size+=len(chunk)
        if size>16384:
            return JSONResponse({'detail':'Request too large'},status_code=413)
        chunks.append(chunk)
    request._body=b''.join(chunks)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid')
    identity: str=Field(min_length=1,max_length=128,pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.@+-]*$')


@app.get('/healthz')
def health():return {'status':'ok'}


@app.post('/session')
def bootstrap(request:Request):
    origin(request)
    claim={'id':'internal-operator','exp':time.time()+28800,'csrf':secrets.token_urlsafe(32)}
    response=JSONResponse({'id':claim['id'],'csrf':claim['csrf']})
    response.set_cookie(COOKIE,signed(claim),max_age=28800,httponly=True,samesite='strict',path=COOKIE_PATH,secure=False)
    return response


@app.get('/session')
def who(request:Request):
    claim=session(request);return {'id':claim['id'],'csrf':claim['csrf']}


@app.post('/logout')
def logout(request:Request):
    mutation(request);response=JSONResponse({'status':'signed out'});response.delete_cookie(COOKIE,path=COOKIE_PATH);return response


def hostname_counts(active_jobs, placement, now):
    observed = placement.get('observed_at', 0)
    available = bool(observed) and now - observed <= 180 and not placement.get('error')
    clusters = placement.get('clusters', {}) if available else {}
    hosts = {host: {'hostname': host, 'launching': 0, 'running': 0, 'total': 0}
             for host in clusters.values() if host}
    unplaced, unknown = 0, 0
    for job in active_jobs:
        cluster = job['cluster']
        if job['status'] == 'QUEUED' or (job['status'] == 'LAUNCHING' and not cluster):
            unplaced += 1
        elif cluster in clusters and clusters[cluster]:
            host = hosts[clusters[cluster]]
            host[job['status'].lower()] += 1
            host['total'] += 1
        elif available and cluster in clusters and clusters[cluster] is None:
            unplaced += 1
        else:
            unknown += 1
    return {'hosts': sorted(hosts.values(), key=lambda row: (-row['total'], row['hostname'])),
            'unplaced': unplaced, 'unknown': unknown, 'total_active': len(active_jobs),
            'observed_at': observed or None, 'stale': not available, 'error': placement.get('error')}


@app.get('/operations')
def operations(request: Request, offset: int = Query(default=0, ge=0)):
    session(request)
    now = time.time()
    with store.connect() as db:
        counts = dict(db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status').fetchall())
        active = [dict(r) for r in db.execute("SELECT j.status,d.cluster FROM jobs j LEFT JOIN job_dispatch d ON d.job_id=j.id WHERE j.status IN ('QUEUED','LAUNCHING','RUNNING')")]
        rows = [dict(r) for r in db.execute("""SELECT j.id,j.name,j.owner,j.status,j.created,j.updated,j.error,d.cluster
            FROM jobs j LEFT JOIN job_dispatch d ON d.job_id=j.id
            ORDER BY (j.status IN ('QUEUED','LAUNCHING','RUNNING')) DESC,j.created DESC,j.id
            LIMIT 100 OFFSET ?""", (offset,))]
    try:
        sky = json.loads((settings.DATA / 'skypilot-observation.json').read_text())
    except (OSError, ValueError):
        sky = {}
    sky['stale'] = now - sky.get('observed_at', 0) > 180 or bool(sky.get('last_error'))
    placement = sky.get('placements', {})
    fresh = bool(placement.get('observed_at')) and now - placement['observed_at'] <= 180 and not placement.get('error')
    clusters = placement.get('clusters', {}) if fresh else {}
    for row in rows:
        # Current placement cannot establish where a completed job ran.
        row['hostname'] = clusters.get(row['cluster']) if row['status'] in ('LAUNCHING', 'RUNNING') else None
    return {'observed_at': now, 'counts': counts, 'total': sum(counts.values()),
            'jobs': rows, 'offset': offset, 'page_size': 100, 'skypilot': sky,
            'jobs_by_hostname': hostname_counts(active, sky.get('placements', {}), now)}


@app.get('/requests')
def requests(request:Request):
    session(request)
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT id,name,email,purpose,created,status,identity,reviewed,notification_state,notification_attempts,notification_error,notified,delivery_state,delivery_attempts,delivery_error,delivered FROM key_requests ORDER BY created DESC LIMIT 500")]


@app.get('/requests/{request_id}')
def request_detail(request_id:str,request:Request):
    session(request)
    with store.connect() as db:
        row=db.execute("SELECT id,name,email,purpose,created,status,identity,reviewed,notification_state,notification_attempts,notification_error,notified,delivery_state,delivery_attempts,delivery_error,delivered FROM key_requests WHERE id=?",(request_id,)).fetchone()
    if row is None:
        raise HTTPException(404,'Request not found')
    return dict(row)


@app.post('/requests/{request_id}/approve')
def approve(request_id:str,body:Approval,request:Request):
    mutation(request);return approve_request(request_id,body.identity)


def request_action(request_id, action, guard=None):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM key_requests WHERE id=?',(request_id,)).fetchone()
        if row is None:
            raise HTTPException(404,'Request not found')
        if guard:
            guard(db,row)
        if action=='reject':
            changed=db.execute("UPDATE key_requests SET status='REJECTED',reviewed=? WHERE id=? AND status='PENDING'",(time.time(),request_id)).rowcount
            result={'status':'REJECTED'}
        elif action=='notify':
            changed=db.execute("UPDATE key_requests SET notification_state='RETRY',notification_attempts=0,notification_next=0 WHERE id=? AND status='PENDING' AND notification_state IN ('FAILED','RETRY')",(request_id,)).rowcount
            result={'status':'RETRY'}
        elif action=='retry-delivery':
            changed=db.execute("UPDATE key_requests SET delivery_state='RETRY',delivery_attempts=0,delivery_next=0 WHERE id=? AND status='APPROVED' AND key_cipher IS NOT NULL AND delivery_state IN ('FAILED','RETRY','UNCERTAIN')",(request_id,)).rowcount
            result={'status':'RETRY'}
        else:
            raise HTTPException(404,'Unknown action')
        if not changed:
            raise HTTPException(409,'Request action is not currently eligible')
        if guard:
            guard(db,None)  # Commit command success atomically with this DB-only effect.
        return result


@app.post('/requests/{request_id}/reject')
def reject(request_id:str,request:Request):
    mutation(request);return request_action(request_id,'reject')


@app.post('/requests/{request_id}/notify')
def retry_notice(request_id:str,request:Request):
    mutation(request);return request_action(request_id,'notify')


@app.post('/requests/{request_id}/retry-delivery')
def retry_delivery(request_id:str,request:Request):
    mutation(request);return request_action(request_id,'retry-delivery')


@app.get('/')
def index():return FileResponse(Path(__file__).parent/'admin_static'/'index.html')


app.mount('/assets',StaticFiles(directory=Path(__file__).parent/'admin_static'),name='assets')
