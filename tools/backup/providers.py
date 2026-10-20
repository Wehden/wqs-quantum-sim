"""Production encryption and conditional S3 providers; no recovery authority."""
from contextlib import contextmanager, suppress
import hashlib
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid

from bech32 import bech32_decode, bech32_encode, convertbits
from cryptography.hazmat.primitives.asymmetric import ec, x25519

from .metadata import canonical, require


class Budget:
    def __init__(self, seconds=540):
        require(0 < seconds <= 540, 'invalid capture budget')
        self.end = time.monotonic() + seconds

    def remaining(self, cap=540):
        value = min(cap, self.end - time.monotonic())
        if value <= 0:
            raise TimeoutError('capture budget exceeded')
        return value

    @contextmanager
    def enforced(self):
        require(signal.getitimer(signal.ITIMER_REAL)[0] == 0, 'caller already owns alarm')
        old = signal.getsignal(signal.SIGALRM)
        def expired(*_):
            raise TimeoutError('capture wall-clock budget exceeded')
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, self.remaining())
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)


def recipient_key(member):
    """Decode canonical released age encodings and reject invalid curve points."""
    value, kind = member['value'], member['type']
    require(type(value) is str and value == value.lower(), 'canonical lowercase recipient required')
    hrp, words = bech32_decode(value)
    require(words is not None, 'recipient checksum')
    raw = convertbits(words, 5, 8, False)
    require(raw is not None, 'recipient padding')
    raw = bytes(raw)
    require(bech32_encode(hrp, convertbits(raw, 8, 5, True)) == value, 'recipient encoding')
    if kind == 'age-x25519':
        require(hrp == 'age' and len(raw) == 32 and int.from_bytes(raw, 'little') < 2**255 - 19, 'X25519 encoding')
        # A fixed, nonsecret scalar only tests for low-order public inputs.
        x25519.X25519PrivateKey.from_private_bytes(bytes(range(32))).exchange(
            x25519.X25519PublicKey.from_public_bytes(raw))
    elif kind == 'age-plugin-yubikey-piv-p256-v0.5.1':
        require(hrp == 'age1yubikey' and len(raw) == 33 and raw[0] in (2, 3), 'PIV recipient encoding')
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    else:
        raise ValueError('unsupported recipient profile')
    require(member['fingerprint'] == hashlib.sha256(value.encode('ascii')).hexdigest(), 'recipient fingerprint')
    return kind, raw


class AgeEncryption:
    def __init__(self, approved_members, executable, executable_sha256, budget, plugin=None):
        self.members = approved_members
        self.executable, self.sha, self.budget, self.plugin = Path(executable), executable_sha256, budget, plugin

    def validate_recipients(self, members):
        require(canonical(members) == canonical(self.members), 'approved recipient set mismatch')
        require(len(members) >= 2 and members == sorted(members, key=lambda m: (m['type'], m['value'])), 'recipient set/order')
        require(len({m['key_id'] for m in members}) == len(members), 'duplicate recipient ID')
        keys = [recipient_key(m) for m in members]
        require(len(set(keys)) == len(keys), 'duplicate decoded public key')
        return True

    def checked_binary(self, path, expected):
        require(path.is_absolute() and path.is_file() and re.fullmatch('[a-f0-9]{64}', expected), 'pinned executable required')
        with path.open('rb') as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        require(actual == expected, 'executable digest mismatch')
        return str(path)

    def encrypt(self, archive, destination, members):
        self.validate_recipients(members)
        binary = self.checked_binary(self.executable, self.sha)
        # No HOME, agent sockets, cloud credentials or private age identity env.
        env = {'PATH': '/nonexistent', 'LANG': 'C', 'LC_ALL': 'C'}
        if any(m['type'].startswith('age-plugin-') for m in members):
            require(self.plugin is not None, 'pinned YubiKey plugin unavailable')
            plugin = Path(self.plugin['path'])
            require(plugin.name == 'age-plugin-yubikey', 'unexpected plugin name')
            self.checked_binary(plugin, self.plugin['sha256'])
            version = subprocess.run([str(plugin), '--version'], capture_output=True, check=True,
                                     timeout=self.budget.remaining(10), env=env).stdout.decode().strip()
            require(version == 'age-plugin-yubikey 0.5.1', 'unsupported plugin version')
            env['PATH'] = str(plugin.parent)
        command = [binary, '--encrypt']
        for member in members:
            command += ['--recipient', member['value']]
        # Exclusive destination avoids overwriting any retained ciphertext.
        with open(destination, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as out, open(archive, 'rb') as source:
            with subprocess.Popen(command, stdin=source, stdout=out, stderr=subprocess.PIPE,
                                  start_new_session=True, env=env) as process:
                try:
                    _, error = process.communicate(timeout=self.budget.remaining())
                    if process.returncode:
                        raise subprocess.CalledProcessError(process.returncode, command, stderr=error)
                except BaseException:
                    # Kill the owned process group, including a blocked plugin.
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
            out.flush()
            os.fsync(out.fileno())


class S3Storage:
    """A single bounded conditional PUT, followed by exact-key/version GET.

    Supply a client configured without retries and with finite socket timeouts.
    Never use upload_file: its multipart path does not imply create-only writes.
    """
    def __init__(self, client, bucket, prefix, budget, max_bytes=512 * 1024 * 1024):
        require(re.fullmatch('[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket), 'invalid bucket')
        require(re.fullmatch('[A-Za-z0-9/_-]*', prefix) and not prefix.startswith('/'), 'invalid prefix')
        require(0 < max_bytes <= 5 * 1024**3, 'single PUT size bound')
        self.client, self.bucket, self.prefix, self.budget, self.max_bytes = client, bucket, prefix, budget, max_bytes
        self.objects = {}

    def put(self, ciphertext):
        self.budget.remaining()
        size = ciphertext.stat().st_size
        require(0 < size <= self.max_bytes, 'ciphertext size exceeds capture policy')
        ref = uuid.uuid4().hex
        key = self.prefix.rstrip('/') + '/' + ref + '.age'
        with ciphertext.open('rb') as source:
            response = self.client.put_object(Bucket=self.bucket, Key=key, Body=source,
                                             ContentLength=size, IfNoneMatch='*', ContentType='application/octet-stream')
        self.budget.remaining()
        self.objects[ref] = (key, response.get('VersionId'), size)
        return ref

    def readback(self, ref, destination):
        self.budget.remaining()
        require(ref in self.objects, 'readback must name this invocation capture')
        key, version, size = self.objects[ref]
        args = {'Bucket': self.bucket, 'Key': key}
        if version:
            args['VersionId'] = version
        response = self.client.get_object(**args)
        body = response['Body']
        try:
            require(response['ContentLength'] == size, 'readback size mismatch')
            total = 0
            with open(destination, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as out:
                while True:
                    self.budget.remaining()
                    block = body.read(min(1024 * 1024, size + 1 - total))
                    if not block:
                        break
                    total += len(block)
                    require(total <= size, 'oversized readback')
                    out.write(block)
                require(total == size, 'truncated readback')
                out.flush()
                os.fsync(out.fileno())
        finally:
            body.close()


def s3_client(endpoint, region, credential_file):
    """Use only explicitly supplied credentials; no ambient SDK credential lookup."""
    import json
    from urllib.parse import urlsplit
    import boto3
    from botocore.config import Config
    url = urlsplit(endpoint)
    require(url.scheme == 'https' and url.hostname and not url.username and not url.password
            and not url.query and not url.fragment, 'TLS object-store endpoint required')
    credentials = json.loads(Path(credential_file).read_text())
    require(set(credentials) == {'access_key_id', 'secret_access_key'}, 'explicit storage credential pair required')
    require(all(isinstance(v, str) and v for v in credentials.values()), 'empty storage credential')
    return boto3.client('s3', endpoint_url=endpoint, region_name=region,
                        aws_access_key_id=credentials['access_key_id'], aws_secret_access_key=credentials['secret_access_key'],
                        config=Config(connect_timeout=10, read_timeout=15, retries={'total_max_attempts': 1},
                                      s3={'addressing_style': 'path'}, proxies={}))


class Providers:
    def __init__(self, boundary, encryption, storage):
        self.boundary, self.encryption, self.storage = boundary, encryption, storage

    def validate_boundary(self, root, manifest):
        return self.boundary.validate(root, manifest)

    def validate_recipients(self, members):
        return self.encryption.validate_recipients(members)

    def encrypt(self, archive, destination, members):
        return self.encryption.encrypt(archive, destination, members)

    def put(self, ciphertext):
        return self.storage.put(ciphertext)

    def readback(self, ref, destination):
        return self.storage.readback(ref, destination)
