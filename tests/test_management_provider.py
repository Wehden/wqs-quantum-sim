import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from qsim import adminweb, settings, store
from qsim.auth import digest
from qsim.management_provider import create_app, ManagementConfig, PREFIX, Invitation
from qsim.welcome_evidence import EvidenceReader

HEADERS={'Authorization':'Bearer synthetic-service-key'}

@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(settings,'DATA',tmp_path)
    monkeypatch.setattr(settings,'TOKEN_FILE',tmp_path/'users.json')
    monkeypatch.setattr(adminweb,'SECRETS',tmp_path)
    (tmp_path/'encryption-key').write_bytes(Fernet.generate_key())
    (tmp_path/'session-key').write_bytes(b's'*32)
    store.init()
    config=ManagementConfig(*(tmp_path/f for f in ('binding.json','grants.json','integrity','jobs.sqlite3','cert','key')))
    binding={'schema_version':1,'revision':'mapping-v1','app_instance_id':'wqs','org_id':'bootstrap',
             'namespace':'qsim','secret_name':'qsim-users','members':[]}
    config.binding_file.write_text(json.dumps(binding))
    grant={'credential_id':'central','sha256':digest('synthetic-service-key'),'audience':'wqs-admin:wqs',
           'org_id':'bootstrap','scopes':['admin:read','admin:write'],'active':True}
    config.auth_file.write_text(json.dumps([grant]));config.integrity_key_file.write_bytes(b'i'*32)
    users=[{'id':name,'sha256':digest('synthetic-'+name),'active':name!='inactive'} for name in ['owner','inactive','legacy','a','b','c']]
    state={'version':1,'users':users,'issued':[]}
    settings.TOKEN_FILE.write_text(json.dumps(users))
    def get(binding,timeout):return str(state['version']),copy.deepcopy(state['users'])
    def ensure(identity,hashed):
        old=next((u for u in state['users'] if u['id']==identity),None)
        if old:
            if old['sha256']!=hashed or not old['active']:raise HTTPException(409,'synthetic collision')
            return
        state['users'].append({'id':identity,'sha256':hashed,'active':True});state['version']+=1
        state['issued'].append((identity,hashed))
        settings.TOKEN_FILE.write_text(json.dumps(state['users']))
    monkeypatch.setattr(adminweb.registry,'ensure',ensure)
    reader=EvidenceReader(config,registry_get=get)
    app=create_app(config,reader)
    with TestClient(app,base_url='https://owner.example.test',headers=HEADERS) as client:
        yield SimpleNamespace(client=client,app=app,config=config,reader=reader,state=state,grant=grant,ensure=ensure)


def invitation(command='cmd-1',identity='new-user',**kw):
    return {'command_id':command,'actor':'central-admin','app_instance_id':'wqs','org_id':'bootstrap',
            'identity':identity,'name':'Example','email':identity+'@example.test','purpose':'Testing API',**kw}


def pending(email='pending@example.test',identity=None):
    rid=hashlib.md5(email.encode()).hexdigest()
    with store.connect() as db:
        db.execute('INSERT INTO key_requests(id,name,email,purpose,created,identity) VALUES(?,?,?,?,?,?)',
                   (rid,'Pending',email,'Requested API',10,identity))
    return rid


def action(rig,rid,operation,command='action-1',**extra):
    row=rig.client.get(PREFIX+'/requests/'+rid).json()
    return rig.client.post(f'{PREFIX}/requests/{rid}/{operation}',json={
        'command_id':command,'actor':'central-admin','app_instance_id':'wqs','org_id':'bootstrap',
        'expected_revision':row['revision'],**extra})


def test_registry_inventory_includes_unmapped_inactive_and_pending_queue(rig):
    for index,name in enumerate(('owner','a','b','c')):
        rid=pending(name+'@example.test',name)
        with store.connect() as db:db.execute("UPDATE key_requests SET status='APPROVED',delivery_state='SENT' WHERE id=?",(rid,))
    pending()
    response=rig.client.get(PREFIX+'/identities?limit=2'); page=response.json(); rows=page['identities']
    while page['next_cursor']:
        page=rig.client.get(PREFIX+'/identities',params={'limit':2,'cursor':page['next_cursor']}).json();rows+=page['identities']
    assert len(rows)==6 and sum(r['active'] for r in rows)==5
    assert sum(not r['requests'] for r in rows)==2
    assert len(rig.client.get(PREFIX+'/requests').json()['requests'])==5
    text=json.dumps(rows)
    for forbidden in ('sha256','token_hash','key_cipher','synthetic-owner'):
        assert forbidden not in text
    assert response.headers['Cache-Control']=='no-store'


def test_snapshot_change_rejects_cursor(rig):
    cursor=rig.client.get(PREFIX+'/identities?limit=1').json()['next_cursor']
    rig.state['version']+=1
    assert rig.client.get(PREFIX+'/identities',params={'cursor':cursor}).status_code==409


def test_invitation_create_only_replay_conflict_and_audit(rig):
    payload=invitation();result=rig.client.post(PREFIX+'/invitations',json=payload)
    assert result.status_code==200,result.text
    command=result.json();assert command['status']=='SUCCEEDED' and command['actor']=='central-admin'
    assert rig.client.post(PREFIX+'/invitations',json=payload).json()==command
    assert rig.client.get(PREFIX+'/commands/cmd-1').json()==command
    assert len(rig.state['issued'])==1
    assert rig.client.post(PREFIX+'/invitations',json={**payload,'purpose':'Different purpose'}).status_code==409
    existing=rig.client.post(PREFIX+'/invitations',json=invitation('cmd-2'))
    assert existing.status_code==409 and existing.json()['error_code']=='identity_exists'
    assert len(rig.state['issued'])==1
    with store.connect() as db:
        row=db.execute('SELECT * FROM key_requests WHERE id=?',(command['request_id'],)).fetchone()
        assert row['status']=='APPROVED' and row['delivery_state']=='PENDING'
        assert row['key_cipher'] and row['token_hash']
        assert row['key_cipher'] not in result.text and row['token_hash'] not in result.text


def test_existing_identity_never_rotates_even_inactive(rig):
    before=copy.deepcopy(rig.state['users'])
    for name in ('owner','inactive','legacy'):
        result=rig.client.post(PREFIX+'/invitations',json=invitation('cmd-'+name,name))
        assert result.status_code==409 and result.json()['error_code']=='identity_exists'
    assert rig.state['users']==before


def test_approval_transient_registry_error_resumes_same_envelope(rig,monkeypatch):
    seen=[]
    def fail(identity,hashed):seen.append(hashed);raise HTTPException(503,'sensitive transport')
    monkeypatch.setattr(adminweb.registry,'ensure',fail)
    payload=invitation();result=rig.client.post(PREFIX+'/invitations',json=payload)
    assert result.status_code==202 and result.json()['status']=='RETRYABLE'
    assert 'sensitive' not in result.text
    monkeypatch.setattr(adminweb.registry,'ensure',rig.ensure)
    retry=rig.client.post(PREFIX+'/invitations',json=payload)
    assert retry.status_code==200 and rig.state['issued'][0][1]==seen[0]


def test_lost_response_after_native_approval_reconciles_without_new_key(rig,monkeypatch):
    management=rig.app.state.management; original=management.finish
    def fail(db,cid,status,error=None):
        if status=='SUCCEEDED':raise RuntimeError('lost after owner commit')
        original(db,cid,status,error)
    monkeypatch.setattr(management,'finish',fail)
    result=rig.client.post(PREFIX+'/invitations',json=invitation())
    assert result.json()['status']=='RETRYABLE' and len(rig.state['issued'])==1
    monkeypatch.setattr(management,'finish',original)
    assert rig.client.post(PREFIX+'/invitations',json=invitation()).json()['status']=='SUCCEEDED'
    assert len(rig.state['issued'])==1


def test_request_approval_rejection_notice_and_stale_revision(rig):
    rid=pending();old=rig.client.get(PREFIX+'/requests/'+rid).json()
    with store.connect() as db:db.execute("UPDATE key_requests SET notification_state='FAILED' WHERE id=?",(rid,))
    stale=rig.client.post(f'{PREFIX}/requests/{rid}/reject',json={'command_id':'stale','actor':'operator','app_instance_id':'wqs','org_id':'bootstrap','expected_revision':old['revision']})
    assert stale.status_code==409 and stale.json()['error_code']=='stale_revision'
    assert action(rig,rid,'notify','notice').status_code==200
    assert action(rig,rid,'reject','reject').status_code==200
    assert action(rig,rid,'approve','approve',identity='pending').status_code==409
    other=pending('other@example.test')
    assert action(rig,other,'approve','approve-other',identity='other').status_code==200


def test_delivery_retry_does_not_recover_sent_material(rig):
    command=rig.client.post(PREFIX+'/invitations',json=invitation()).json();rid=command['request_id']
    with store.connect() as db:db.execute("UPDATE key_requests SET delivery_state='UNCERTAIN' WHERE id=?",(rid,))
    assert action(rig,rid,'retry-delivery','retry').status_code==200
    with store.connect() as db:db.execute("UPDATE key_requests SET delivery_state='SENT',key_cipher=NULL WHERE id=?",(rid,))
    detail=rig.client.get(PREFIX+'/requests/'+rid).json()
    assert 'retry-delivery' not in detail['actions']
    assert action(rig,rid,'retry-delivery','retry-sent').status_code==409


def test_concurrent_invites_reserve_identity_without_rotation(rig):
    manager=rig.app.state.management
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda n:manager.execute('invitation',Invitation(**invitation('race-'+str(n),'same'))),range(2)))
    assert sorted(r['status'] for r in results)==['FAILED','SUCCEEDED']
    assert len(rig.state['issued'])==1


@pytest.mark.parametrize('change,status', [('missing',401),('http',401),('welcome',401),('read_only',403),('scope',404)])
def test_service_boundary(rig,change,status):
    headers=HEADERS;payload=invitation();url=PREFIX+'/invitations'
    if change=='missing':headers={'Authorization':''}
    if change=='http':url='http://owner.example.test'+url
    if change=='welcome':
        rig.grant['audience']='wqs-welcome:wqs';rig.config.auth_file.write_text(json.dumps([rig.grant]))
    if change=='read_only':
        rig.grant['scopes']=['admin:read'];rig.config.auth_file.write_text(json.dumps([rig.grant]))
    if change=='scope':payload['org_id']='other'
    assert rig.client.post(url,json=payload,headers=headers).status_code==status
    assert not rig.state['issued']


def test_validation_and_transport_do_not_echo_inputs_or_secrets(rig):
    payload={**invitation(),'raw_key':'DO_NOT_ECHO'}
    result=rig.client.post(PREFIX+'/invitations',json=payload)
    assert result.status_code==422 and 'DO_NOT_ECHO' not in result.text
    def fail(*a):raise RuntimeError('DO_NOT_ECHO')
    rig.reader.registry_get=fail
    result=rig.client.get(PREFIX+'/identities')
    assert result.status_code==503 and 'DO_NOT_ECHO' not in result.text


def test_native_registry_cas_replays_same_hash_without_overwrite(rig, monkeypatch):
    import base64
    from pathlib import Path
    import httpx
    state={'rv':'1','users':copy.deepcopy(rig.state['users']),'put':0,'conflict':True}
    def transport(request):
        assert request.url.path=='/api/v1/namespaces/qsim/secrets/qsim-users'
        if request.method=='GET':
            return httpx.Response(200,json={'metadata':{'resourceVersion':state['rv']},'data':{'users.json':base64.b64encode(json.dumps(state['users']).encode()).decode()}})
        payload=json.loads(request.content);state['put']+=1
        assert payload['metadata']['resourceVersion']==state['rv']
        if state['conflict']:
            state['conflict']=False;state['rv']='2';return httpx.Response(409)
        state['users']=json.loads(base64.b64decode(payload['data']['users.json']));state['rv']='3'
        return httpx.Response(200,json={})
    original_read=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda p,*a,**kw:'synthetic-kube-token' if str(p)=='/var/run/secrets/kubernetes.io/serviceaccount/token' else original_read(p,*a,**kw))
    original_client=httpx.Client
    monkeypatch.setattr(adminweb.httpx,'Client',lambda **kw:original_client(transport=httpx.MockTransport(transport),headers=kw.get('headers')))
    monkeypatch.setattr(adminweb.ssl,'create_default_context',lambda **kw:True)
    monkeypatch.setenv('KUBERNETES_SERVICE_HOST','kubernetes.example.test')
    monkeypatch.setattr(adminweb.registry,'ensure',adminweb.Registry().ensure)
    payload=invitation();response=rig.client.post(PREFIX+'/invitations',json=payload)
    assert response.status_code==202 and response.json()['status']=='RETRYABLE'
    response=rig.client.post(PREFIX+'/invitations',json=payload)
    assert response.status_code==200
    assert len([u for u in state['users'] if u['id']=='new-user'])==1 and state['put']==2
    before=copy.deepcopy(state['users'])
    assert rig.client.post(PREFIX+'/invitations',json=payload).status_code==200
    assert state['users']==before and state['put']==2


def test_db_only_action_and_outcome_commit_atomically(rig,monkeypatch):
    rid=pending();manager=rig.app.state.management;finish=manager.finish
    def fail(db,cid,status,error=None):
        if status=='SUCCEEDED':raise RuntimeError('transaction interrupted')
        finish(db,cid,status,error)
    monkeypatch.setattr(manager,'finish',fail)
    response=action(rig,rid,'reject','atomic')
    assert response.json()['status']=='RETRYABLE'
    assert rig.client.get(PREFIX+'/requests/'+rid).json()['status']=='PENDING'
    monkeypatch.setattr(manager,'finish',finish)
    assert action(rig,rid,'reject','atomic').json()['status']=='SUCCEEDED'


def test_legacy_approval_cannot_take_reserved_identity(rig):
    rid=pending();other=pending('other@example.test')
    with store.connect() as db:
        db.execute('INSERT INTO admin_identity_reservations VALUES(?,?,?)',('reserved',rid,'claim'))
    with pytest.raises(HTTPException) as error:adminweb.approve_request(other,'reserved')
    assert error.value.status_code==409 and not rig.state['issued']


def test_unknown_query_and_historical_scope_outcome_rejected(rig):
    assert rig.client.get(PREFIX+'/capabilities?org_id=other').status_code==422
    result=rig.client.post(PREFIX+'/invitations',json=invitation())
    with store.connect() as db:db.execute("UPDATE admin_commands SET org_id='other' WHERE id='cmd-1'")
    assert rig.client.get(PREFIX+'/commands/cmd-1').status_code==404


def test_read_only_grant_capabilities_do_not_offer_commands(rig):
    rig.grant['scopes']=['admin:read'];rig.config.auth_file.write_text(json.dumps([rig.grant]))
    caps=rig.client.get(PREFIX+'/capabilities').json()
    assert caps['operations']==['identities','requests','command']
    assert rig.client.get(PREFIX+'/identities').status_code==200
    assert rig.client.post(PREFIX+'/invitations',json=invitation()).status_code==403


def test_stale_approving_request_is_terminal_conflict_not_retryable(rig):
    rid=pending();before=rig.client.get(PREFIX+'/requests/'+rid).json()
    with store.connect() as db:db.execute("UPDATE key_requests SET status='APPROVING',identity='pending' WHERE id=?",(rid,))
    result=rig.client.post(f'{PREFIX}/requests/{rid}/approve',json={
        'command_id':'stale-approving','actor':'operator','app_instance_id':'wqs','org_id':'bootstrap',
        'identity':'pending','expected_revision':before['revision']})
    assert result.status_code==409
    assert result.json()['status']=='FAILED' and result.json()['error_code']=='stale_revision'


def test_duplicate_during_lock_acquisition_has_durable_intent(rig, monkeypatch):
    import fcntl
    import threading
    acquired, release = threading.Event(), threading.Event()
    original = fcntl.flock
    def pause_first_holder(lock, operation):
        original(lock, operation)
        if operation == fcntl.LOCK_EX | fcntl.LOCK_NB and not acquired.is_set():
            acquired.set()
            assert release.wait(5)
    monkeypatch.setattr(fcntl, 'flock', pause_first_holder)
    payload = invitation()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(rig.client.post, PREFIX+'/invitations', json=payload)
        assert acquired.wait(5)
        try:
            duplicate = rig.client.post(PREFIX+'/invitations', json=payload)
            assert duplicate.status_code == 202
            assert duplicate.json()['status'] == 'ACCEPTED'
            lookup = rig.client.get(PREFIX+'/commands/cmd-1')
            assert lookup.status_code == 200
            assert lookup.json() == duplicate.json()
            conflict = rig.client.post(PREFIX+'/invitations', json=invitation(identity='different'))
            assert conflict.status_code == 409
            assert conflict.json()['detail'] == 'command_payload_conflict'
            assert not rig.state['issued']
        finally:
            release.set()
        assert first.result(timeout=5).json()['status'] == 'SUCCEEDED'
    assert len(rig.state['issued']) == 1
    assert rig.client.post(PREFIX+'/invitations', json=payload).json()['status'] == 'SUCCEEDED'
    assert len(rig.state['issued']) == 1


def test_crash_before_lock_acquisition_leaves_resumable_intent(rig, monkeypatch):
    import fcntl
    class Crash(BaseException):
        pass
    original = fcntl.flock
    def crash(lock, operation):
        raise Crash()
    monkeypatch.setattr(fcntl, 'flock', crash)
    manager = rig.app.state.management
    body = Invitation(**invitation())
    with pytest.raises(Crash):
        manager.execute('invitation', body)
    assert manager.outcome(body.command_id)['status'] == 'ACCEPTED'
    assert not rig.state['issued']
    monkeypatch.setattr(fcntl, 'flock', original)
    assert manager.execute('invitation', body)['status'] == 'SUCCEEDED'
    assert len(rig.state['issued']) == 1
