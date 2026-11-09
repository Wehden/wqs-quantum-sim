"""Published physics JSON agrees with schema, admission and packaged execution."""
import html
import json
from pathlib import Path
import re

import jsonschema
import numpy as np
import pytest

from qsim.physics_submission import canonical_physics_request, normalize_physics_submission, physics_schema
from qsim.release_probe import release_cases
from test_physics_lifecycle import joined, complete, arrays

ROOT=Path(__file__).resolve().parents[1]
CASES={
    **{'physics-lindblad-'+suffix.replace('_','-'):'lindblad_'+suffix
       for suffix in ('evolve_se','evolve_me','evolve_mc','propagator_se','propagator_me','spectrum','steadystate')},
    'physics-scqubits-spectrum':'scqubits_spectrum',
    'physics-scqubits-matrix-elements':'scqubits_matrix_elements',
    'physics-scqubits-export':'scqubits_export_for_lindblad',
    'physics-josim-sfq':'josim_sfq',
}
PRIMARY=('physics-lindblad-evolve-se','physics-scqubits-export','physics-josim-sfq')
DOCUMENTED=[(path,name) for path in ('docs/physics-engines.md','qsim/static/index.html') for name in CASES]
DOCUMENTED += [('README.md','physics-lindblad-evolve-se')]
DOCUMENTED += [('qsim/static/agents.md',name) for name in PRIMARY]


def example(path,name):
    text=(ROOT/path).read_text()
    pattern=(r'<pre id="example-'+re.escape(name)+r'"><code>(.*?)</code></pre>' if path.endswith('.html') else
             r'<!-- example: '+re.escape(name)+r' -->\s*```json\n(.*?)\n```')
    found=re.findall(pattern,text,re.S)
    assert len(found)==1, (path,name,len(found))
    return json.loads(html.unescape(found[0])) if path.endswith('.html') else json.loads(found[0])


@pytest.mark.parametrize('path,name',DOCUMENTED)
def test_documented_physics_json_matches_schema_admission_and_release_inventory(path,name):
    body=example(path,name)
    engine=body['engine']
    normalized=normalize_physics_submission(body,explicit_engine=engine)
    jsonschema.validate(body,physics_schema(engine,1))
    plan=normalized.request.admission_plan()
    assert plan.peak_bytes<=2*1024**3
    frozen=normalize_physics_submission(release_cases()[CASES[name]],explicit_engine=engine)
    assert canonical_physics_request(normalized)==canonical_physics_request(frozen)


def test_guides_explain_guarded_cpu_availability_and_preserve_agent_download():
    for path in ('README.md','qsim/static/index.html','qsim/static/agents.md','docs/physics-engines.md'):
        text=(ROOT/path).read_text()
        assert '/api/capabilities' in text and 'CPU' in text and 'acceptance' in text.lower()
        assert all('/api/jobs/'+engine in text for engine in ('lindblad','scqubits','josim'))
        assert all('/api/schemas/'+engine+'/1.json' in text for engine in ('lindblad','scqubits','josim'))
    page=(ROOT/'qsim/static/index.html').read_text()
    assert '<a href="#start">Developer guide</a>' in page
    assert 'href="/agents.md" download=' in page
    assert '<section id="physics">' in page
    assert all(term in (ROOT/'docs/physics-engines.md').read_text()
               for term in ('evolve','propagator','steadystate','Transmon','TunableTransmon','Fluxonium','FluxQubit','allow_pickle=False'))


@pytest.mark.parametrize('name',CASES)
def test_packaged_physics_examples(joined,name):
    body=example('docs/physics-engines.md',name)
    engine=body['engine']
    identifier=complete(joined,engine,body)
    result,metadata=arrays(joined,identifier)
    from tools.cd.acceptance import verify_result
    summary=joined.client.get('/api/jobs/'+identifier).json()['summary']
    artifact=joined.client.get('/api/jobs/'+identifier+'/result').content
    verified=verify_result(body,summary,artifact,name=CASES[name])
    assert metadata['engine']==engine and metadata['backend']=='cpu'
    assert all(np.isfinite(value).all() for value in result.values())
    if name=='physics-lindblad-evolve-se':
        np.testing.assert_allclose(result['expectations'][:,0],1.,atol=1e-7)
        np.testing.assert_allclose(result['states'][:,1],np.exp(-2j*np.pi*.017*result['times']),atol=1e-7)
    elif name=='physics-lindblad-evolve-me':
        np.testing.assert_allclose(result['expectations'][:,0],np.exp(-result['times']/3),atol=1e-7)
    elif name=='physics-lindblad-evolve-mc':
        repeated,_=arrays(joined,complete(joined,engine,body))
        for member in result:np.testing.assert_array_equal(result[member],repeated[member])
        np.testing.assert_allclose(np.trace(result['states'],axis1=1,axis2=2),1.,atol=1e-7)
    elif name=='physics-lindblad-propagator-se':
        for time,propagator in zip(result['times'],result['propagators']):
            np.testing.assert_allclose(propagator,np.diag([1.,np.exp(-2j*np.pi*.017*time)]),atol=1e-7)
    elif name=='physics-lindblad-propagator-me':
        excited=np.asarray([[0.,0.],[0.,1.]],dtype=complex).reshape(-1,order='F')
        for time,propagator in zip(result['times'],result['propagators']):
            density=(propagator@excited).reshape((2,2),order='F')
            np.testing.assert_allclose(density,np.diag([1-np.exp(-time/3),np.exp(-time/3)]),atol=1e-7)
    elif name=='physics-lindblad-spectrum':
        np.testing.assert_allclose(result['eigenvalues'],[0.,.017],atol=1e-12)
    elif name=='physics-lindblad-steadystate':
        np.testing.assert_allclose(result['steady_state'],np.diag([1.,0.]),atol=1e-10)
    elif name=='physics-scqubits-spectrum':
        vectors=result['eigenvectors'];np.testing.assert_allclose(vectors.conj().T@vectors,np.eye(4),atol=1e-9)
        assert np.all(np.diff(result['eigenvalues'])>=0)
    elif name=='physics-scqubits-matrix-elements':
        matrix=result['matrix_elements'];np.testing.assert_allclose(matrix,matrix.conj().T,atol=1e-12)
    elif name=='physics-scqubits-export':
        target=metadata['lindblad_request']
        followup=complete(joined,'lindblad',target)
        roundtrip,_=arrays(joined,followup)
        verify_result(target,joined.client.get('/api/jobs/'+followup).json()['summary'],
            joined.client.get('/api/jobs/'+followup+'/result').content,name='scqubits_export_lindblad',
            related={'scqubits_export_for_lindblad':verified})
        assert result['eigenvalues'][0]==0
        np.testing.assert_array_equal(result['hamiltonian'],np.diag(result['eigenvalues']))
        np.testing.assert_allclose(roundtrip['eigenvalues'],result['eigenvalues'],atol=1e-9,rtol=0)
    else:
        times=result['times'];window=(times>=50e-12)&(times<=200e-12)
        for column in (0,2):assert abs(np.trapezoid(result['traces'][window,column],times[window])/2.0678338484619295e-15-1)<.02
