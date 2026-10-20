"""Explicit decrypt, validate and direct native load. Never invoked by creation."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tarfile
import time

from .metadata import canonical, parse_metadata, require
from .native_load import NativeLoad, validate_wqs
from .v2 import validate_manifest

MAX_BYTES = 512 * 1024 * 1024


def decrypt(backup, identity, executable, executable_sha256, timeout=540, max_bytes=MAX_BYTES):
    executable = Path(executable)
    require(executable.is_absolute(), 'absolute age executable required')
    if not executable.is_file() or not Path(backup).is_file() or not Path(identity).is_file():
        raise FileNotFoundError('required restore input unavailable')
    require(hashlib.sha256(executable.read_bytes()).hexdigest() == executable_sha256, 'age executable pin mismatch')
    deadline = time.monotonic() + timeout
    process = subprocess.Popen([str(executable), '--decrypt', '--identity', str(identity), str(backup)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        content = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('decryption timeout')
                block = os.read(process.stdout.fileno(), min(65536, max_bytes + 1 - len(content)))
                if not block:
                    break
                content.extend(block)
                require(len(content) <= max_bytes, 'decrypted archive exceeds size limit')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('decryption timeout')
        require(process.wait(timeout=remaining) == 0, 'decryption failed')
        return bytes(content)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        process.stdout.close()


def unpack(raw, expected_id=None, max_bytes=MAX_BYTES):
    require(len(raw) <= max_bytes, 'archive exceeds size limit')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        first = archive.next()
        require(first is not None and first.name == 'manifest.json' and first.isfile()
                and not first.issparse() and 0 < first.size <= min(max_bytes, 4 * 1024 * 1024), 'manifest must be first regular member')
        manifest_bytes = archive.extractfile(first).read()
        manifest = parse_metadata(manifest_bytes)
        require(manifest_bytes == canonical(manifest), 'noncanonical manifest bytes')
        validate_manifest(manifest, expected_id)
        expected = {entry['path']: entry for entry in manifest['inventory']}
        require('manifest.json' not in expected, 'reserved manifest path')
        files, total = {}, first.size
        while (member := archive.next()) is not None:
            require(member.name in expected and member.name not in files and member.isfile()
                    and not member.issparse(), 'unexpected or nonregular archive member')
            entry = expected[member.name]
            total += member.size
            require(member.size == entry['size'] and total <= max_bytes, 'archive member size mismatch')
            value = archive.extractfile(member).read()
            require(len(value) == member.size and hashlib.sha256(value).hexdigest() == entry['sha256'],
                    'archive member hash mismatch')
            files[member.name] = value
        require(set(files) == set(expected), 'incomplete archive')
    validate_wqs(manifest, files)
    return manifest, files


def restore(backup, identity, executable, executable_sha256, expected_id, destinations):
    raw = decrypt(backup, identity, executable, executable_sha256)
    manifest, files = unpack(raw, expected_id)
    NativeLoad(destinations).load(manifest, files)
    return {'evidence_type': 'final_result', 'verified': True, 'backup_id': manifest['context']['set_id']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True)
    parser.add_argument('--identity', required=True, help='Existing YubiKey or fallback age identity file')
    parser.add_argument('--age', required=True)
    parser.add_argument('--age-sha256', required=True)
    parser.add_argument('--set-id', help='Optional operator mismatch check; defaults to manifest ID')
    parser.add_argument('--destinations', required=True, help='Explicit offline native destination JSON')
    args = parser.parse_args()
    try:
        result = restore(args.backup, args.identity, args.age, args.age_sha256, args.set_id,
                         parse_metadata(Path(args.destinations).read_bytes()))
    except (TimeoutError, subprocess.TimeoutExpired):
        result = failure('timeout')
    except FileNotFoundError:
        result = failure('unavailable')
    except Exception:
        result = failure('ineligible')
    print(canonical(result).decode())
    return 0 if result['verified'] else 1


def failure(code):
    return {'evidence_type': 'final_result', 'verified': False, 'backup_id': None, 'failure': code}


if __name__ == '__main__':
    raise SystemExit(main())
