import re
import base64
import binascii
import json
import os
import secrets
import shutil
import time
import uuid
import ipaddress
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from . import settings, store
from . import public_docs
from .streams import router as stream_router
from .http_boundary import HttpBoundary
from .qasm_adapter import SUPPORTED_COMBINATIONS
from .auth import digest, identity
from .models import Submission, StimSubmission, QasmSubmission, Qasm3Submission, Completion, KeyRequest


@asynccontextmanager
async def lifespan(app):
    public_docs.public_url()
    store.init()
    yield


app = FastAPI(title="wqsim", version="0.3.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(stream_router)


app.add_middleware(HttpBoundary)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return JSONResponse(status_code=422, content={"detail": [
        {key: item[key] for key in ("type", "loc", "msg")} for item in error.errors()
    ]})


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/readyz")
def ready():
    try:
        users = json.loads(settings.TOKEN_FILE.read_text())
        assert any(u.get("active") and len(u.get("sha256", "")) == 64 for u in users)
        with store.connect() as db:
            db.execute("SELECT 1 FROM jobs LIMIT 1").fetchone()
    except Exception:
        raise HTTPException(503, "Service configuration not ready")
    return {"status": "ready"}


@app.post("/api/key-requests", status_code=202)
def request_key(body: KeyRequest, request: Request):
    peer = request.client.host if request.client else "unknown"
    try:
        trusted = any(ipaddress.ip_address(peer) in ipaddress.ip_network(cidr.strip()) for cidr in os.environ.get("QSIM_TRUSTED_PROXY_CIDRS", "").split(",") if cidr.strip())
        if trusted and request.headers.get("CF-Connecting-IP"):
            peer = str(ipaddress.ip_address(request.headers["CF-Connecting-IP"]))
    except ValueError:
        pass
    ip_hash = hashlib.sha256(peer.encode()).hexdigest()
    now = time.time()
    email = body.email.strip().lower()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM request_throttle WHERE created<?", (now-3600,))
        recent_ip = db.execute("SELECT COUNT(*) FROM request_throttle WHERE ip_hash=?", (ip_hash,)).fetchone()[0]
        recent_all = db.execute("SELECT COUNT(*) FROM request_throttle").fetchone()[0]
        count = db.execute("SELECT COUNT(*) FROM key_requests WHERE status IN ('PENDING','APPROVING')").fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM key_requests").fetchone()[0]
        if recent_ip >= 3 or recent_all >= 10 or count >= 500 or total >= 5000:
            raise HTTPException(429, "Too many key requests; please try again later")
        db.execute("INSERT INTO request_throttle (ip_hash,created) VALUES (?,?)", (ip_hash, now))
        existing = db.execute("SELECT 1 FROM key_requests WHERE email=? AND status IN ('PENDING','APPROVING')", (email,)).fetchone()
        if not existing:
            db.execute("INSERT INTO key_requests (id,name,email,purpose,created) VALUES (?,?,?,?,?)",
                       (uuid.uuid4().hex, body.name, email, body.purpose, now))
    # Identical response for a new request and an already pending email. No public lookup.
    return {"status": "pending", "message": "Your request will be reviewed. A key is issued only after approval."}


@app.get("/api/me")
def me(user=Depends(identity)):
    return {"id": user, "engine": "Clifft 0.12.0"}


@app.get("/api/jobs")
def jobs(user=Depends(identity)):
    with store.connect() as db:
        return [store.public(r) for r in db.execute("SELECT * FROM jobs WHERE owner=? ORDER BY created DESC", (user,))]


def owned(job_id, user):
    with store.connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, user)).fetchone()
    if row is None:
        raise HTTPException(404, "Job not found")
    return row


@app.post("/api/jobs", status_code=202)
def submit(body: Submission, user=Depends(identity), idempotency_key: str | None = Header(default=None)):
    return enqueue(body,user,idempotency_key)


def canonical_request(body):
    canonical_body = body.model_dump()
    # Existing reservations include normalized defaults but predate engine selection and bindings.
    if canonical_body["engine"] == "clifft":
        canonical_body.pop("engine")
    legacy = canonical_body["input_format"] in ("stim", "qasm2")
    if legacy and canonical_body.get("bindings") == {}:
        canonical_body.pop("bindings")
    return json.dumps(canonical_body, sort_keys=True, separators=(",", ":"))


def enqueue(body,user,idempotency_key):
    if idempotency_key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}",idempotency_key):
        raise HTTPException(422,"Idempotency-Key must be 1–128 ASCII letters, digits, dots, underscores, colons or hyphens")
    request_hash=digest(canonical_request(body))
    key_hash=digest(idempotency_key) if idempotency_key is not None else None
    job_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    now = time.time()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM job_idempotency WHERE created<? AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.id=job_idempotency.job_id)",(now-30*86400,))
        if key_hash:
            previous=db.execute("SELECT * FROM job_idempotency WHERE owner=? AND key_hash=?",(user,key_hash)).fetchone()
            if previous:
                if previous['request_hash'] != request_hash:
                    raise HTTPException(409,"Idempotency-Key was already used with a different request")
                existing=db.execute("SELECT * FROM jobs WHERE id=? AND owner=?",(previous['job_id'],user)).fetchone()
                if existing is None:
                    raise HTTPException(410,"The idempotent job was deleted or expired; use a new key for new work")
                return JSONResponse(store.public(existing),status_code=202,headers={'Location':'/api/jobs/'+existing['id'],'Idempotency-Replayed':'true'})

        release = db.execute('SELECT release,smoke_owner FROM release_control WHERE singleton=1').fetchone()
        if release['release'] and (not release['smoke_owner'] or user != release['smoke_owner']):
            raise HTTPException(503, 'Release maintenance: retry new submissions later', headers={'Retry-After': '60'})

        directory = settings.DATA / job_id
        directory.mkdir(mode=0o700)
        # Only the controller reads this file; user tokens are never sent to compute pods.
        token_path=directory / "callback-token"
        fd=os.open(token_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as file:
            file.write(token);file.flush();os.fsync(file.fileno())
        for path in (directory,settings.DATA):
            fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        db.execute("INSERT INTO jobs (id,owner,name,status,created,updated,request,callback_hash) VALUES (?,?,?,'QUEUED',?,?,?,?)",
                   (job_id, user, body.name, now, now, body.model_dump_json(), digest(token)))
        if key_hash:
            db.execute("INSERT INTO job_idempotency(owner,key_hash,request_hash,job_id,created) VALUES (?,?,?,?,?)",(user,key_hash,request_hash,job_id,now))
    return JSONResponse(store.public(owned(job_id,user)),status_code=202,headers={'Location':'/api/jobs/'+job_id,'Idempotency-Replayed':'false'})


@app.post('/api/jobs/stim',status_code=202)
def submit_stim(body:StimSubmission,user=Depends(identity),idempotency_key:str=Header(min_length=1,max_length=128)):
    return enqueue(Submission(**body.model_dump()),user,idempotency_key)


@app.post('/api/jobs/qasm2',status_code=202)
def submit_qasm(body:QasmSubmission,user=Depends(identity),idempotency_key:str=Header(min_length=1,max_length=128)):
    return enqueue(Submission(**{**body.model_dump(exclude_unset=True),
                                 'input_format': 'qasm2', 'operation': body.operation}), user, idempotency_key)


@app.post('/api/jobs/qasm3', status_code=202)
def submit_qasm3(body: Qasm3Submission, user=Depends(identity), idempotency_key: str=Header(min_length=1, max_length=128)):
    return enqueue(Submission(**{**body.model_dump(exclude_unset=True),
                                 'input_format': 'qasm3', 'operation': body.operation}), user, idempotency_key)


@app.get('/api/capabilities')
def capabilities(user=Depends(identity)):
    sampling = 'sample' in SUPPORTED_COMBINATIONS.get('qsim', {}).get('qasm2', ())
    qasm_operations = ['basis_probabilities'] + (['sample'] if sampling else [])
    qasm3_operations = list(SUPPORTED_COMBINATIONS.get('qsim', {}).get('qasm3', ()))
    formats = {'qasm2': qasm_operations}
    if qasm3_operations:
        formats['qasm3'] = qasm3_operations
    result = {'engines':{'clifft':{'version':'0.12.0','default':True},
                       'qsim':{'version':'0.22.1','input_formats':formats,'max_qubits':26,'device':'CPU','precision':'single',
                               'importer': {'name': 'cirq', 'version': '1.7.0'},
                               'sampling': {'enabled': sampling, 'result_schema': 'qasm-samples/1' if sampling else None,
                                            'classical_registers': sampling, 'measurement': sampling,
                                            'mid_circuit_measurement': sampling, 'reset': sampling,
                                            'repeated_writes': sampling, 'conditions': False, 'custom_gates': sampling,
                                            'max_shots': 10000, 'max_classical_cells': 1000000,
                                            'max_count_entries': 256, 'max_preview_shots': 20},
                               'features_by_operation': {
                                   'qasm2': {'basis_probabilities': {'custom_gates': False, 'bindings': False},
                                             **({'sample': {'custom_gates': True, 'bindings': False}} if sampling else {})},
                                   **({'qasm3': {operation: {'custom_gates': True, 'bindings': True, 'conditions': False}
                                                for operation in qasm3_operations}} if qasm3_operations else {})},
                               'binding_types': ['float[32]', 'float[64]', 'angle[32]', 'angle[64]'] if qasm3_operations else [],
                               'custom_gate_limits': {'max_expanded_operations': 65536, 'max_nesting_depth': 32}}},
            'input_formats':{'stim':['sample','basis_probabilities'], **formats},
            'transactional':'durable jobs with owner-scoped Idempotency-Key',
            'streaming':{'protocol':'SSE','progress':True,'final_result':True,'resume':'Last-Event-ID',
                         'incremental_samples':False,'reason':'Workers return complete sample arrays; incremental sample streaming is not enabled'},
            'qasm2_subset':'Probability queries retain unitary qelib1.inc circuits. Google qsim sampling supports classical registers, measurement, reset and bounded custom gates when enabled; conditions remain unsupported.',
            'client_manages':['call sequences','batches','quantum state']}
    if SUPPORTED_COMBINATIONS.get('aer'):
        result['engines']['aer'] = {
            'version': '0.17.2', 'default': False,
            'input_formats': {fmt: list(ops) for fmt, ops in SUPPORTED_COMBINATIONS['aer'].items()},
            'max_qubits': 26, 'device': 'CPU', 'method': 'statevector', 'precision': 'double',
            'importer': {'name': 'qiskit', 'version': '2.6.0', 'qasm3_importer_version': '0.6.0'},
            'sampling': {'enabled': True, 'result_schema': 'qasm-samples/1',
                         'classical_registers': True, 'measurement': True,
                         'mid_circuit_measurement': True, 'reset': True,
                         'repeated_writes': True, 'conditions': True, 'custom_gates': False,
                         'max_shots': 10000, 'max_classical_cells': 1000000,
                         'max_count_entries': 256, 'max_preview_shots': 20},
            'features_by_operation': {
                'qasm2': {'sample': {'conditions': True, 'custom_gates': False, 'bindings': False}},
                'qasm3': {'sample': {'conditions': True, 'custom_gates': False, 'bindings': False}}},
            'condition_subset': {
                'qasm2': 'whole-register equality to an in-range integer; one standard gate statement',
                'qasm3': 'scalar bit == boolean or whole-register == in-range integer; flat if/else blocks',
                'conditional_measurement_reset': ['qasm3'],
                'nested_conditions': False, 'loops': False},
            'limits': {'max_expanded_operations': 65536, 'max_source_nesting': 32}}
    return result



@app.get("/api/jobs/{job_id}")
def job(job_id: str, user=Depends(identity)):
    return store.public(owned(job_id, user))


@app.post("/api/jobs/{job_id}/cancel", status_code=202)
def cancel(job_id: str, user=Depends(identity)):
    owned(job_id, user)
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='CANCELLED', updated=? WHERE id=? AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED')", (time.time(), job_id))
    return store.public(owned(job_id, user))


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, user=Depends(identity)):
    owned(job_id, user)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, user)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found")
        if row["status"] not in settings.TERMINAL or not row["cleaned"]:
            raise HTTPException(409, "Wait for the job and compute cleanup to finish")
        shutil.rmtree(settings.DATA / row["id"], ignore_errors=True)
        db.execute("DELETE FROM jobs WHERE id=?", (job_id,))


@app.get("/api/jobs/{job_id}/result")
def result(job_id: str, user=Depends(identity)):
    row = owned(job_id, user)
    if row["status"] != "SUCCEEDED":
        raise HTTPException(409, "Results are not available")
    return FileResponse(settings.DATA / row["id"] / "result.npz", filename=f"qsim-{row['id']}.npz", media_type="application/octet-stream")


def callback_job(job_id, authorization):
    with store.connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or not authorization.startswith("Bearer ") or not secrets.compare_digest(row["callback_hash"], digest(authorization[7:])):
        raise HTTPException(401, "Invalid job credential")
    return row


@app.get("/internal/jobs/{job_id}")
def input_job(job_id: str, authorization: str = Header(default="")):
    row = callback_job(job_id, authorization)
    if row["status"] in settings.TERMINAL:
        raise HTTPException(409, "Job has ended")
    return json.loads(row["request"])


@app.post("/internal/jobs/{job_id}")
def complete(job_id: str, body: Completion, authorization: str = Header(default="")):
    callback_job(job_id, authorization)
    if body.summary is not None and len(json.dumps(body.summary).encode()) > 2 * 1024 * 1024:
        raise HTTPException(422, "Result summary too large")
    artifact = None
    if body.status == "SUCCEEDED":
        if not body.artifact or body.summary is None:
            raise HTTPException(422, "Success requires results")
        try:
            artifact = base64.b64decode(body.artifact, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(422, "Invalid result encoding")
        if not artifact.startswith(b"PK"):
            raise HTTPException(422, "Result must be an NPZ archive")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row["status"] in settings.TERMINAL:
            # Idempotent callbacks never resurrect cancelled or failed jobs.
            if row["status"] == body.status:
                return {"status": row["status"]}
            raise HTTPException(409, "Job has ended")
        if artifact is not None:
            dest = settings.DATA / job_id / "result.npz"
            temp = dest.with_suffix(".tmp")
            with temp.open("wb") as f:
                f.write(artifact)
                f.flush()
                os.fsync(f.fileno())
            temp.replace(dest)
        db.execute("UPDATE jobs SET status=?, updated=?, error=?, summary=? WHERE id=?",
                   (body.status, time.time(), body.error, json.dumps(body.summary) if body.summary else None, job_id))
    return {"status": body.status}


@app.get('/agents.md',include_in_schema=False)
@app.api_route('/static/agents.md', methods=['GET', 'HEAD'], include_in_schema=False)
def agent_guide():
    return Response(public_docs.render('agents.md'), media_type='text/markdown',
                    headers={'Content-Disposition': 'attachment; filename="qsim-agent-guide.md"'})


@app.get("/")
@app.api_route('/static/index.html', methods=['GET', 'HEAD'], include_in_schema=False)
def index():
    return HTMLResponse(public_docs.render('index.html'))


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
