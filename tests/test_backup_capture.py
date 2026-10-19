"""Synthetic adapter tests. These do not encrypt or establish recovery trust."""
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from tools.backup.capture import capture, unavailable_result
from tools.backup.metadata import canonical, digest, parse_metadata, validate_record

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/backup/conformance.json').read_text())


@pytest.mark.parametrize('vector', FIXTURE['canonical_vectors'])
def test_canonical_vectors(vector):
    actual = canonical(parse_metadata(vector['input_json']))
    assert actual.decode() == vector['canonical_json']
    assert digest(actual) == {'sha256': vector['sha256'], 'size': vector['size']}


@pytest.mark.parametrize('vector', FIXTURE['byte_vectors'])
def test_byte_vectors(vector):
    raw = bytes.fromhex(vector['input_hex'])
    if vector['valid']:
        assert canonical(parse_metadata(raw)).decode() == vector['canonical_json']
    else:
        with pytest.raises((ValueError, UnicodeError)):
            parse_metadata(raw)


class SyntheticProviders:
    """Test doubles only: deliberately fake encryption and boundary authority."""
    def __init__(self):
        self.objects = {}
        self.archive = None
        self.boundaries = 0

    def validate_recipients(self, members):
        return True

    def validate_boundary(self, root, manifest):
        self.boundaries += 1
        return True

    def encrypt(self, archive, ciphertext, members):
        self.archive = archive.read_bytes()
        ciphertext.write_bytes(b'SYNTHETIC opaque encrypted bytes')

    def put(self, ciphertext):
        self.objects['SYNTHETIC-object'] = ciphertext.read_bytes()
        return 'SYNTHETIC-object'

    def readback(self, reference, destination):
        destination.write_bytes(self.objects[reference])


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    work = tmp_path / 'work'
    work.mkdir()
    manifest = copy.deepcopy(FIXTURE['base']['graph']['records']['manifest'])
    for item in manifest['inventory']:
        value = ('SYNTHETIC bytes for ' + item['role']).encode()
        (source / item['path']).write_bytes(value)
        item.update(digest(value))
    return source, work, manifest, SyntheticProviders()


def run(setup):
    source, work, manifest, providers = setup
    return capture(source, manifest, copy.deepcopy(manifest['context']), providers, work,
                   now=datetime(2026, 10, 17, 12, 2, tzinfo=timezone.utc))


def test_capture_complete_archive_readback_never_promotes(setup):
    source, work, manifest, providers = setup
    receipt = run(setup)
    validate_record(receipt)
    assert receipt['evidence_type'] == 'capture_readback'
    assert 'verified' not in receipt
    assert providers.boundaries == 2
    with tarfile.open(fileobj=io.BytesIO(providers.archive)) as archive:
        assert set(archive.getnames()) == {'manifest.json', *(item['path'] for item in manifest['inventory'])}
        assert archive.extractfile('manifest.json').read() == canonical(manifest)
        for entry in manifest['inventory']:
            assert digest(archive.extractfile(entry['path']).read()) == {k: entry[k] for k in ('sha256', 'size')}
    assert not list(work.iterdir())
    assert unavailable_result()['verified'] is False


@pytest.mark.parametrize('failure', ['bytes', 'missing', 'symlink', 'hardlink', 'recipient', 'boundary', 'readback', 'dependency', 'hold', 'context'])
def test_capture_rejects_invalid_inputs_without_acceptance(setup, failure):
    source, work, manifest, providers = setup
    path = source / manifest['inventory'][0]['path']
    if failure == 'bytes':
        path.write_bytes(b'changed')
    elif failure == 'missing':
        path.unlink()
    elif failure == 'symlink':
        path.unlink()
        path.symlink_to('/etc/passwd')
    elif failure == 'hardlink':
        (source / 'alias').hardlink_to(path)
    elif failure == 'recipient':
        providers.validate_recipients = lambda _: False
    elif failure == 'boundary':
        providers.validate_boundary = lambda *_: False
    elif failure == 'readback':
        providers.readback = lambda _, out: out.write_bytes(b'corrupt')
    elif failure == 'dependency':
        manifest['dependencies']['results'] = []
    elif failure == 'hold':
        manifest['dependencies']['admission_held'] = False
    elif failure == 'context':
        with pytest.raises(ValueError):
            capture(source, manifest, {}, providers, work)
        return
    with pytest.raises((ValueError, OSError)):
        run(setup)
    assert not list(work.iterdir())


def test_parent_symlink_cannot_escape(setup):
    source, work, manifest, providers = setup
    old = manifest['inventory'][0]['path']
    manifest['inventory'][0]['path'] = 'link/' + old
    manifest['dependencies']['database'] = 'link/' + old
    (source / 'link').symlink_to(source, target_is_directory=True)
    with pytest.raises(OSError):
        run(setup)


def test_cli_fails_closed():
    result = subprocess.run([sys.executable, '-m', 'tools.backup.capture'], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout) == unavailable_result()


@pytest.mark.parametrize('vector', FIXTURE['invalid_metadata'])
def test_invalid_metadata_vectors(vector):
    with pytest.raises((ValueError, UnicodeError)):
        parse_metadata(vector['input_json'])


def test_recheck_boundary_after_readback(setup):
    providers = setup[3]
    results = iter([True, False])
    providers.validate_boundary = lambda *_: next(results)
    with pytest.raises(ValueError, match='boundary changed'):
        run(setup)
    assert not list(setup[1].iterdir())


def test_provider_failure_cleans_plaintext(setup):
    def fail(*_):
        raise TimeoutError('synthetic provider timeout')
    setup[3].encrypt = fail
    with pytest.raises(TimeoutError):
        run(setup)
    assert not list(setup[1].iterdir())
