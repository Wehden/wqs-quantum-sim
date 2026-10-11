#!/usr/bin/env python3
"""Exercise transactional Stim/QASM submissions and resumable SSE without printing keys."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import time
import uuid
import httpx
import numpy as np

parser=argparse.ArgumentParser()
parser.add_argument('--base',default='https://simulation.example.invalid')
parser.add_argument('--key-file',type=Path,default=Path('var/owner-invitation.key'))
args=parser.parse_args()
headers={'Authorization':'Bearer '+args.key_file.read_text().strip()}
report={'base':args.base,'jobs':[],'placements':{}}

def placement():
    pods=json.loads(subprocess.check_output(['kubectl','-n','qsim-jobs','get','pods','-o','json']))['items']
    for pod in pods:
        name=pod['metadata']['name']
        if any(name.startswith('qs-'+job['id'][:20]) for job in report['jobs']):
            spec=pod['spec']
            report['placements'][name]={'node':spec.get('nodeName'),'nodeSelector':spec['nodeSelector'],'affinity':spec.get('affinity'),'scheduler':spec['schedulerName']}

with httpx.Client(base_url=args.base,headers=headers,timeout=httpx.Timeout(30,read=25)) as client:
    capability=client.get('/api/capabilities');capability.raise_for_status()
    assert capability.json()['streaming']['incremental_samples'] is False
    for kind,payload in [('stim',{'name':'Transactional Stim live validation','circuit':'H 0\nCX 0 1\nM 0 1','shots':1000,'seed':42}),
                         ('qasm2',{'name':'Transactional QASM2 live validation','circuit':'OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; h q[0]; cx q[0],q[1];','bitstrings':['00','01','10','11']})]:
        key=str(uuid.uuid4());url='/api/jobs/'+kind
        r=client.post(url,headers={'Idempotency-Key':key},json=payload);r.raise_for_status()
        assert r.status_code==202 and r.headers['Idempotency-Replayed']=='false'
        job=r.json();again=client.post(url,headers={'Idempotency-Key':key},json=payload)
        assert again.status_code==202 and again.json()['id']==job['id'] and again.headers['Idempotency-Replayed']=='true'
        conflict=client.post(url,headers={'Idempotency-Key':key},json={**payload,'name':'Different request'})
        assert conflict.status_code==409
        report['jobs'].append({'format':kind,'id':job['id'],'retry_same_job':True,'conflict':409,'events':[]})
    Path('var/api-smoke-running.json').write_text(json.dumps(report,indent=2))
    for job in report['jobs']:
        url='/api/jobs/'+job['id']+'/events';started=time.monotonic();cursor='0';received=set();terminal=False;next_placement=0
        # Disconnect immediately after the first complete status event, then resume.
        with client.stream('GET',url) as response:
            response.raise_for_status();assert response.headers['content-type'].startswith('text/event-stream')
            event={}
            for line in response.iter_lines():
                if line.startswith('id: '):event['id']=line[4:]
                elif line.startswith('data: '):event['data']=json.loads(line[6:])
                elif line=='' and 'id' in event and 'data' in event:
                    cursor=event['id'];received.add(cursor);job['events'].append({'id':int(cursor),'status':event['data']['status']});break
        job['first_event_seconds']=round(time.monotonic()-started,2)
        while not terminal and time.monotonic()-started<600:
            with client.stream('GET',url,headers={'Last-Event-ID':cursor}) as response:
                response.raise_for_status();event={}
                for line in response.iter_lines():
                    if time.monotonic()>=next_placement:placement();next_placement=time.monotonic()+8
                    if line.startswith('id: '):event['id']=line[4:]
                    elif line.startswith('event: '):event['type']=line[7:]
                    elif line.startswith('data: '):event['data']=json.loads(line[6:])
                    elif line=='':
                        if 'id' in event:
                            assert event['id'] not in received,'Resume replayed an already acknowledged event'
                            cursor=event['id'];received.add(cursor)
                            state=event['data']['status'];job['events'].append({'id':int(cursor),'status':state})
                            print(job['format'],job['id'],state,flush=True)
                            if state in ('FAILED','CANCELLED'):raise RuntimeError(event['data']['error'] or state)
                            if state=='SUCCEEDED':
                                assert event['type']=='result';terminal=True;job['summary']=event['data']['summary']
                        event={}
            if not terminal:time.sleep(1)
        assert terminal,'No final event within deadline'
        result=client.get('/api/jobs/'+job['id']+'/result');result.raise_for_status()
        with np.load(io.BytesIO(result.content),allow_pickle=False) as arrays:
            if job['format']=='stim':
                m=arrays['measurements'];assert m.shape==(1000,2) and np.all(m[:,0]==m[:,1]);assert set(map(tuple,m))=={(0,0),(1,1)}
            else:
                assert arrays['bitstrings'].tolist()==['00','01','10','11'];assert np.allclose(arrays['probabilities'],[.5,0,0,.5])
        job['artifact_valid']=True;job['resume_no_duplicates']=True;job['elapsed_seconds']=round(time.monotonic()-started,2)
    # Warm workers stay alive for reuse. Cleanup means the dispatcher has released
    # each completed job, not that SkyPilot has already removed its idle worker.
    ids=[j['id'] for j in report['jobs']]
    code="import sqlite3,json,sys; d=sqlite3.connect('/data/jobs.sqlite3'); ids=json.loads(sys.argv[1]); print(json.dumps([r[0] for r in d.execute('SELECT cleaned FROM jobs WHERE id IN ('+','.join('?' for _ in ids)+')',ids)]))"
    for _ in range(30):
        cleaned=json.loads(subprocess.check_output(['kubectl','-n','qsim','exec','deployment/qsim-api','--','python','-c',code,json.dumps(ids)]))
        if len(cleaned)==len(ids) and all(cleaned):break
        time.sleep(3)
    else:raise RuntimeError('Validation jobs are still awaiting dispatcher cleanup')
report['cleanup']='Both jobs released; idle workers remain eligible for reuse'
Path('var/api-smoke-result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':'PASS','jobs':[{'format':j['format'],'id':j['id'],'events':j['events'],'artifact_valid':j['artifact_valid']} for j in report['jobs']],'cleanup':report['cleanup']},indent=2))
