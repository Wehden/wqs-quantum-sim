"""Stored execution/result contracts fail closed without native allocation."""
import base64
from dataclasses import asdict
import io
import json
import zipfile
import numpy as np
import pytest
from qsim.physics_results import (ArraySpec, AllocationPlan, PhysicsExecutionResult, plan_result,
    serialize_physics_result, allocation_plan_digest, validate_execution_context, validate_physics_callback, make_execution_context)
from qsim import settings


def contract():
    result=plan_result((ArraySpec('eigenvalues','float64',(2,),None),),metadata_characters=65536,summary_bytes=65536)
    plan=AllocationPlan('scqubits-cpu-evr-v1','native',{'hilbert':2},(),0,{},result)
    profile=json.loads(json.dumps(settings._certified_physics_profiles['worker']))
    profile['image']='registry.example.invalid/worker@sha256:'+'a'*64
    profile['resources']['infra']='k8s'
    context=make_execution_context(plan,profile)
    context=json.loads(json.dumps(context))
    request=dict(engine='scqubits',operation='spectrum',schema_version=1)
    summary,artifact=serialize_physics_result(PhysicsExecutionResult({'eigenvalues':np.array([0.,1.])},
        {**request,'backend':'cpu','precision':'float64/complex128','policy_revision':plan.policy_revision,'algorithm':plan.algorithm},{}),result)
    return plan,context,request,summary,base64.b64decode(artifact)


def test_context_and_callback_bind_sealed_contract():
    plan,ctx,request,summary,raw=contract()
    validate_execution_context(ctx,plan,expected_profile=settings._certified_physics_profiles['worker'])
    validate_physics_callback(raw,summary,request,ctx)


@pytest.mark.parametrize('field',['allocation_plan_digest','algorithm','admission_policy_revision','result_plan','worker_profile','version','extra'])
def test_context_tampering_refused(field):
    plan,ctx,*_=contract()
    if field=='worker_profile':ctx[field]['isolation']['address_space_bytes']*=2
    elif field=='result_plan':ctx[field]['numeric_bytes']+=8
    elif field=='version':ctx[field]=True
    else:ctx[field]='tampered'
    with pytest.raises(ValueError,match='execution_policy_mismatch'):
        validate_execution_context(ctx,plan,expected_profile=settings._certified_physics_profiles['worker'])


@pytest.mark.parametrize('kind',['object','duplicate','traversal','compressed','wrong_shape','nonfinite','missing','metadata','summary'])
def test_archive_refuses_hostile_or_mismatched_members(kind):
    _,ctx,request,summary,raw=contract()
    if kind=='summary':summary['arrays']['eigenvalues']['shape']=[1]
    else:
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED if kind=='compressed' else zipfile.ZIP_STORED) as archive:
            arrays={'eigenvalues':np.array([0.,1.]),'metadata_json':np.asarray(json.dumps({**request,'backend':'cpu','precision':'float64','work':{}}))}
            if kind=='object':arrays['eigenvalues']=np.asarray([object()],dtype=object)
            if kind=='wrong_shape':arrays['eigenvalues']=np.array([0.])
            if kind=='nonfinite':arrays['eigenvalues']=np.array([0.,np.inf])
            if kind=='missing':arrays.pop('eigenvalues')
            if kind=='metadata':arrays['metadata_json']=np.asarray(json.dumps({'engine':'josim','operation':'transient','work':{}}))
            for name,array in arrays.items():
                member=io.BytesIO();np.lib.format.write_array(member,array,allow_pickle=kind=='object')
                archive.writestr(('../' if kind=='traversal' else '')+name+'.npy',member.getvalue())
                if kind=='duplicate':archive.writestr(name+'.npy',member.getvalue())
        raw=out.getvalue()
    with pytest.raises(ValueError,match='invalid_result|resource_limit'):
        validate_physics_callback(raw,summary,request,ctx)


@pytest.mark.parametrize('field,value',[('backend','gpu'),('precision','float32'),
    ('policy_revision','unrelated-policy'),('algorithm','unrelated-algorithm')])
def test_self_consistent_but_incompatible_result_receipt_is_refused(field,value):
    plan,ctx,request,_,_=contract()
    metadata={**request,'backend':'cpu','precision':'float64/complex128',
              'policy_revision':plan.policy_revision,'algorithm':plan.algorithm}
    metadata[field]=value
    summary,artifact=serialize_physics_result(PhysicsExecutionResult({'eigenvalues':np.array([0.,1.])},metadata,{}),plan.result)
    with pytest.raises(ValueError,match='invalid_result'):
        validate_physics_callback(base64.b64decode(artifact),summary,request,ctx)


@pytest.mark.parametrize('field,value',[('version',999),('version',True),
    ('allocation_plan_digest','not-a-digest'),('extra','unknown')])
def test_unknown_or_corrupt_stored_context_cannot_commit_success(field,value):
    _,ctx,request,summary,raw=contract()
    ctx[field]=value
    with pytest.raises(ValueError,match='invalid_result'):
        validate_physics_callback(raw,summary,request,ctx)
