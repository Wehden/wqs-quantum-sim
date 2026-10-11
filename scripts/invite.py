#!/usr/bin/env python3
"""Create or rotate an invitation; output the raw key only to a private file."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('identity', help='Stable local user ID')
parser.add_argument('--users', type=Path, default=Path('var/users.json'))
parser.add_argument('--output', type=Path, required=True, help='New private file to receive the invitation key')
args = parser.parse_args()
if not args.identity.strip() or len(args.identity) > 128:
    parser.error('Use a nonempty identity of at most 128 characters')
args.users.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
users = json.loads(args.users.read_text()) if args.users.exists() else []
token = 'qsim_' + secrets.token_urlsafe(32)
with open(args.output, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
    f.write(token + '\n')
users = [u for u in users if u['id'] != args.identity]
users.append({'id': args.identity, 'sha256': hashlib.sha256(token.encode()).hexdigest(), 'active': True})
fd, name = tempfile.mkstemp(dir=args.users.parent)
with os.fdopen(fd, 'w') as f:
    json.dump(users, f, indent=2)
Path(name).replace(args.users)
print(f'Invitation created for {args.identity}. Key saved to {args.output}; hashed registry saved to {args.users}.')
