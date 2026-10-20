import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess

from bech32 import bech32_encode, convertbits
from botocore.stub import Stubber
import boto3
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from tools.backup.providers import AgeEncryption, Budget, S3Storage, recipient_key


def member(raw, kind='age-x25519', key_id='test'):
    hrp = 'age' if kind == 'age-x25519' else 'age1yubikey'
    value = bech32_encode(hrp, convertbits(raw, 8, 5, True))
    return {'key_id': key_id, 'type': kind, 'value': value, 'fingerprint': hashlib.sha256(value.encode()).hexdigest()}


def members():
    values = [member(x25519.X25519PrivateKey.generate().public_key().public_bytes_raw(), key_id=str(i)) for i in range(2)]
    return sorted(values, key=lambda x: (x['type'], x['value']))


def test_recipient_decoding_and_rejections():
    value = members()[0]
    assert len(recipient_key(value)[1]) == 32
    hardware = member(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint),
                      'age-plugin-yubikey-piv-p256-v0.5.1')
    assert len(recipient_key(hardware)[1]) == 33
    for invalid in ({**value, 'value': value['value'].upper()}, {**value, 'value': value['value'][:-1] + '!'},
                    member(bytes(32)), member((2**255 - 19).to_bytes(32, 'little')),
                    member(bytes(33), 'age-plugin-yubikey-piv-p256-v0.5.1'), {**value, 'fingerprint': '0'*64}):
        with pytest.raises(ValueError):
            recipient_key(invalid)


def test_exact_policy_and_decoded_duplicates(tmp_path):
    approved = members()
    engine = AgeEncryption(approved, '/unused', '0'*64, Budget())
    assert engine.validate_recipients(approved)
    with pytest.raises(ValueError):
        engine.validate_recipients(approved[::-1])
    duplicate = [approved[0], {**approved[0], 'key_id': 'alias'}]
    with pytest.raises(ValueError):
        AgeEncryption(duplicate, '/unused', '0'*64, Budget()).validate_recipients(duplicate)


def test_real_age_roundtrip_each_recipient(tmp_path):
    # age is a required CI tool: do not silently skip the encryption exercise.
    age, keygen = shutil.which('age'), shutil.which('age-keygen')
    assert age and keygen, 'Install pinned age tools before running CI'
    approved, keys = [], []
    for index in range(2):
        key = tmp_path / f'key-{index}'
        subprocess.run([keygen, '-o', str(key)], check=True, capture_output=True)
        value = subprocess.run([keygen, '-y', str(key)], check=True, capture_output=True, text=True).stdout.strip()
        approved.append({'key_id': str(index), 'type': 'age-x25519', 'value': value,
                         'fingerprint': hashlib.sha256(value.encode()).hexdigest()})
        keys.append(key)
    approved.sort(key=lambda x: (x['type'], x['value']))
    source, encrypted = tmp_path / 'payload', tmp_path / 'encrypted'
    source.write_bytes(b'isolated synthetic complete payload\0' * 2000)
    engine = AgeEncryption(approved, age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), Budget())
    engine.encrypt(source, encrypted, approved)
    for key in keys:
        actual = subprocess.run([age, '--decrypt', '-i', str(key), str(encrypted)], capture_output=True, check=True).stdout
        assert actual == source.read_bytes()
    with pytest.raises(FileExistsError):
        engine.encrypt(source, encrypted, approved)
    engine.sha = '0'*64
    with pytest.raises(ValueError):
        engine.encrypt(source, tmp_path / 'other', approved)


class FakeStore:
    def put_object(self, **kwargs):
        assert kwargs['IfNoneMatch'] == '*'
        self.key, self.value = kwargs['Key'], kwargs['Body'].read()
        assert kwargs['ContentLength'] == len(self.value)
        return {'VersionId': 'version-one'}

    def get_object(self, **kwargs):
        assert kwargs['Key'] == self.key and kwargs['VersionId'] == 'version-one'
        return {'ContentLength': len(self.value), 'Body': io.BytesIO(self.value)}


def test_storage_conditional_write_exact_version_and_bounds(tmp_path):
    client = FakeStore()
    store = S3Storage(client, 'test-bucket', 'wqs', Budget(), 100)
    source = tmp_path / 'encrypted'
    source.write_bytes(b'opaque fixture')
    ref = store.put(source)
    assert len(ref) == 32 and '/' not in ref
    destination = tmp_path / 'readback'
    store.readback(ref, destination)
    assert destination.read_bytes() == source.read_bytes()
    with pytest.raises(ValueError):
        store.readback('different', tmp_path / 'bad')
    source.write_bytes(b'x'*101)
    with pytest.raises(ValueError):
        store.put(source)


def test_sdk_supports_conditional_put_and_collision_fails(tmp_path):
    client = boto3.client('s3', region_name='us-east-1', aws_access_key_id='synthetic', aws_secret_access_key='synthetic')
    with Stubber(client) as stub:
        stub.add_client_error('put_object', service_error_code='PreconditionFailed', http_status_code=412)
        source = tmp_path / 'ciphertext'
        source.write_bytes(b'opaque')
        store = S3Storage(client, 'test-bucket', 'wqs', Budget())
        with pytest.raises(client.exceptions.ClientError):
            store.put(source)
        assert not store.objects


def test_budget_expires(monkeypatch):
    budget = Budget()
    budget.end = 0
    with pytest.raises(TimeoutError):
        budget.remaining()


def test_enforced_budget_interrupts_blocking_operation():
    import time
    budget = Budget(0.03)
    with pytest.raises(TimeoutError):
        with budget.enforced():
            time.sleep(1)


def test_plugin_profile_requires_pinned_tool(tmp_path):
    native = members()[0]
    hardware = member(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint),
                      'age-plugin-yubikey-piv-p256-v0.5.1', 'hardware')
    approved = sorted([native, hardware], key=lambda x: (x['type'], x['value']))
    age = shutil.which('age')
    engine = AgeEncryption(approved, age, hashlib.sha256(Path(age).read_bytes()).hexdigest(), Budget())
    source = tmp_path / 'plain'
    source.write_bytes(b'synthetic')
    with pytest.raises(ValueError, match='plugin unavailable'):
        engine.encrypt(source, tmp_path / 'ciphertext', approved)
