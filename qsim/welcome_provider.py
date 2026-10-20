"""Private welcome metadata service; no browser sessions or mutation routes."""
import argparse
import asyncio
import hashlib
import hmac
import logging
import ssl
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

from .welcome_evidence import (ProviderConfig, EvidenceReader, EvidenceError,
                               InvalidCursor, SnapshotChanged)

log = logging.getLogger('qsim.welcome_provider')


def create_app(config: ProviderConfig, evidence: EvidenceReader | None = None):
    evidence = evidence or EvidenceReader(config)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    @app.middleware('http')
    async def boundary(request, call_next):
        started = time.monotonic()
        operation = 'unknown'
        try:
            if request.url.path.startswith('/provider/'):
                operation = 'welcome:capabilities' if request.url.path == '/provider/v1/capabilities' else 'welcome:read'
                authenticate(request, operation)
            response = await call_next(request)
        except HTTPException as error:
            response = JSONResponse({'detail': error.detail}, status_code=error.status_code)
        except Exception:
            log.warning('welcome_evidence_unavailable')
            response = JSONResponse({'detail': 'evidence_unavailable'}, status_code=503)
        log.info('welcome_read operation=%s credential=%s status=%s duration_ms=%d',
                 operation, getattr(request.state, 'credential_id', 'unauthenticated'),
                 response.status_code, int((time.monotonic() - started) * 1000))
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def authenticate(request, operation):
        if request.url.scheme != 'https':
            raise HTTPException(401, 'TLS required')
        authorization = request.headers.get('authorization', '')
        if not authorization.startswith('Bearer ') or not 1 <= len(authorization[7:]) <= 4096:
            raise HTTPException(401, 'Service authentication required')
        hashed = hashlib.sha256(authorization[7:].encode()).hexdigest()
        binding = evidence.binding_identity
        grant = next((g for g in config.grants() if g.active and hmac.compare_digest(g.sha256, hashed)), None)
        if not grant or grant.audience != 'wqs-welcome:' + binding.app_instance_id or grant.org_id != binding.org_id:
            raise HTTPException(401, 'Service authentication required')
        request.state.credential_id = grant.credential_id
        if operation not in grant.scopes:
            raise HTTPException(403, 'Operation not permitted')
        return binding

    def scoped(request, app_instance_id, org_id):
        binding = authenticate(request, 'welcome:read')
        if (app_instance_id, org_id) != (binding.app_instance_id, binding.org_id):
            raise HTTPException(404, 'Scope not found')
        return evidence.binding()

    @app.get('/provider/v1/capabilities')
    def capabilities(request: Request):
        authenticate(request, 'welcome:capabilities')
        if request.query_params:
            raise HTTPException(422, 'Unknown query fields')
        return evidence.capabilities()

    async def observe():
        try:
            async with asyncio.timeout(15):
                return await asyncio.to_thread(evidence.observe)
        except Exception:
            log.warning('welcome_evidence_unavailable')
            raise HTTPException(503, 'evidence_unavailable') from None

    prefix = '/provider/v1/apps/{app_instance_id}/orgs/{org_id}'

    @app.get(prefix + '/contacts')
    async def contacts(request: Request, app_instance_id: str, org_id: str):
        scoped(request, app_instance_id, org_id)
        if any(k not in ('limit', 'cursor') or len(request.query_params.getlist(k)) != 1 for k in request.query_params):
            raise HTTPException(422, 'Invalid query')
        try:
            limit = int(request.query_params.get('limit', '50'))
            if not 1 <= limit <= 100:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Invalid limit') from None
        binding, items, snapshot = await observe()
        try:
            return evidence.page(binding, items, snapshot, limit, request.query_params.get('cursor'))
        except InvalidCursor:
            raise HTTPException(422, 'Invalid cursor') from None
        except SnapshotChanged:
            raise HTTPException(409, 'snapshot_changed') from None

    @app.get(prefix + '/subjects/{subject_id}')
    async def subject(request: Request, app_instance_id: str, org_id: str, subject_id: str):
        binding = scoped(request, app_instance_id, org_id)
        if request.query_params:
            raise HTTPException(422, 'Unknown query fields')
        if not any(m.subject_id == subject_id for m in binding.members):
            raise HTTPException(404, 'Subject not found')
        _, contacts, _ = await observe()
        item = next((c for c in contacts if c['subject_id'] == subject_id), None)
        if item is None:
            raise HTTPException(404, 'Subject not found')
        return item

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        config = ProviderConfig.from_env()
        config.grants()
        reader = EvidenceReader(config)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(config.tls_cert_file), str(config.tls_key_file))
        if args.check:
            reader.observe()
            return
        app = create_app(config, reader)
    except Exception:
        raise SystemExit('Invalid welcome provider configuration') from None
    uvicorn.run(app, host='0.0.0.0', port=8443, proxy_headers=False, access_log=False,
                ssl_certfile=str(config.tls_cert_file), ssl_keyfile=str(config.tls_key_file),
                ssl_version=ssl.PROTOCOL_TLS_SERVER)


if __name__ == '__main__':
    main()
