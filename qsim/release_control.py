"""Local-only release admission control, sharing the job transaction boundary."""
import argparse
import json
import re
from . import store


def snapshot(db):
    row = db.execute('SELECT * FROM release_control WHERE singleton=1').fetchone()
    pending = db.execute('SELECT COUNT(*) FROM jobs WHERE cleaned=0').fetchone()[0]
    return {'protocol': 1, 'release': row['release'], 'smoke_owner': row['smoke_owner'],
            'pending': pending}


def control(action, release, smoke_owner=''):
    if not re.fullmatch('[0-9a-f]{40}', release):
        raise ValueError('Expected full release SHA')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        state = snapshot(db)
        if action == 'status':
            return state
        if state['release'] not in ('', release):
            raise ValueError('Another release owns admission')
        if action == 'hold':
            # A retry may not reset an existing smoke phase.
            if not state['release']:
                db.execute('UPDATE release_control SET release=?,smoke_owner=? WHERE singleton=1', (release, ''))
        elif action == 'smoke':
            if state['release'] != release or state['pending'] or not smoke_owner:
                raise ValueError('Held, drained release and smoke identity required')
            db.execute('UPDATE release_control SET smoke_owner=? WHERE singleton=1', (smoke_owner,))
        elif action == 'open':
            if state['release'] != release or state['pending']:
                raise ValueError('Held and drained release required')
            db.execute("UPDATE release_control SET release='',smoke_owner='' WHERE singleton=1")
        else:
            raise ValueError('Unknown action')
        return snapshot(db)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['hold', 'status', 'smoke', 'open'])
    parser.add_argument('--release', required=True)
    parser.add_argument('--smoke-owner', default='')
    args = parser.parse_args()
    # Deliberately do not initialize/migrate here: old deployments must fail preflight.
    print(json.dumps(control(args.action, args.release, args.smoke_owner)))


if __name__ == '__main__':
    main()
