"""Pinned portable v2 metadata vectors; real decrypt/load is tested separately."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from tools.backup.metadata import canonical, digest, parse_metadata
from tools.backup.v2 import validate_manifest, CONTRACT_COMMIT

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'tests/fixtures/backup/conformance-v2.json').read_text())


@pytest.mark.parametrize('vector', FIXTURE['canonical_vectors'])
def test_canonical_vectors(vector):
    value = canonical(parse_metadata(vector['input_json']))
    assert value.decode() == vector['canonical_json']
    assert digest(value) == {key: vector[key] for key in ('sha256', 'size')}


@pytest.mark.parametrize('vector', FIXTURE['invalid_metadata'])
def test_invalid_metadata(vector):
    with pytest.raises((ValueError, UnicodeError)): parse_metadata(vector['input_json'])


@pytest.mark.parametrize('vector', FIXTURE['byte_vectors'])
def test_byte_vectors(vector):
    raw = bytes.fromhex(vector['input_hex'])
    if vector['valid']:
        assert canonical(parse_metadata(raw)).decode() == vector['canonical_json']
    else:
        with pytest.raises((ValueError, UnicodeError)): parse_metadata(raw)


@pytest.mark.parametrize('empty', FIXTURE['empty_variants'])
def test_empty_first_release_collections(empty):
    manifest = copy.deepcopy(FIXTURE['manifest'])
    for role in empty:
        manifest['dependencies'][role] = []
        manifest['inventory'] = [entry for entry in manifest['inventory'] if entry['role'] != role]
    validate_manifest(manifest, None)


def test_exact_upstream_provenance():
    provenance = json.loads((ROOT / 'tools/backup/contract/v2/provenance.json').read_text())
    assert provenance['commit'] == CONTRACT_COMMIT
    for entry in provenance['files']:
        assert hashlib.sha256((ROOT / entry['local']).read_bytes()).hexdigest() == entry['sha256']
