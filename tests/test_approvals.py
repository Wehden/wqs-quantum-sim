import json
import sqlite3
import smtplib
import time
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from qsim import settings, store, adminweb, notifications
from qsim.api import app
from qsim.auth import digest

@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(settings,'DATA',tmp_path)
    monkeypatch.setattr(settings,'TOKEN_FILE',tmp_path/'users.json')
    settings.TOKEN_FILE.write_text(json.dumps([{'id':i,'sha256':digest(i+'-key'),'active':True} for i in ('owner','alice')]))
    monkeypatch.setattr(adminweb,'SECRETS',tmp_path)
    monkeypatch.setattr(adminweb,'COOKIE_PATH','/')
    monkeypatch.setenv('QSIM_NOTIFY','0')
    (tmp_path/'session-key').write_bytes(b'x'*32)
    (tmp_path/'encryption-key').write_bytes(Fernet.generate_key())
    with TestClient(app) as public, TestClient(adminweb.app,base_url="http://admin.example.invalid") as admin:
        yield public,admin

def request(public,email='test@example.com'):
    return public.post('/api/key-requests',json={'name':'Test','email':email,'purpose':'Test programmatic access'})

def row():
    with store.connect() as db:return dict(db.execute('SELECT * FROM key_requests LIMIT 1').fetchone())

def bootstrap(admin):
    response=admin.post('/session',headers={'Origin':'http://admin.example.invalid'})
    assert response.status_code==200
    return {'Origin':'http://admin.example.invalid','X-Qsim-CSRF':response.json()['csrf']}

def test_public_request_boundary(clients):
    public,admin=clients
    assert request(public).status_code==202
    assert request(public).status_code==202
    assert row()['status']=='PENDING' and row()['token_hash'] is None
    assert public.get('/api/key-requests').status_code==405
    assert admin.get('/requests').status_code==401
    assert public.get('/requests').status_code==404
    assert request(public,'two@example.com').status_code==202
    assert request(public,'three@example.com').status_code==429
    assert request(public).status_code==429
    assert request(public,'four@example.com').status_code==429
    assert public.post('/api/key-requests',json={'name':' ','email':'a@b.com','purpose':'lots of reasons'}).status_code==422
    text=public.get('/').text.lower()
    for private in ('skypilot','admin.example.invalid','private-node','namespace','qsimconsole','internal/'):
        assert private not in text
    assert public.get('/api/me',headers={'Authorization':'Bearer owner-key'}).json()=={'id':'owner','engine':'Clifft 0.11.0'}

def test_admin_network_session_and_csrf(clients):
    public,admin=clients;request(public);rid=row()['id']
    assert admin.post('/session').status_code==403
    assert admin.post('/session',headers={'Origin':'http://evil.example'}).status_code==403
    assert admin.post('/session',headers={'Origin':'http://admin.example.invalid','Host':'evil.example'}).status_code==403
    assert 'operator-key' not in admin.get('/').text
    assert admin.post('/login',json={'key':'owner-key'}).status_code==404
    headers=bootstrap(admin)
    assert admin.post(f'/requests/{rid}/approve',json={'identity':'test'},headers={'Origin':'http://admin.example.invalid'}).status_code==403
    assert admin.post(f'/requests/{rid}/approve',json={'identity':'owner'},headers=headers).status_code==409
    assert admin.get('/requests',headers={'Host':'evil.example'}).status_code==403
    admin.cookies.clear()
    assert admin.get('/requests').status_code==401


def test_resumable_approval_and_same_key_delivery(clients,monkeypatch):
    public,admin=clients;request(public);rid=row()['id'];headers=bootstrap(admin);hashes=[]
    def conflict(account,hashed):hashes.append(hashed);raise HTTPException(409,'retry')
    monkeypatch.setattr(adminweb.registry,'ensure',conflict)
    assert admin.post(f'/requests/{rid}/approve',json={'identity':'test'},headers=headers).status_code==409
    pending=row();assert pending['status']=='APPROVING'
    decrypt=lambda value:json.loads(adminweb.cipher().decrypt(value.encode()))
    key=decrypt(pending['key_cipher'])['key']
    assert key not in json.dumps(pending)
    def ensure(account,hashed):
        hashes.append(hashed)
        users=json.loads(settings.TOKEN_FILE.read_text());users.append({'id':account,'sha256':hashed,'active':True});settings.TOKEN_FILE.write_text(json.dumps(users))
    monkeypatch.setattr(adminweb.registry,'ensure',ensure)
    assert admin.post(f'/requests/{rid}/approve',json={'identity':'test'},headers=headers).status_code==200
    assert hashes[0]==hashes[1]==digest(key)
    assert key not in admin.get('/requests').text
    attempts=[]
    def uncertain(r,k):attempts.append((r['email'],k));raise smtplib.SMTPServerDisconnected('uncertain')
    now=time.time()
    assert notifications.deliver_one(key_sender=uncertain,decrypt=decrypt,now=now)
    assert row()['delivery_state']=='UNCERTAIN' and row()['key_cipher']
    assert notifications.deliver_one(key_sender=lambda r,k:attempts.append((r['email'],k)),decrypt=decrypt,now=now+61)
    assert attempts==[('test@example.com',key)]*2
    assert row()['delivery_state']=='SENT' and row()['key_cipher'] is None
    assert public.get('/api/me',headers={'Authorization':'Bearer '+key}).status_code==200
    assert admin.post(f'/requests/{rid}/approve',json={'identity':'test'},headers=headers).status_code==200
    assert row()['delivery_state']=='SENT'

def test_notice_no_key_and_mail_budget(clients,monkeypatch):
    public,_=clients;request(public);messages=[]
    monkeypatch.setattr(notifications,'transport',messages.append)
    assert notifications.deliver_one(notice_sender=notifications.send_notice)
    assert messages[0]['To']=='operator@example.invalid'
    assert 'No key has been issued' in messages[0].get_body(preferencelist=('plain',)).get_content()
    assert row()['token_hash'] is None
    with store.connect() as db:
        for _ in range(10):db.execute('INSERT INTO notification_attempts VALUES (?,?)',('test',time.time()))
    assert not notifications.deliver_one()

def test_upgrade_legacy_schema(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'DATA',tmp_path)
    with sqlite3.connect(tmp_path/'jobs.sqlite3') as db:
        db.execute("CREATE TABLE key_requests (id TEXT PRIMARY KEY,name TEXT,email TEXT,purpose TEXT,created REAL,status TEXT DEFAULT 'PENDING')")
        db.execute("INSERT INTO key_requests VALUES ('test','Test','test@example.com','Testing',1,'PENDING')")
    store.init();store.init()
    assert row()['delivery_state']=='NONE' and row()['notification_state']=='PENDING'

def test_capacity_response_does_not_reveal_address(clients):
    public,_=clients;request(public)
    with store.connect() as db:
        for i in range(499):
            db.execute("INSERT INTO key_requests(id,name,email,purpose,created) VALUES (?,?,?,?,?)",(str(i),'Test',f'{i}@example.com','Test',1))
    known=request(public);unknown=request(public,'unknown@example.com')
    assert known.status_code==unknown.status_code==429
    assert known.json()==unknown.json()

def test_delivery_rejects_recipient_change(clients,monkeypatch):
    public,admin=clients;request(public);rid=row()['id']
    monkeypatch.setattr(adminweb.registry,'ensure',lambda *args:None)
    assert admin.post(f'/requests/{rid}/approve',headers=bootstrap(admin),json={'identity':'test'}).status_code==200
    with store.connect() as db:db.execute('UPDATE key_requests SET email=? WHERE id=?',('changed@example.com',rid))
    assert notifications.deliver_one(key_sender=lambda *args:pytest.fail('must not send to changed recipient'),decrypt=lambda value:json.loads(adminweb.cipher().decrypt(value.encode())))
    assert row()['delivery_state']=='RETRY' and row()['delivery_error']=='ValueError'
    assert row()['key_cipher'] is not None


def test_notice_mime_deep_links_and_safe_preview(clients,monkeypatch):
    from email import policy
    from email.parser import BytesParser
    from html.parser import HTMLParser
    public,admin=clients;request(public);data=row();messages=[]
    data.update(name='<script>alert(1)</script>',purpose='Testing <b>markup</b> & escaping')
    monkeypatch.setattr(notifications,'transport',messages.append)
    notifications.send_notice(data)
    mail=BytesParser(policy=policy.default).parsebytes(messages[0].as_bytes())
    assert mail.get_content_type()=='multipart/alternative'
    assert mail['To']=='operator@example.invalid'
    url='http://admin.example.invalid/qsimconsole/#request-'+data['id']
    plain=mail.get_body(preferencelist=('plain',)).get_content()
    html=mail.get_body(preferencelist=('html',)).get_content()
    assert 'Open console: '+url in plain and 'Approve request' in plain
    assert '<script>' not in html and '&lt;script&gt;' in html
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.links=[]
        def handle_starttag(self,tag,attrs):
            if tag=='a':self.links.append(dict(attrs)['href'])
    links=Links();links.feed(html)
    assert links.links==[url,url]
    assert '>Approve request</a>' in html
    assert 'must confirm approval' in html
    assert admin.get('/requests/'+data['id']).status_code==401
    # Link previews and reading details never issue keys or approve a request.
    assert admin.get('/#request-'+data['id']).status_code==200
    headers=bootstrap(admin)
    detail=admin.get('/requests/'+data['id'])
    assert detail.status_code==200 and detail.json()['id']==data['id']
    assert 'token_hash' not in detail.json() and 'key_cipher' not in detail.json()
    assert admin.get('/requests/'+data['id']+'/approve').status_code==405
    assert row()['status']=='PENDING' and row()['token_hash'] is None


def test_operations_private_paginated_and_stale(clients):
    public, admin = clients
    assert admin.get('/operations').status_code == 401
    assert public.get('/operations').status_code == 404
    bootstrap(admin)
    now = time.time()
    with store.connect() as db:
        for i in range(105):
            db.execute('INSERT INTO jobs (id,owner,name,status,created,updated,request,callback_hash) VALUES (?,?,?,?,?,?,?,?)',
                       (str(i),'owner','circuit','RUNNING' if i==0 else 'SUCCEEDED',now+i,now+i,'secret circuit','private-token'))
    first=admin.get('/operations').json()
    assert first['counts']=={'RUNNING':1,'SUCCEEDED':104}
    assert len(first['jobs'])==100 and first['jobs'][0]['id']=='0'
    assert first['skypilot']['stale'] is True
    assert 'secret circuit' not in json.dumps(first) and 'private-token' not in json.dumps(first)
    second=admin.get('/operations?offset=100').json()
    assert len(second['jobs'])==5
    assert not {j['id'] for j in first['jobs']} & {j['id'] for j in second['jobs']}
    assert admin.get('/operations?offset=-1').status_code==422
    (settings.DATA/'skypilot-observation.json').write_text(json.dumps({'observed_at':now,'current':{'cpus':2}}))
    assert admin.get('/operations').json()['skypilot']['stale'] is False
    (settings.DATA/'skypilot-observation.json').write_text(json.dumps({'observed_at':now-181}))
    assert admin.get('/operations').json()['skypilot']['stale'] is True


def test_hostname_counts_cover_all_jobs_and_withhold_stale_placement(clients):
    public,admin=clients;bootstrap(admin);now=time.time()
    with store.connect() as db:
        for i in range(105):
            state='RUNNING' if i<101 else ['LAUNCHING','QUEUED','RUNNING','SUCCEEDED'][i-101]
            cluster='qs-a' if i<101 else ['qs-b',None,'qs-missing','qs-a'][i-101]
            db.execute('INSERT INTO jobs (id,owner,name,status,created,updated,request,callback_hash) VALUES (?,?,?,?,?,?,?,?)',
                       (str(i),'owner','test',state,now,now,'private circuit','private token'))
            db.execute('INSERT INTO job_dispatch(job_id,cluster) VALUES(?,?)',(str(i),cluster))
    snapshot={'observed_at':now,'placements':{'observed_at':now,'clusters':{'qs-a':'node-a','qs-b':'node-b','qs-idle':'node-idle'}}}
    path=settings.DATA/'skypilot-observation.json';path.write_text(json.dumps(snapshot))
    data=admin.get('/operations?offset=100').json();groups=data['jobs_by_hostname']
    assert len(data['jobs'])==5
    assert groups['hosts']==[
        {'hostname':'node-a','running':101,'launching':0,'total':101},
        {'hostname':'node-b','running':0,'launching':1,'total':1},
        {'hostname':'node-idle','running':0,'launching':0,'total':0}]
    assert groups['total_active']==104 and groups['unplaced']==1 and groups['unknown']==1
    assert 'private circuit' not in json.dumps(data) and 'private token' not in json.dumps(data)
    snapshot['placements']['observed_at']=now-181;path.write_text(json.dumps(snapshot))
    groups=admin.get('/operations').json()['jobs_by_hostname']
    assert groups['stale'] is True and groups['hosts']==[] and groups['unknown']==103
    snapshot['placements'].update(observed_at=now,error='ApiException');path.write_text(json.dumps(snapshot))
    assert admin.get('/operations').json()['jobs_by_hostname']['hosts']==[]
    assert public.get('/operations').status_code==404
