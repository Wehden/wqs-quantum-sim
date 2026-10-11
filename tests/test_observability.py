from types import SimpleNamespace as NS
from qsim.observability import summarize


def test_native_resources_scope_and_no_double_counting():
    resource=NS(cpus='2',memory='4')
    name='qs-'+'a'*20
    clusters=[{'name':name,'status':NS(value='UP'),'handle':NS(launched_resources=resource,launched_nodes=2)},
              {'name':'unrelated','status':NS(value='UP'),'handle':NS(launched_resources=resource,launched_nodes=999)}]
    record={'name':name,'cluster_hash':'one','resources':resource,'num_nodes':2,'duration':3600}
    other={**record,'name':'other','cluster_hash':'two'}
    missing={**record,'cluster_hash':'missing','resources':None}
    result=summarize(clusters,[record,record,other,missing])
    assert result['current']['cpus']==4 and result['current']['memory_gib']==8
    assert result['history']['cpu_hours']==4 and result['history']['memory_gib_hours']==8
    assert result['history']['worker_hours']==2
    assert result['history']['unknown_resource_records']==1
    assert len(result['workers'])==1


def test_pod_placement_uses_actual_nodes_and_rejects_ambiguous_or_dead_workers():
    from qsim.observability import pod_placements
    def pod(cluster, node, phase='Running', deleting=None, **labels):
        return NS(metadata=NS(labels={'workload':'qsim','ray-node-type':'head','skypilot-cluster-name':cluster+'-abc123',**labels},deletion_timestamp=deleting),spec=NS(node_name=node),status=NS(phase=phase))
    a,b,c,d,e=['qs-'+letter*20 for letter in 'abcde']
    result=pod_placements([pod(a,'node-a'),pod(a,'node-a'),pod(b,None,'Pending'),
        pod(c,'node-c','Failed'),pod(c,'node-c','Succeeded'),pod(d,'node-d',deleting='now'),
        pod(e,'node-a'),pod(e,'node-b'),pod('unrelated','node-z'),pod(c,'node-c',workload='other')])
    assert result=={a:'node-a',b:None}


def test_placement_failure_is_reported_without_exception_details(monkeypatch):
    from kubernetes import config
    from qsim.observability import collect_placements
    def fail():raise RuntimeError('private connection details')
    monkeypatch.setattr(config,'load_incluster_config',fail)
    result=collect_placements()
    assert result['error']=='RuntimeError' and result['clusters']=={}
    assert 'private' not in str(result)
