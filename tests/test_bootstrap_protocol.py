import hashlib
from pathlib import Path

import pytest

from tools.cd.bootstrap_protocol import overlay, API_ADDITION, STORE_ADDITION


def test_overlay_reproduces_reviewed_protocol_and_preserves_customizations(tmp_path):
    source = tmp_path / 'legacy'
    source.mkdir()
    root = Path(__file__).resolve().parents[1] / 'qsim'
    expected = {}
    for name, addition in [('api.py', API_ADDITION), ('store.py', STORE_ADDITION)]:
        current = (root / name).read_text()
        assert current.count(addition) == 1
        expected[name] = current + '\n# preserved deployment customization\n'
        (source / name).write_text(expected[name].replace(addition, ''))
    digests = [hashlib.sha256((source / n).read_bytes()).hexdigest() for n in ('api.py', 'store.py')]
    output = tmp_path / 'overlay'
    result = overlay(source, output, *digests)
    for name, value in expected.items():
        assert (output / name).read_text() == value
    assert (output / 'release_control.py').read_bytes() == (root / 'release_control.py').read_bytes()
    assert set(result) == {'api.py', 'store.py', 'release_control.py'}
    with pytest.raises(FileExistsError):
        overlay(source, output, *digests)
    (source / 'api.py').write_text('changed source')
    with pytest.raises(ValueError, match='digest mismatch'):
        overlay(source, tmp_path / 'rejected', *digests)
    assert not (tmp_path / 'rejected').exists()
