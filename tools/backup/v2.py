"""Active v2 structural semantics; historical v1 remains in metadata.py."""
import json
from pathlib import Path
from .metadata import canonical, digest, require, timestamp, verify_manifest

CONTRACT_COMMIT = "fdec5b863579fd775a776eb93f7cc1ed910cf015"


def validate_record(record):
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads((Path(__file__).parent / 'contract/v2/schema.json').read_text())
    canonical(record)
    require(not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record)),
            'v2 schema failure')
    if record['evidence_type'] not in ('creation_result', 'final_result'):
        timestamp(record['created_at'])
        timestamp(record['context']['boundary']['source_at'])


def validate_manifest(record, expected_id):
    require(record.get('schema_id') == 'backup-recovery-set-v2' and record.get('contract_version') == 2
            and record.get('evidence_type') == 'manifest', 'v2 manifest required')
    validate_record(record)
    require((expected_id is None or record['context']['set_id'] == expected_id) and record['context']['set_type'] == 'wqs',
            'unexpected backup identity or type')
    members = record['context']['recipients']
    require(members == sorted(members, key=lambda item: (item['type'], item['value'])), 'recipient order')
    for field in ('key_id', 'value', 'fingerprint'):
        require(len({member[field] for member in members}) == len(members), 'duplicate recipient')
    for member in members:
        require(member['fingerprint'] == digest(member['value'].encode('ascii'))['sha256'], 'recipient fingerprint')
    require(record['context']['recipients_digest'] == digest(canonical(record['context']['recipients'])),
            'recipient digest mismatch')
    verify_manifest(record)  # Internal provenance consistency, no query of old PVC or workload.
