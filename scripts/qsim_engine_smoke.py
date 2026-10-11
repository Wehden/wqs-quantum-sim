"""Compare saved Clifft fixtures with Google qsim through the public job API."""
import argparse
import io
import json
from pathlib import Path
import time
import uuid
import httpx
import numpy as np

parser=argparse.ArgumentParser()
parser.add_argument('--base',default='https://simulation.example.invalid')
parser.add_argument('--fixtures',type=Path,required=True)
parser.add_argument('--key-file',type=Path,default=Path('var/owner-invitation.key'))
parser.add_argument('--report',type=Path,default=Path('var/qsim-engine-smoke.json'))
args=parser.parse_args()
report={'base':args.base,'jobs':[]}
with httpx.Client(base_url=args.base,headers={'Authorization':'Bearer '+args.key_file.read_text().strip()},timeout=30) as c:
    caps=c.get('/api/capabilities');caps.raise_for_status()
    assert caps.json()['engines']['qsim']['version']=='0.22.1'
    for fixture in json.loads(args.fixtures.read_text()):
        body={k:v for k,v in fixture['request'].items() if k in ('circuit','bitstrings','bit_order')}
        body.update(engine='qsim',name='Google qsim comparison: '+fixture['request']['name'])
        key=str(uuid.uuid4());started=time.monotonic()
        r=c.post('/api/jobs/qasm2',json=body,headers={'Idempotency-Key':key});r.raise_for_status()
        job=r.json();item={'id':job['id'],'baseline_id':fixture['id'],'request':body};report['jobs'].append(item)
        args.report.write_text(json.dumps(report,indent=2))
        again=c.post('/api/jobs/qasm2',json=body,headers={'Idempotency-Key':key})
        assert again.status_code==202 and again.json()['id']==job['id'] and again.headers['Idempotency-Replayed']=='true'
        last=None
        while time.monotonic()-started<600:
            r=c.get('/api/jobs/'+job['id']);r.raise_for_status();job=r.json()
            if job['status']!=last:print(job['id'],job['status'],flush=True);last=job['status']
            if job['status'] in ('SUCCEEDED','FAILED','CANCELLED'):break
            time.sleep(3)
        assert job['status']=='SUCCEEDED', job
        s=job['summary'];assert s['engine']=='qsim 0.22.1'
        expected=np.asarray(fixture['summary']['probabilities']);actual=np.asarray(s['probabilities'])
        np.testing.assert_allclose(actual,expected,atol=1e-6,rtol=1e-6)
        result=c.get('/api/jobs/'+job['id']+'/result');result.raise_for_status()
        with np.load(io.BytesIO(result.content),allow_pickle=False) as arrays:
            np.testing.assert_array_equal(arrays['probabilities'],actual)
            assert arrays['bitstrings'].tolist()==body['bitstrings']
        events=c.get('/api/jobs/'+job['id']+'/events');events.raise_for_status()
        assert 'event: result' in events.text and 'qsim 0.22.1' in events.text
        assert c.post('/api/jobs/qasm2',json={**body,'engine':'clifft'},headers={'Idempotency-Key':key}).status_code==409
        item.update(summary=s,wall_seconds=round(time.monotonic()-started,3),max_absolute_error=float(np.max(np.abs(actual-expected))),idempotent_replay=True,engine_conflict=409,sse_result=True,npz_verified=True)
        args.report.write_text(json.dumps(report,indent=2))
        print(json.dumps(item),flush=True)
