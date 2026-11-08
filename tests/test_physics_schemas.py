"""Versioned structural schemas and discovery stay native-import-free."""
import json
import subprocess
import sys

import jsonschema
import pytest
from qsim.physics_submission import normalize_physics_submission, canonical_physics_request, physics_schema
from test_service import client, auth
from test_physics_api import scqubits, enabled


@pytest.mark.parametrize('engine',['lindblad','scqubits','josim'])
def test_schema_route_model_parity(client,engine):
    response=client.get('/api/schemas/'+engine+'/1.json',headers=auth())
    assert response.status_code==200
    assert response.json()==physics_schema(engine,1)
    jsonschema.Draft202012Validator.check_schema(response.json())
    assert 'semantic' in response.json()['description'].lower()
    assert client.get('/api/jobs',headers=auth()).json()==[]
    assert client.get('/api/schemas/'+engine+'/2.json',headers=auth()).status_code==404


def test_structural_schema_and_runtime_agree_on_controls():
    payload={**scqubits(),'engine':'scqubits','input_format':'scqubits_model'}
    schema=physics_schema('scqubits',1)
    jsonschema.validate(payload,schema)
    normalize_physics_submission(payload)
    for patch in ({'schema_version':2},{'schema_version':True},{'shots':4},{'backend':'cuda'}):
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate({**payload,**patch},schema)
        with pytest.raises(ValueError):normalize_physics_submission({**payload,**patch})


def test_capabilities_only_advertise_enabled_certified_cells(client,monkeypatch):
    enabled(monkeypatch)
    engines=client.get('/api/capabilities',headers=auth()).json()['engines']
    assert engines['scqubits']['input_formats']=={'scqubits_model':['spectrum','matrix_elements']}
    assert 'lindblad' not in engines and 'josim' not in engines
    assert engines['scqubits']['device']=='CPU'


def test_api_import_has_no_native_or_scheduling_side_effects():
    subprocess.run([sys.executable,'-c',"""
import sys
from qsim.api import app
assert not any(n in sys.modules for n in ('qutip','scqubits','scipy','sky','cupy','qsim.worker','qsim.controller'))
"""],check=True)


def test_unknown_schema_rejected_and_canonical_identity_excludes_policy():
    with pytest.raises(ValueError):physics_schema('other',1)
    with pytest.raises(ValueError):physics_schema('scqubits',2)
    explicit=normalize_physics_submission(scqubits(),explicit_engine='scqubits')
    generic=normalize_physics_submission({**scqubits(),'engine':'scqubits','input_format':'scqubits_model',
                                         'name':'Untitled simulation','return_vectors':False})
    assert canonical_physics_request(explicit)==canonical_physics_request(generic)
    assert 'profile' not in canonical_physics_request(explicit)
