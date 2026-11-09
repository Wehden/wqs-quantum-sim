"""Local, read-only release acceptance probes; never change admission or jobs."""
import argparse
import contextlib
import io
import json
import re
import time



# Fixed MIT-licensed upstream fixture, identical to the numerical proof fixture.
SFQ_NETLIST = '* Source: https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/test/ex_jtl_basic.cir\n* MIT License\n* \n* Copyright (c) 2020 Johannes Delport\n* \n* Permission is hereby granted, free of charge, to any person obtaining a copy\n* of this software and associated documentation files (the "Software"), to deal\n* in the Software without restriction, including without limitation the rights\n* to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n* copies of the Software, and to permit persons to whom the Software is\n* furnished to do so, subject to the following conditions:\n* \n* The above copyright notice and this permission notice shall be included in all\n* copies or substantial portions of the Software.\n* \n* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n* IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n* AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n* LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n* OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n* SOFTWARE.\n* Fixed single-pulse JTL fixture adapted from JoSIM v2.7 ex_jtl_basic (MIT).\nB01        3          7          jmitll     area=2.16\nB02        6          8          jmitll     area=2.16\nIB01       0          1          pwl(0      0 5p 280u)\nL01        4          3          2p        \nL02        3          2          2.425p    \nL03        2          6          2.425p    \nL04        6          5          2.031p    \nLP01       0          7          0.086p    \nLP02       0          8          0.096p    \nLPR01      2          1          0.278p    \nLRB01      7          9          0.086p    \nLRB02      8          10         0.086p    \nRB01       9          3          5.23      \nRB02       10         6          5.23      \nROUT       5          0          2         \nVIN        4          0          pwl(0 0 100p 0 102.5p 827.13u 105p 0)\n.model jmitll jj(rtype=1, vg=2.8mV, cap=0.07pF, r0=160, rN=16, icrit=0.1mA)\n.tran 0.25p 250p\n.print DEVV B01\n.print PHASE B01\n.print DEVV B02\n.print PHASE B02\n.print DEVI ROUT\n.end\n'

def release_cases(exported=None):
    result = {'clifft_stim': {'engine': 'clifft', 'input_format': 'stim',
                             'operation': 'sample', 'circuit': 'X 0\nM 0', 'shots': 16, 'seed': 42}}
    for engine, formats, operations in (
            ('clifft', ('qasm2',), ('basis_probabilities',)),
            ('qsim', ('qasm2', 'qasm3'), ('sample', 'basis_probabilities')),
            ('aer', ('qasm2', 'qasm3'), ('sample',))):
        for fmt in formats:
            for operation in operations:
                if fmt == 'qasm2':
                    circuit = 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; '
                    circuit += 'x q[0];' if operation == 'basis_probabilities' else 'creg c[1]; x q[0]; measure q[0] -> c[0];'
                else:
                    circuit = 'OPENQASM 3.0; include "stdgates.inc"; qubit[1] q; '
                    circuit += 'x q[0];' if operation == 'basis_probabilities' else 'bit[1] c; x q[0]; c[0] = measure q[0];'
                # Exercise classical feed-forward and reset on the Aer path.
                if engine == 'aer':
                    circuit += (' reset q[0]; if(c==1) x q[0]; measure q[0] -> c[0];'
                                if fmt == 'qasm2' else ' reset q[0]; if(c == 1) { x q[0]; } c[0] = measure q[0];')
                key = f'{engine}_{fmt}_' + ('probability' if operation == 'basis_probabilities' else 'sample')
                result[key] = {'engine': engine, 'input_format': fmt, 'operation': operation, 'circuit': circuit}
                result[key].update({'bitstrings': ['0', '1']} if operation == 'basis_probabilities'
                                   else {'shots': 16, 'seed': 42})
    for operation, solver in (('evolve','se'),('evolve','me'),('evolve','mc'),
                              ('propagator','se'),('propagator','me'),('spectrum',None),('steadystate',None)):
        body={'engine':'lindblad','input_format':'lindblad_model','schema_version':1,'operation':operation,
              'model':{'modes':[{'name':'q','kind':'oscillator','dimension':2,'frequency':.017}]}}
        if solver: body.update(solver=solver,times=[0.,.5,1.])
        if operation=='evolve':
            body.update(initial_state={'kind':'product','modes':{'q':{'kind':'fock','index':1}}},
                observables=[{'name':'n','operator':{'kind':'product','factors':{'q':'n'}}}],store_states='all')
        if solver in ('me','mc') or operation=='steadystate':
            body['model']['collapse']=[{'kind':'relaxation','mode':'q','T1':3.}]
        if solver=='mc':body.update(trajectories=8,seed=42)
        if operation=='spectrum':body.update(evals_count=2,return_vectors=True)
        if operation=='steadystate':body.update(return_state=True,
            observables=[{'name':'n','operator':{'kind':'product','factors':{'q':'n'}}}])
        result['lindblad_'+operation+('_'+solver if solver else '')]=body
    result['lindblad_mc_repeat']=json.loads(json.dumps(result['lindblad_evolve_mc']))
    for operation in ('spectrum','matrix_elements','export_for_lindblad'):
        body={'engine':'scqubits','input_format':'scqubits_model','schema_version':1,
              'operation':operation,'evals_count':4,
              'model':{'qubit':'Transmon','params':{'EJ':15.,'EC':.3,'ng':0.,'ncut':30}}}
        if operation=='spectrum':body['return_vectors']=True
        if operation=='matrix_elements':body['operator']='n_operator'
        if operation=='export_for_lindblad':body['operators']=['n_operator']
        result['scqubits_'+operation]=body
    result['scqubits_export_lindblad']=exported
    result['josim_sfq']={'engine':'josim','input_format':'josim_netlist','schema_version':1,
                         'operation':'transient','netlist':SFQ_NETLIST}
    return result



def probe(sha, owner, job_ids, native=False, sky=None, worker_image=None):
    from . import release_control, settings, store
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or not owner:
        raise ValueError('Release and owner required')
    versioned = type(job_ids) is dict
    inventory = release_cases()
    if versioned:
        if (not set(job_ids) <= set(inventory) or len(set(job_ids.values())) != len(job_ids)
                or not isinstance(worker_image,str)
                or not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}',worker_image)):
            raise ValueError('Invalid named smoke selection or image')
        selection = job_ids
        if native and set(selection) != set(inventory):
            raise ValueError('Complete named smoke inventory required')
    else:
        # Read-only compatibility for the original circuit-only probe protocol.
        if type(job_ids) is not list or len(job_ids)>8 or len(set(job_ids))!=len(job_ids):
            raise ValueError('Invalid legacy smoke selection')
        selection = {str(i):job for i,job in enumerate(job_ids)}
    if any(not isinstance(job,str) or not re.fullmatch(r'[0-9a-f]{32}',job) for job in selection.values()):
        raise ValueError('Invalid smoke job selection')
    with store.connect() as db:
        state = release_control.snapshot(db)
        if state['release'] != sha or state['smoke_owner'] != owner:
            raise ValueError('Release smoke admission required')
        jobs = {}
        for name,job in selection.items():
            row = db.execute('SELECT j.*,d.cluster,d.launch_id,d.profile_json,d.native_job_id '
                             'FROM jobs j JOIN job_dispatch d ON d.job_id=j.id '
                             'WHERE j.id=? AND j.owner=?',(job,owner)).fetchone()
            if row is None:raise ValueError('Smoke job identity mismatch')
            jobs[name] = dict(row)
    observation = json.loads((settings.DATA / 'skypilot-observation.json').read_text())
    heartbeat = float((settings.DATA / 'controller-heartbeat').read_text())
    now = time.time()
    fresh = lambda value: isinstance(value, (int, float)) and 0 <= now - value <= 180
    if (not fresh(heartbeat) or not fresh(observation.get('observed_at'))
            or not fresh(observation.get('queues_observed_at'))
            or observation.get('last_error') or observation.get('queue_errors')
            or observation.get('queues_complete') is not True):
        raise ValueError('Controller or observer not healthy')
    result = {'heartbeat':heartbeat,'observer_at':observation['observed_at'],
              'cleaned':all(j['cleaned'] and j['status']=='SUCCEEDED' for j in jobs.values())}
    records = {}
    if versioned:
        import hashlib
        import numpy as np
        from .models import Submission
        from .physics_submission import normalize_physics_submission, canonical_physics_request
        from .physics_results import validate_execution_context, validate_physics_callback
        handoff = None
        if 'scqubits_export_lindblad' in jobs:
            parent = jobs.get('scqubits_export_for_lindblad')
            if parent is None or parent['status']!='SUCCEEDED':raise ValueError('Validated export required')
            path=settings.DATA/parent['id']/'result.npz'
            if path.stat().st_size>9*1024**2:raise ValueError('Smoke export too large')
            raw=path.read_bytes()
            validate_physics_callback(raw,json.loads(parent['summary']),json.loads(parent['request']),json.loads(parent['profile_json']))
            with np.load(io.BytesIO(raw),allow_pickle=False) as arrays:
                handoff=json.loads(str(arrays['metadata_json']))['lindblad_request']
            inventory=release_cases(handoff)
        for name,job in jobs.items():
            expected=inventory[name]
            physics=expected['engine'] in ('lindblad','scqubits','josim')
            context=None
            if physics:
                normalized=normalize_physics_submission(expected)
                canonical=canonical_physics_request(normalized)
                context=json.loads(job['profile_json'])
                if context['version']!=2 or context['worker_profile']['image']!=worker_image:
                    raise ValueError('Release physics image/context mismatch')
                validate_execution_context(context,normalized.request.admission_plan(),
                                           expected_profile=settings._certified_physics_profiles['worker'])
            else:
                normalized=Submission(**expected).model_dump()
                if normalized['engine']=='clifft':normalized.pop('engine')
                if normalized['input_format'] in ('stim','qasm2') and normalized.get('bindings')=={}:
                    normalized.pop('bindings')
                canonical=json.dumps(normalized,sort_keys=True,separators=(',',':'))
                if job['profile_json'] is not None:raise ValueError('Circuit profile mismatch')
            if ((physics and canonical!=job['request']) or
                    (not physics and json.loads(job['request'])!=Submission(**expected).model_dump())):
                raise ValueError('Smoke canonical request mismatch')
            record={'id':job['id'],'owner':owner,'source_sha':sha,'engine':expected['engine'],
                'operation':expected['operation'],'request_sha256':hashlib.sha256(canonical.encode()).hexdigest(),
                'worker_image':worker_image,'profile_id':context['worker_profile']['id'] if context else 'circuit',
                'context_sha256':hashlib.sha256(job['profile_json'].encode()).hexdigest() if context else None,
                'cleaned':bool(job['cleaned']),'result_sha256':None}
            if job['status']=='SUCCEEDED':
                path=settings.DATA/job['id']/'result.npz'
                if path.stat().st_size>9*1024**2:raise ValueError('Smoke result too large')
                raw=path.read_bytes()
                if physics:validate_physics_callback(raw,json.loads(job['summary']),json.loads(canonical),context)
                record['result_sha256']=hashlib.sha256(raw).hexdigest()
            records[name]=record
        result['jobs']=records
    if native:
        if not jobs or not result['cleaned']:raise ValueError('Completed smoke jobs required')
        if sky is None:import sky
        queues,handles={},{}
        for name,job in jobs.items():
            if not job['launch_id'] or not job['cluster']:raise ValueError('Missing native job identity')
            cluster=job['cluster']
            native_id=job['native_job_id']
            if native_id is None:native_id,_=sky.get(job['launch_id'])
            if type(native_id) is not int or native_id<1:raise ValueError('Missing native job id')
            if cluster not in queues:
                queues[cluster]=sky.get(sky.queue(cluster,skip_finished=False,all_users=True))
                if versioned:
                    from sky.utils.common import StatusRefreshMode
                    statuses=sky.get(sky.status(cluster_names=[cluster],refresh=StatusRefreshMode.FORCE,all_users=True))
                    matches=[r for r in statuses if r['name']==cluster]
                    if len(matches)!=1 or getattr(matches[0]['status'],'value',matches[0]['status'])!='UP':
                        raise ValueError('Native allocation unavailable')
                    handle=matches[0]['handle'];resources=handle.launched_resources
                    image=resources.extract_docker_image()
                    cpus=float(str(resources.cpus).rstrip('+'));memory=float(str(resources.memory).rstrip('+'))
                    if image!=worker_image or handle.launched_nodes!=1 or resources.accelerators or cpus<2 or memory<4:
                        raise ValueError('Native allocation profile/image mismatch')
                    handles[cluster]={'worker_image':image,'cpus':cpus,'memory_gib':memory,'backend':'cpu'}
            matches=[r for r in queues[cluster] if r['job_id']==native_id]
            if len(matches)!=1:raise ValueError('Native smoke job not successful')
            status=getattr(matches[0]['status'],'value',matches[0]['status'])
            if status in ('INIT','PENDING','SETTING_UP','RUNNING'):
                result['native_succeeded']=False
                return result
            if status!='SUCCEEDED':raise ValueError('Native smoke job not successful')
            if versioned:
                records[name].update(cluster=cluster,native_job_id=native_id,native_status=status,
                                      allocation=handles[cluster])
        result['native_succeeded']=True
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--jobs', default='[]')
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--worker-image')
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = probe(args.release, args.owner, json.loads(args.jobs), args.native, worker_image=args.worker_image)
        print(json.dumps(result))
    except Exception:
        raise SystemExit('Release probe failed; admission unchanged.') from None


if __name__ == '__main__':
    main()
