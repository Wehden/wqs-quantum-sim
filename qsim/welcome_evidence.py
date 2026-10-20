"""Read-only welcome evidence. Never import application startup or mail code."""
import base64
from contextlib import closing
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool

ID = r'^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$'
HASH = r'^[a-f0-9]{64}$'


class EvidenceError(Exception):
    pass


class SnapshotChanged(Exception):
    pass


class InvalidCursor(Exception):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Member(StrictModel):
    subject_id: str = Field(pattern=ID)
    membership_id: str = Field(pattern=ID)
    identity: str = Field(pattern=ID)
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    recipient_email: str = Field(max_length=254, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    display_name: str = Field(min_length=1, max_length=100)
    enabled: StrictBool
    reviewed_by: str = Field(min_length=1, max_length=128)
    reviewed_at: str = Field(min_length=1, max_length=64)


class Binding(StrictModel):
    schema_version: int = Field(ge=1, le=1)
    revision: str = Field(pattern=ID)
    app_instance_id: str = Field(pattern=ID)
    org_id: str = Field(pattern=ID)
    namespace: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,62}$')
    secret_name: str = Field(pattern=r'^[a-z0-9][a-z0-9.-]{0,252}$')
    members: list[Member] = Field(max_length=10000)


class Grant(StrictModel):
    credential_id: str = Field(pattern=ID)
    sha256: str = Field(pattern=HASH)
    audience: str
    org_id: str = Field(pattern=ID)
    scopes: list[str]
    active: StrictBool


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise EvidenceError()
    return json.loads(raw)


@dataclass(frozen=True)
class ProviderConfig:
    binding_file: Path
    auth_file: Path
    integrity_key_file: Path
    database: Path
    tls_cert_file: Path
    tls_key_file: Path
    kube_url: str = 'https://kubernetes.default.svc'
    kube_ca: Path = Path('/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
    kube_token: Path = Path('/var/run/secrets/kubernetes.io/serviceaccount/token')

    @classmethod
    def from_env(cls):
        return cls(*(Path(os.environ['WQS_WELCOME_' + key]) for key in
                     ('BINDING_FILE', 'AUTH_FILE', 'INTEGRITY_KEY_FILE')),
                   Path(os.environ.get('QSIM_DATA', '/data')) / 'jobs.sqlite3',
                   *(Path(os.environ['WQS_WELCOME_' + key]) for key in
                     ('TLS_CERT_FILE', 'TLS_KEY_FILE')))

    def binding(self):
        binding = Binding.model_validate(read_json(self.binding_file))
        for key in ('subject_id', 'membership_id', 'identity', 'request_id', 'recipient_email'):
            values = [getattr(m, key) for m in binding.members]
            if len(values) != len(set(values)):
                raise EvidenceError()
        return binding

    def grants(self):
        raw = read_json(self.auth_file)
        if not isinstance(raw, list) or not 1 <= len(raw) <= 2:
            raise EvidenceError()
        grants = [Grant.model_validate(g) for g in raw]
        for key in ('credential_id', 'sha256'):
            if len({getattr(g, key) for g in grants}) != len(grants):
                raise EvidenceError()
        if any(set(g.scopes) - {'welcome:read', 'welcome:capabilities'} for g in grants):
            raise EvidenceError()
        return grants


class EvidenceReader:
    def __init__(self, config, registry_get=None, clock=time.time, monotonic=time.monotonic):
        self.config = config
        self.registry_get = registry_get or self._registry_get
        self.clock = clock
        self.monotonic = monotonic
        self.binding_identity = self.config.binding()
        self.key = self.config.integrity_key_file.read_bytes()
        if len(self.key) < 32:
            raise EvidenceError()

    def binding(self):
        value = self.config.binding()
        original = self.binding_identity
        if any(getattr(value, k) != getattr(original, k) for k in
               ('app_instance_id', 'org_id', 'namespace', 'secret_name')):
            raise EvidenceError()
        return value

    def sign(self, purpose, value):
        return hmac.new(self.key, purpose.encode() + b'\0' + canonical(value), hashlib.sha256).hexdigest()

    def capabilities(self):
        binding = self.binding()
        body = {'contract_version': 'wqs-welcome/1', 'app_instance_id': binding.app_instance_id,
                'org_id': binding.org_id, 'auth_mode': 'api_tokens', 'read_only': True,
                'welcome_eligibility': True, 'organization_enforcement': False,
                'additional_organizations': False, 'first_use_tracking': False,
                'operations': ['capabilities', 'contacts', 'subject'], 'policy_revision': binding.revision}
        body['capability_revision'] = hashlib.sha256(canonical(body)).hexdigest()
        return body

    def _registry_get(self, binding, timeout):
        token = self.config.kube_token.read_text().strip()
        url = (self.config.kube_url + '/api/v1/namespaces/' + binding.namespace
               + '/secrets/' + binding.secret_name)
        deadline = self.monotonic() + timeout
        with httpx.Client(verify=str(self.config.kube_ca), timeout=timeout, trust_env=False) as client:
            with client.stream('GET', url, headers={'Authorization': 'Bearer ' + token}) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024 or self.monotonic() >= deadline:
                        raise EvidenceError()
        payload = json.loads(data)
        users = json.loads(base64.b64decode(payload['data']['users.json'], validate=True))
        return str(payload['metadata']['resourceVersion']), users

    @staticmethod
    def registry(value):
        revision, users = value
        if not isinstance(revision, str) or not revision or not isinstance(users, list):
            raise EvidenceError()
        lookup, hashes = {}, set()
        for user in users:
            if (not isinstance(user, dict) or not isinstance(user.get('id'), str)
                    or type(user.get('active')) is not bool or not isinstance(user.get('sha256'), str)):
                raise EvidenceError()
            digest = user['sha256']
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise EvidenceError()
            if user['id'] in lookup or digest in hashes:
                raise EvidenceError()
            lookup[user['id']] = user
            hashes.add(digest)
        return revision, lookup

    def _contact(self, binding, member, row, users, registry_revision, binding_digest):
        user = users.get(member.identity)
        reason = 'eligible'
        if not member.enabled:
            reason = 'mapping_disabled'
        elif not user or not user['active']:
            reason = 'inactive_identity'
        elif not row or row['status'] != 'APPROVED':
            reason = 'unapproved_request'
        elif (row['identity'] != member.identity or row['email'] != member.recipient_email
              or not isinstance(row['token_hash'], str)
              or not hmac.compare_digest(row['token_hash'], user['sha256'])):
            reason = 'binding_mismatch'
        elif row['delivery_state'] != 'SENT':
            reason = 'delivery_not_sent'
        safe = {k: row[k] for k in ('status', 'reviewed', 'delivery_state', 'delivered')} if row else None
        proof = {'binding_digest': binding_digest, 'registry': registry_revision,
                 'subject': member.subject_id, 'request': safe, 'reason': reason}
        return {'subject_id': member.subject_id, 'membership_id': member.membership_id,
                'app_instance_id': binding.app_instance_id, 'org_id': binding.org_id,
                'display_name': member.display_name, 'recipient_email': member.recipient_email if reason == 'eligible' else None,
                'eligible': reason == 'eligible', 'reason': reason, 'revision': 'r1.' + self.sign('revision', proof),
                'first_use': 'unknown', 'binding_kind': 'operator_reviewed_legacy_mapping',
                'key_delivery': {'state': row['delivery_state'] if row else 'UNKNOWN',
                                 'smtp_accepted_at': row['delivered'] if row and row['delivery_state'] == 'SENT' else None}}

    def observe(self):
        deadline = self.monotonic() + 15
        def remaining():
            left = deadline - self.monotonic()
            if left <= 0:
                raise EvidenceError()
            return min(5, left)
        for _ in range(2):
            observed = self.clock()
            binding = self.binding()
            uri = 'file:' + quote(str(self.config.database.resolve()), safe='/') + '?mode=ro'
            with closing(sqlite3.connect(uri, uri=True, timeout=min(2, remaining()))) as db:
                try:
                    db.set_progress_handler(lambda: int(self.monotonic() >= deadline), 1000)
                    db.row_factory = sqlite3.Row
                    db.execute('PRAGMA query_only=ON')
                    db.execute('BEGIN')
                    # Establish the SQLite snapshot before the first registry observation.
                    rows = {r['id']: r for r in db.execute('SELECT id,identity,email,status,reviewed,token_hash,delivery_state,delivered FROM key_requests')}
                    revision, users = self.registry(self.registry_get(binding, remaining()))
                    binding_digest = hashlib.sha256(canonical(binding.model_dump())).hexdigest()
                    contacts = [self._contact(binding, m, rows.get(m.request_id), users, revision, binding_digest)
                                for m in sorted(binding.members, key=lambda m: m.subject_id)]
                    final_revision, _ = self.registry(self.registry_get(binding, remaining()))
                    remaining()
                    if final_revision != revision or binding != self.binding():
                        continue
                    snapshot = self.sign('snapshot', {'revisions': [c['revision'] for c in contacts],
                                                     'binding': binding.model_dump(), 'registry': revision})
                    for contact in contacts:
                        contact.update(observed_at=observed, expires_at=observed + 30)
                    return binding, contacts, snapshot
                finally:
                    db.rollback()
        raise EvidenceError()

    def page(self, binding, contacts, snapshot, limit, cursor):
        after, expiry = '', self.clock() + 300
        scope = [binding.app_instance_id, binding.org_id, limit]
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError()
                encoded, signature = cursor.split('.')
                payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
                if not hmac.compare_digest(signature, self.sign('cursor', payload)):
                    raise ValueError()
                if payload['scope'] != scope or payload['expires'] <= self.clock():
                    raise ValueError()
                after, expiry = payload['after'], payload['expires']
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise InvalidCursor() from None
            if payload['snapshot'] != snapshot:
                raise SnapshotChanged()
        eligible = [c for c in contacts if c['eligible'] and c['subject_id'] > after]
        items, next_cursor = eligible[:limit], None
        if len(eligible) > limit:
            payload = {'scope': scope, 'snapshot': snapshot, 'after': items[-1]['subject_id'], 'expires': expiry}
            next_cursor = (base64.urlsafe_b64encode(canonical(payload)).decode().rstrip('=')
                           + '.' + self.sign('cursor', payload))
        return {'contacts': items, 'next_cursor': next_cursor, 'snapshot_revision': snapshot}
