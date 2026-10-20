"""Prepare a minimal protocol-1 overlay for a pinned legacy API image.

Does not build, publish, deploy, change admission, or access credentials.
"""
import argparse
import hashlib
from pathlib import Path

STORE_ANCHOR = "        db.execute('BEGIN IMMEDIATE')\n"
STORE_ADDITION = '''        db.execute("CREATE TABLE IF NOT EXISTS release_control (singleton INTEGER PRIMARY KEY CHECK(singleton=1), release TEXT NOT NULL, smoke_owner TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO release_control VALUES (1,'','')")
'''
API_ANCHOR = '        directory = settings.DATA / job_id\n'
API_ADDITION = '''        release = db.execute('SELECT release,smoke_owner FROM release_control WHERE singleton=1').fetchone()
        if release['release'] and (not release['smoke_owner'] or user != release['smoke_owner']):
            raise HTTPException(503, 'Release maintenance: retry new submissions later', headers={'Retry-After': '60'})

'''


def overlay(source, output, api_sha256, store_sha256):
    source, output = Path(source), Path(output)
    changes = {}
    for name, expected, anchor, addition in (
        ('api.py', api_sha256, API_ANCHOR, API_ADDITION),
        ('store.py', store_sha256, STORE_ANCHOR, STORE_ADDITION),
    ):
        raw = (source / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Source digest mismatch: ' + name)
        text = raw.decode()
        if 'release_control' in text or text.count(anchor) != 1:
            raise ValueError('Unsupported legacy source: ' + name)
        patched = text.replace(anchor, addition + anchor if name == 'api.py' else anchor + addition)
        compile(patched, name, 'exec')
        changes[name] = patched.encode()
    # Keep the control implementation identical to the reviewed repository source.
    changes['release_control.py'] = (Path(__file__).resolve().parents[2] / 'qsim/release_control.py').read_bytes()
    output.mkdir(mode=0o700)  # Refuse reuse of a previous overlay.
    for name, raw in changes.items():
        (output / name).write_bytes(raw)
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in changes.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Extracted legacy qsim directory')
    parser.add_argument('--output', type=Path, required=True, help='New overlay directory')
    parser.add_argument('--api-sha256', required=True)
    parser.add_argument('--store-sha256', required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(overlay(args.source, args.output, args.api_sha256, args.store_sha256), sort_keys=True))


if __name__ == '__main__':
    main()
