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
