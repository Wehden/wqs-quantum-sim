#!/usr/bin/env python3
"""Stream a consistent SQLite snapshot and immutable artifacts to a private local archive."""
import argparse
import os
from pathlib import Path
import subprocess
parser = argparse.ArgumentParser()
parser.add_argument('destination', type=Path)
args = parser.parse_args()
# The backup never contains raw invitation keys. Keep the archive private: it has circuits/results.
code = '''import sqlite3, tempfile, tarfile, pathlib, sys
with tempfile.TemporaryDirectory() as d:
 source=sqlite3.connect('/data/jobs.sqlite3')
 source.execute('BEGIN IMMEDIATE')
 try:
  snapshot=pathlib.Path(d)/'jobs.sqlite3'
  reader=sqlite3.connect('/data/jobs.sqlite3')
  target=sqlite3.connect(snapshot)
  reader.backup(target)
  target.close(); reader.close()
  with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
   archive.add(snapshot,arcname='jobs.sqlite3')
   for row in source.execute("SELECT id FROM jobs WHERE status='SUCCEEDED'"):
    artifact=pathlib.Path('/data')/row[0]/'result.npz'
    archive.add(artifact,arcname=row[0]+'/result.npz')
 finally:
  source.rollback();source.close()
'''
with open(args.destination, 'xb', opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
    proc = subprocess.run(['kubectl','-n','qsim','exec','deployment/qsim-api','--','python','-c',code],stdout=out)
if proc.returncode:
    args.destination.unlink()
    raise SystemExit(proc.returncode)
print(f'Backup saved to {args.destination}')
