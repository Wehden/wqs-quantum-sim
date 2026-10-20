import pytest
from fastapi.testclient import TestClient
from qsim.api import app
from qsim import public_docs


@pytest.mark.parametrize('path', ['/', '/agents.md', '/static/index.html', '/static/agents.md'])
def test_configured_origin_in_all_guides(monkeypatch, path):
    monkeypatch.setenv('QSIM_PUBLIC_URL', 'https://quantum.example.test:8443/')
    response = TestClient(app).get(path, headers={
        'Host': 'attacker.example.test', 'X-Forwarded-Host': 'attacker.example.test'})
    assert response.status_code == 200
    assert 'https://quantum.example.test:8443' in response.text
    assert public_docs.PLACEHOLDER not in response.text
    assert 'attacker.example.test' not in response.text
    if path.endswith('.md'):
        assert response.headers['content-disposition'].startswith('attachment;')


@pytest.mark.parametrize('value', [
    'javascript:alert(1)', 'https://user:secret@example.test',
    'https://example.test/path', 'https://example.test?x=1',
    'https://example.test#anchor', 'https://example.test;id',
    'https://example.test/$(id)', 'https://example.test:99999',
    'https://example.test:0', 'https://example.test\n',
    'https://example.test\"',
])
def test_unsafe_configuration_rejected(monkeypatch, value):
    monkeypatch.setenv('QSIM_PUBLIC_URL', value)
    with pytest.raises(ValueError):
        public_docs.public_url()


def test_default_remains_generic(monkeypatch):
    monkeypatch.delenv('QSIM_PUBLIC_URL', raising=False)
    assert public_docs.public_url() == public_docs.PLACEHOLDER


@pytest.mark.parametrize('path', ['/static/index.html', '/static/agents.md'])
def test_static_head_retains_get_metadata(monkeypatch, path):
    monkeypatch.setenv('QSIM_PUBLIC_URL', 'https://quantum.example.test')
    client = TestClient(app)
    get = client.get(path)
    head = client.head(path)
    assert head.status_code == 200
    assert head.content == b''
    for header in ('content-type', 'content-length', 'content-disposition'):
        assert head.headers.get(header) == get.headers.get(header)
