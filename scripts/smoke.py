#!/usr/bin/env python3
"""Run an authenticated Bell circuit through the live service and validate its artifact."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import time
import httpx
import numpy as np

p=argparse.ArgumentParser()
p.add_argument('--base', default='http://127.0.0.1:18089')
p.add_argument('--key-file',type=Path,default=Path('var/owner-invitation.key'))
p.add_argument('--port-forward',action='store_true')
p.add_argument('--timeout',type=int,default=420)
a=p.parse_args()
forward=None
try:
    if a.port_forward:
        forward=subprocess.Popen(['kubectl','-n','qsim','port-forward','service/qsim','18089:8080'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                if httpx.get(a.base+'/healthz',timeout=1).status_code==200:break
            except httpx.TransportError:pass
            time.sleep(.25)
        else:raise RuntimeError('Port-forward did not start')
    key=a.key_file.read_text().strip()
    assert httpx.get(a.base+'/api/jobs',timeout=20).status_code==401
    with httpx.Client(base_url=a.base,headers={'Authorization':'Bearer '+key},timeout=30) as c:
        r=c.post('/api/jobs',json={'name':'Bell pair: end-to-end verification','circuit':'H 0\nCX 0 1\nM 0 1','shots':1000,'seed':42})
        r.raise_for_status(); job=r.json(); start=time.monotonic(); state=None
        Path('var/smoke-job.json').write_text(r.text)
        while time.monotonic()-start<a.timeout:
            r=c.get('/api/jobs/'+job['id']); r.raise_for_status();job=r.json()
            if job['status']!=state:
                state=job['status'];print(job['id'],state,flush=True)
            if state in ('FAILED','CANCELLED'):raise RuntimeError(job['error'] or state)
            if state=='SUCCEEDED':break
            time.sleep(5)
        else:raise TimeoutError('Job did not finish within smoke-test deadline; it remains tracked by Qsim')
        result=c.get('/api/jobs/'+job['id']+'/result');result.raise_for_status()
        with np.load(io.BytesIO(result.content),allow_pickle=False) as data:
            m=data['measurements'];assert m.shape==(1000,2);assert np.all(m[:,0]==m[:,1])
            assert {tuple(row) for row in m}=={(0,0),(1,1)}
        job['verification_elapsed_seconds']=round(time.monotonic()-start,2)
        Path('var/smoke-result.json').write_text(json.dumps(job,indent=2))
        print(json.dumps({'status':'PASS','elapsed_seconds':job['verification_elapsed_seconds'],'summary':job['summary']},indent=2),flush=True)
finally:
    if forward:
        forward.terminate()
        try:forward.wait(timeout=5)
        except subprocess.TimeoutExpired:forward.kill()
