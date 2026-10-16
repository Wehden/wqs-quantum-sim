# Read-only WQS welcome provider

Implementation contract: `wqs-welcome/1`. This provider lets an authorized central
console select recipients for credential-free welcome/resend. WQS continues to
issue API keys and send original key emails. Nothing in this provider issues,
rotates or revokes a key, sends mail, or changes job authorization.

It supports one operator-reviewed legacy app/org mapping. It does **not** add
organization isolation to the simulation API. `organization_enforcement`,
`additional_organizations` and `first_use_tracking` are false. Do not describe
recipients as users who have never signed in; first API use is unknown.

## Routes and caller contract

All business routes require a separate service bearer over verified TLS:

| GET path | Scope |
| --- | --- |
| `/provider/v1/capabilities` | `welcome:capabilities` |
| `/provider/v1/apps/{app_instance_id}/orgs/{org_id}/contacts` | `welcome:read` |
| `/provider/v1/apps/{app_instance_id}/orgs/{org_id}/subjects/{subject_id}` | `welcome:read` |

Contacts use `limit` (1–100, default 50) and an opaque `cursor`. Iterate until
`next_cursor` is null. On 409 `snapshot_changed`, restart enumeration. Cursors
expire after five minutes and are bound to scope, page size and evidence snapshot.
Unknown query fields and duplicate parameters are rejected. A single-subject read
returns fresh eligibility; a known revoked subject has `recipient_email=null`.
Unknown/out-of-grant scopes or subjects return 404. Bad authentication returns 401,
missing scope 403, invalid queries/cursors 422, unavailable evidence 503.

Central registration fixes the provider endpoint, TLS CA, service credential,
backend namespace and exact app/org IDs server-side. Never choose these from a
browser header or form. Normalize `revision` to `source_revision`, the mapping
revision to `policy_revision`, and the canonical capability digest to
`capability_revision`. IDs and revisions are opaque. No key, hash or encrypted
payload may be exported in normalized records.

Immediately before enqueueing a welcome/resend, re-read the single subject and
require `eligible=true` and the expected revision. Proofs expire after 30 seconds;
revalidate before sending if expired. Suppress mail on provider failure or lost
eligibility. Central owns its mail idempotency, retention and delivery receipts.
A read cannot prevent revocation immediately afterward; the welcome is key-free
and grants no access. Original `SENT` means SMTP acceptance, not inbox delivery.

## Evidence and mapping

The process opens the existing SQLite database with `mode=ro` and `query_only`.
It reads request metadata and compares an approved request's hash internally with
an active identity in a fresh Kubernetes Secret GET. It brackets observations
with another registry read and detects concurrent mapping changes, retrying once.
It never falls back to projected Secret contents, an old contact cache or browser
admin sessions. Missing/unreadable WAL state or ambiguous data fails closed.

Install a private binding JSON only after operator review. Example shape (all
values below are placeholders, not a valid deployment inventory):

```json
{
  "schema_version": 1,
  "revision": "mapping-v1",
  "app_instance_id": "app-example",
  "org_id": "org-legacy-example",
  "namespace": "example-wqs",
  "secret_name": "example-users",
  "members": [{
    "subject_id": "subject-example",
    "membership_id": "membership-example",
    "identity": "example-user",
    "request_id": "00000000000000000000000000000000",
    "recipient_email": "user@example.test",
    "display_name": "Example User",
    "enabled": true,
    "reviewed_by": "operator-example",
    "reviewed_at": "2026-10-16"
  }]
}
```

Membership IDs identify mapping entries, not native WQS membership rows. Mapping
IDs must be one-to-one and stable; never reuse an ID for another person. Match
identity, request and recipient explicitly. No domain-based inference or automatic
mapping of manually issued identities. New key/approval bindings require review.
Change the mapping revision for every approved update. App/org/registry location
changes require process restart and a new reviewed registration, not hot rebinding.

## Private configuration and deployment gates

Run `python -m qsim.welcome_provider` from the exact tested API image. The standalone
process serves TLS on 8443 with forwarded-proxy trust disabled. It does not import
the public API, browser admin, database initialization or mail loop.

Required environment variables point to private files:
`WQS_WELCOME_BINDING_FILE`, `WQS_WELCOME_AUTH_FILE`,
`WQS_WELCOME_INTEGRITY_KEY_FILE`, `WQS_WELCOME_TLS_CERT_FILE`,
`WQS_WELCOME_TLS_KEY_FILE`. `QSIM_DATA` selects the existing data directory.
The integrity key must have at least 32 random bytes and remain separate from
user keys and cookie/encryption secrets. Restart the process after changing it;
old proofs/cursors will be invalid.

The auth file is an array of one or two verifier entries with `credential_id`,
`sha256`, `audience`, `org_id`, `scopes`, `active`. Audience must equal
`wqs-welcome:<configured-app-id>` and org must match exactly. Use a random service
credential distinct from every user key; store only its digest in the provider.
Two active entries permit a separately approved rotation. File reloads apply
verifier changes on subsequent requests. Keep actual values out of Git/logs.
Central verifies the private server CA and DNS name; never disable TLS checks.

[Example manifests](../deploy/welcome-provider.example.yaml) require replacement
of image, namespace, PVC/node placement, registry name, central selectors, TLS and
API-server network values. They deliberately cannot be applied as a working
production configuration. Dedicated RBAC permits only GET of the named registry
Secret. Read-only PVC/WAL behavior must be verified on the actual storage driver.
No broad Secret list/watch or mutation permission, public ingress, SMTP egress or
simulation permissions are needed. Secret file ownership must allow UID/GID 1000
read access without making keys world-readable.

Code approval does not authorize credentials, mapping installation, merge,
deployment, central registration, CD or mail. Ship requires exact-head CI/review,
owner-approved mapping and service-auth inputs, isolated readiness and TLS checks,
then separate activation approval. Disable central registration first to back out;
stop this provider without altering WQS jobs, key delivery or databases.

See the [ON04A packet](../spec/tenancy/on04a/design.md) and its verification gates.
Synthetic golden responses are in `spec/tenancy/on04a/golden.json`; they contain no
real identities or credentials and do not demonstrate deployed availability.
