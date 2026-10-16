import json

from fastapi.testclient import TestClient
import pytest

from qsim.welcome_provider import create_app
from test_welcome_evidence import rig

PREFIX = '/provider/v1/apps/app-a/orgs/org-a'
HEADERS = {'Authorization': 'Bearer synthetic-service-key'}


def client(rig):
    return TestClient(create_app(rig.config, rig.reader), base_url='https://provider.example.test')


def test_capabilities_and_subject_contract(rig):
    c = client(rig)
    response = c.get('/provider/v1/capabilities', headers=HEADERS)
    caps = response.json()
    assert caps['auth_mode'] == 'api_tokens'
    assert caps['organization_enforcement'] is False and caps['additional_organizations'] is False
    assert caps['policy_revision'] == 'mapping-v1' and len(caps['capability_revision']) == 64
    assert response.headers['cache-control'] == 'no-store'
    item = c.get(PREFIX + '/subjects/subject-a', headers=HEADERS).json()
    assert item['eligible'] and item['first_use'] == 'unknown'
    listing = c.get(PREFIX + '/contacts', headers=HEADERS).json()
    assert listing['contacts'] == [item] and listing['next_cursor'] is None
    assert len(rig.calls) == 4


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'}, {'Cookie': 'qsim_admin=fake', 'Host': 'polecat'}])
def test_no_browser_auth_or_invalid_bearer(rig, headers):
    assert client(rig).get(PREFIX + '/contacts', headers=headers).status_code == 401
    assert rig.calls == []


def test_no_http_or_forwarded_scheme_bypass(rig):
    c = TestClient(create_app(rig.config, rig.reader))
    assert c.get(PREFIX + '/contacts', headers={**HEADERS, 'X-Forwarded-Proto': 'https'}).status_code == 401


@pytest.mark.parametrize('key,value,code', [('audience','wqs-welcome:app-b',401),('org_id','org-b',401),('active',False,401),('scopes',['welcome:capabilities'],403)])
def test_scoped_credentials(rig, key, value, code):
    rig.grant[key] = value
    rig.config.auth_file.write_text(json.dumps([rig.grant]))
    assert client(rig).get(PREFIX + '/contacts', headers=HEADERS).status_code == code


@pytest.mark.parametrize('path', [PREFIX.replace('app-a','app-b')+'/contacts', PREFIX.replace('org-a','org-b')+'/contacts', PREFIX+'/subjects/unknown'])
def test_no_cross_scope_or_subject_enumeration(rig, path):
    assert client(rig).get(path, headers=HEADERS).status_code == 404
    assert not rig.calls


@pytest.mark.parametrize('query', ['?email=evil@example.test','?org_id=org-b','?limit=101','?limit=0','?limit=x','?limit=1&limit=2'])
def test_no_query_overrides(rig, query):
    assert client(rig).get(PREFIX+'/contacts'+query, headers=HEADERS).status_code == 422


def test_no_mutations_and_no_side_effects(rig):
    c = client(rig)
    before = rig.config.database.read_bytes()
    for path in (PREFIX+'/contacts', PREFIX+'/subjects/subject-a', '/provider/v1/capabilities'):
        assert c.post(path, headers=HEADERS).status_code == 405
        assert c.post(path).status_code == 401
    for path in ('/session','/requests','/api/jobs','/docs','/openapi.json'):
        assert c.get(path, headers=HEADERS).status_code == 404
    assert rig.config.database.read_bytes() == before and not rig.calls


def test_failures_do_not_echo_secrets(rig, caplog):
    def fail(*args): raise RuntimeError('NEVER_EXPORT_SECRET')
    rig.reader.registry_get = fail
    response = client(rig).get(PREFIX+'/contacts', headers=HEADERS)
    assert response.status_code == 503
    assert 'NEVER_EXPORT_SECRET' not in response.text + caplog.text
    assert response.headers['cache-control'] == 'no-store'


def test_revoked_contact_not_listed_but_detail_fresh(rig):
    c = client(rig)
    old = c.get(PREFIX+'/subjects/subject-a', headers=HEADERS).json()
    rig.state['users'][0]['active'] = False
    rig.state['revision'] = '2'
    assert c.get(PREFIX+'/contacts', headers=HEADERS).json()['contacts'] == []
    new = c.get(PREFIX+'/subjects/subject-a', headers=HEADERS).json()
    assert not new['eligible'] and new['recipient_email'] is None and new['revision'] != old['revision']


def test_actual_tls_verification(rig):
    from datetime import datetime, timedelta, timezone
    import socket
    import threading
    import time
    import httpx
    import uvicorn
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
            .sign(key, hashes.SHA256()))
    rig.config.tls_cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    rig.config.tls_key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(rig.config,rig.reader), access_log=False,
        proxy_headers=False, ssl_certfile=str(rig.config.tls_cert_file), ssl_keyfile=str(rig.config.tls_key_file)))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.01)
        assert server.started
        with httpx.Client(verify=str(rig.config.tls_cert_file), trust_env=False) as c:
            assert c.get(f'https://localhost:{port}/provider/v1/capabilities',headers=HEADERS).status_code == 200
            with pytest.raises(httpx.ConnectError): c.get(f'https://127.0.0.1:{port}/provider/v1/capabilities',headers=HEADERS)
        with httpx.Client(trust_env=False) as c:
            with pytest.raises(httpx.ConnectError): c.get(f'https://localhost:{port}/provider/v1/capabilities',headers=HEADERS)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
    assert not thread.is_alive()


def test_contract_matches_golden(rig):
    from pathlib import Path
    golden = json.loads(Path('spec/tenancy/on04a/golden.json').read_text())
    c = client(rig)
    assert c.get('/provider/v1/capabilities',headers=HEADERS).json() == golden['capabilities']
    assert c.get(PREFIX+'/subjects/subject-a',headers=HEADERS).json() == golden['eligible_subject']
    assert c.get(PREFIX+'/contacts',headers=HEADERS).json() == golden['eligible_page']
    rig.state['users'][0]['active'] = False
    rig.state['revision'] = '2'
    assert c.get(PREFIX+'/subjects/subject-a',headers=HEADERS).json() == golden['revoked_subject']


def test_reviewable_manifest_is_restricted():
    from pathlib import Path
    import yaml
    docs = list(yaml.safe_load_all(Path('deploy/welcome-provider.example.yaml').read_text()))
    role = next(x for x in docs if x['kind'] == 'Role')
    assert role['rules'] == [{'apiGroups':[''], 'resources':['secrets'], 'resourceNames':['example-users'], 'verbs':['get']}]
    deploy = next(x for x in docs if x['kind'] == 'Deployment')
    container = deploy['spec']['template']['spec']['containers'][0]
    assert container['command'] == ['python','-m','qsim.welcome_provider']
    assert all(x['readOnly'] for x in container['volumeMounts'])
    assert not any(x['kind'] in ('Ingress','ClusterRole') for x in docs)
    assert next(x for x in docs if x['kind']=='Service')['spec']['type'] == 'ClusterIP'


def test_request_timeout_fails_closed(rig, monkeypatch):
    import asyncio
    import time
    original = asyncio.timeout
    monkeypatch.setattr('qsim.welcome_provider.asyncio.timeout', lambda delay: original(.01))
    def slow():
        time.sleep(.05)
        raise RuntimeError('private detail')
    rig.reader.observe = slow
    response = client(rig).get(PREFIX+'/contacts', headers=HEADERS)
    assert response.status_code == 503 and response.json() == {'detail':'evidence_unavailable'}


def test_service_revocation_reloaded(rig):
    c = client(rig)
    assert c.get(PREFIX+'/contacts',headers=HEADERS).status_code == 200
    rig.grant['active'] = False
    rig.config.auth_file.write_text(json.dumps([rig.grant]))
    assert c.get(PREFIX+'/contacts',headers=HEADERS).status_code == 401


def test_config_race_never_leaks_exception(rig, monkeypatch, caplog):
    from qsim.welcome_evidence import ProviderConfig
    original = ProviderConfig.grants
    calls = []
    def racing(self):
        calls.append(1)
        if len(calls) == 2: raise ValueError('PRIVATE_CONFIGURATION_VALUE')
        return original(self)
    monkeypatch.setattr(ProviderConfig, 'grants', racing)
    response = client(rig).get('/provider/v1/capabilities', headers=HEADERS)
    assert response.status_code == 503
    assert 'PRIVATE_CONFIGURATION_VALUE' not in response.text + caplog.text


def test_invalid_bearer_does_not_read_mapping(rig, monkeypatch):
    c = client(rig)
    def forbidden_read(): raise AssertionError('Applicant mapping must not be read')
    monkeypatch.setattr(rig.reader, 'binding', forbidden_read)
    response = c.get(PREFIX+'/contacts',headers={'Authorization':'Bearer incorrect-key'})
    assert response.status_code == 401
    rig.grant['scopes'] = ['welcome:capabilities']
    rig.config.auth_file.write_text(json.dumps([rig.grant]))
    assert c.get(PREFIX+'/contacts',headers=HEADERS).status_code == 403
    assert not rig.calls
