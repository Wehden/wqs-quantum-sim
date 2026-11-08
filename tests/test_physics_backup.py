"""Real serializer/encryption/native-restore format coverage using synthetic data.

The default records exercise numeric/complex/Unicode backup compatibility, not
native simulator acceptance. The joined packaged-image harness feeds its actual
native records to assert_physics_restore to cover that separate obligation.
"""
import base64
import copy
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import zipfile

from cryptography.fernet import Fernet
import numpy as np
import pytest

from qsim import store
from qsim.physics_results import ArraySpec, PhysicsExecutionResult, plan_result, serialize_physics_result
from tools.backup.capture import payload
from tools.backup.metadata import digest
from tools.backup.restore import restore
from test_backup_restore import backup, identities


def format_records():
    """Create actual production-serializer archives; do not claim native physics."""
    records = []
    cases = [
        ('lindblad', 'lindblad_model', 'evolve', {
            'times': np.asarray([0., 1.]),
            'states': np.asarray([[1., 0.], [0., 1.j]], dtype=np.complex128)}),
        ('scqubits', 'scqubits_model', 'matrix_elements', {
            'eigenvalues': np.asarray([0., 1.]),
            'matrix_elements': np.asarray([[0., 1.j], [-1.j, 0.]], dtype=np.complex128)}),
        ('josim', 'josim_netlist', 'transient', {
            'times': np.asarray([0., 1e-12]),
            'traces': np.asarray([[0., 0.], [1e-6, np.pi]])}),
    ]
    for index, (engine, fmt, operation, arrays) in enumerate(cases, 1):
        result_plan = plan_result(tuple(ArraySpec(name, value.dtype.name, value.shape, None)
                                       for name, value in arrays.items()),
                                  metadata_characters=65536, summary_bytes=65536)
        summary, encoded = serialize_physics_result(PhysicsExecutionResult(arrays,
            {'engine':engine, 'operation':operation, 'schema_version':1,
             'label':'Synthetic format-only Ω ψ fixture'}, {}), result_plan)
        records.append({'id':str(index)*32,
            'request': {'engine':engine, 'input_format':fmt, 'schema_version':1,
                        'operation':operation, 'name':'Synthetic format-only fixture'},
            'context': {'version':2, 'worker_profile': {
                'id':'synthetic-format-only', 'image':'registry.example.invalid/worker@sha256:'+'a'*64},
                'admission_policy_revision':'synthetic-format-only',
                'allocation_plan_digest':hashlib.sha256(('synthetic-format-only:'+engine).encode()).hexdigest(),
                'algorithm':'synthetic-format-only', 'result_plan':asdict(result_plan)},
            'summary':summary, 'artifact':base64.b64decode(encoded)})
    return records


def assert_physics_restore(snapshot, tmp_path, records):
    """Restore all-three records and check artifacts, durable context and invites.

    records is a list of dictionaries with id (32 hex), normalized request,
    context (stored dispatch mapping), summary, and artifact (actual NPZ bytes).
    The source of those records determines whether this is format or native
    evidence; this helper never substitutes a model, certificate or executable.
    """
    assert {record['request']['engine'] for record in records} == {'lindblad','scqubits','josim'}
    assert len({record['id'] for record in records}) == len(records)
    source, _, original_manifest, destinations = snapshot
    manifest = copy.deepcopy(original_manifest)
    dependencies = manifest['dependencies']
    expected = {}
    with store.connect() as db:
        invitations = [tuple(row) for row in db.execute('SELECT * FROM key_requests ORDER BY id')]
        for record in records:
            identifier = record['id']
            assert len(identifier) == 32 and all(c in '0123456789abcdef' for c in identifier)
            assert record['context']['version'] == 2 and 'result_plan' in record['context']
            request = json.dumps(record['request'], sort_keys=True, separators=(',', ':'), allow_nan=False)
            context = json.dumps(record['context'], sort_keys=True, separators=(',', ':'), allow_nan=False)
            summary = json.dumps(record['summary'], sort_keys=True, separators=(',', ':'), allow_nan=False)
            key_hash = hashlib.sha256(('synthetic-restore:'+identifier).encode()).hexdigest()
            request_hash = hashlib.sha256(request.encode()).hexdigest()
            cluster = 'synthetic-restore-'+identifier
            db.execute("INSERT INTO jobs(id,owner,name,status,created,updated,request,callback_hash,summary,cleaned) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (identifier,'sample',record['request']['name'],'SUCCEEDED',1.,2.,request,
                        'synthetic-callback-hash',summary,1))
            db.execute('INSERT INTO job_idempotency(owner,key_hash,request_hash,job_id,created) VALUES(?,?,?,?,?)',
                       ('sample',key_hash,request_hash,identifier,1.))
            db.execute('INSERT INTO job_dispatch(job_id,launch_id,cluster,profile_json) VALUES(?,?,?,?)',
                       (identifier,'synthetic-native-ack',cluster,context))
            profile = json.dumps(record['context']['worker_profile'], sort_keys=True, separators=(',', ':'), allow_nan=False)
            db.execute('INSERT INTO worker_pool(cluster,job_id,created,released,image,profile_json) VALUES(?,?,?,?,?,?)',
                       (cluster,None,1.,2.,record['context']['worker_profile']['image'],profile))
            expected[identifier] = (request,context,summary,key_hash,request_hash,cluster,profile)
            path = 'results/'+identifier+'.npz'
            artifact = record['artifact']
            assert isinstance(artifact, bytes)
            (source/path).parent.mkdir(exist_ok=True)
            (source/path).write_bytes(artifact)
            dependencies['results'].append(path)
            manifest['inventory'].append({'path':path,'kind':'regular','role':'results',**digest(artifact)})
    with sqlite3.connect(source/'jobs.sqlite3') as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    database = (source/'jobs.sqlite3').read_bytes()
    (source/dependencies['database']).write_bytes(database)
    next(entry for entry in manifest['inventory'] if entry['role']=='database').update(digest(database))
    archive = tmp_path/'physics-snapshot.tar'
    root = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    try:
        payload(root, manifest, archive)
    finally:
        os.close(root)
    key_dir = tmp_path/'restore-identities'
    key_dir.mkdir()
    age, keys, recipients = identities(key_dir)
    encrypted = tmp_path/'physics.age'
    subprocess.run([age,'-r',recipients[0],'-o',str(encrypted),str(archive)], check=True)
    outcome = restore(encrypted,keys[0],age,hashlib.sha256(Path(age).read_bytes()).hexdigest(),None,destinations)
    assert outcome == {'evidence_type':'final_result','verified':True,'backup_id':manifest['context']['set_id']}
    assert Path(destinations['data_dir']).resolve() != source.resolve()
    with sqlite3.connect(Path(destinations['data_dir'])/'jobs.sqlite3') as db:
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert [tuple(row) for row in db.execute('SELECT * FROM key_requests ORDER BY id')]==invitations
        for record in records:
            identifier = record['id']
            request,context,summary,key_hash,request_hash,cluster,profile = expected[identifier]
            assert db.execute('SELECT request,summary,status,cleaned FROM jobs WHERE id=?',(identifier,)).fetchone()==(request,summary,'SUCCEEDED',1)
            assert db.execute('SELECT profile_json,cluster FROM job_dispatch WHERE job_id=?',(identifier,)).fetchone()==(context,cluster)
            assert db.execute('SELECT request_hash,job_id FROM job_idempotency WHERE owner=? AND key_hash=?',('sample',key_hash)).fetchone()==(request_hash,identifier)
            assert db.execute('SELECT profile_json,job_id FROM worker_pool WHERE cluster=?',(cluster,)).fetchone()==(profile,None)
            restored = (Path(destinations['data_dir'])/identifier/'result.npz').read_bytes()
            assert restored == record['artifact']
            with np.load(io.BytesIO(restored),allow_pickle=False) as actual, np.load(io.BytesIO(record['artifact']),allow_pickle=False) as original:
                assert actual.files == original.files
                for name in actual.files:
                    np.testing.assert_array_equal(actual[name],original[name])
                    assert not actual[name].dtype.hasobject
    for role, target in [('invitation_registry','registry_file'),('pending_delivery_key','delivery_key_file')]:
        assert Path(destinations[target]).read_bytes() == (source/dependencies[role]).read_bytes()
    registry = json.loads(Path(destinations['registry_file']).read_text())
    cipher = Fernet(Path(destinations['delivery_key_file']).read_bytes())
    for row in invitations:
        with sqlite3.connect(source/'jobs.sqlite3') as db:
            db.row_factory = sqlite3.Row
            invitation = db.execute('SELECT * FROM key_requests WHERE id=?',(row[0],)).fetchone()
        if invitation['key_cipher']:
            delivered = json.loads(cipher.decrypt(invitation['key_cipher'].encode()))
            assert any(user['id']==delivered['identity'] and user['sha256']==hashlib.sha256(delivered['key'].encode()).hexdigest() for user in registry)
    return destinations, manifest


def test_complex_unicode_physics_artifacts_and_context_restore(backup,tmp_path):
    records = format_records()
    destinations, _ = assert_physics_restore(backup,tmp_path,records)
    for record in records:
        with np.load(Path(destinations['data_dir'])/record['id']/'result.npz',allow_pickle=False) as result:
            assert result['metadata_json'].dtype.kind=='U'
            assert 'Ω ψ' in str(result['metadata_json'])
    with np.load(Path(destinations['data_dir'])/records[0]['id']/'result.npz',allow_pickle=False) as result:
        assert result['states'].dtype==np.dtype('complex128')
        assert result['states'][1,1]==1.j


@pytest.mark.parametrize('hostile',['object','duplicate','traversal'])
def test_hostile_physics_npz_never_returns_verified_restore(backup,tmp_path,hostile):
    records = format_records()
    bad = io.BytesIO()
    if hostile=='object':
        np.savez(bad,states=np.asarray([object()],dtype=object),metadata_json=np.asarray('{}'))
    else:
        with zipfile.ZipFile(io.BytesIO(records[0]['artifact'])) as original, zipfile.ZipFile(bad,'w') as modified:
            for item in original.infolist():
                modified.writestr(item.filename,original.read(item))
            first = original.infolist()[0]
            modified.writestr(first.filename if hostile=='duplicate' else '../states.npy',original.read(first))
    records[0]['artifact'] = bad.getvalue()
    with pytest.raises(ValueError,match='unsafe native array|invalid or oversized NPZ'):
        assert_physics_restore(backup,tmp_path,records)
    assert not Path(backup[3]['data_dir']).exists()
