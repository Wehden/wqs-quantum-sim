# WQS Authconsole management contract 

Version `wqs-admin/1`. Separate private TLS service (`python -m qsim.management_provider`), no public API/LAN-session widening. Fixed configured bootstrap app/org; no new tenancy. TLS service Bearer grants use existing private hashed-grant mechanism, audience `wqs-admin:<app_instance_id>`, exact org_id, active=true, scopes `admin:read`, `admin:write`. Welcome grants never authorize management. Configuration WQS_ADMIN_BINDING_FILE, AUTH_FILE, INTEGRITY_KEY_FILE, TLS_CERT_FILE, TLS_KEY_FILE; QSIM_DATA and existing QSIM_TOKEN_FILE/QSIM_ADMIN_SECRET_DIR remain owner-side. Commands require actor attribution supplied by the authenticated trusted console service.

All responses no-store. No key/hash/cipher values or per-key invented IDs. IDs: `[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}` for identity; command_id `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}` (UUID strings work). request IDs 32 lowercase hex. Actor string1..128 printable characters. Invitation name1..100 and purpose10..2000 characters are trimmed; email is canonical lowercase, local part at most64 characters. Revision strings opaque HMAC, compare unchanged.

GET `/internal/admin/v1/capabilities` (admin:read): operations are grant-aware. With only admin:read, operations is ["identities","requests","command"]. With both grants:
```
{"contract_version":"wqs-admin/1","app_instance_id":"...","org_id":"...","auth_mode":"api_tokens","operations":["identities","requests","invitation","approve","reject","notify","retry-delivery","command"],"create_only":true,"organization_enforcement":false,"additional_organizations":false,"first_use_tracking":false}
```
GET `/internal/admin/v1/identities?limit=100&cursor=...` (admin:read):
```
{"app_instance_id":"...","org_id":"...","snapshot_revision":"opaque","next_cursor":null,"identities":[{"identity":"...","active":true,"requests":[<request>]}]}
```
Includes ALL registry identities, inactive and those with requests=[]; one registry hash per identity, no recoverable credential. Requests within an identity are ordered newest first.

GET `/internal/admin/v1/requests?limit=100&cursor=...` lists all pending/approving/approved/rejected requests independently of registry:
```
{"app_instance_id":"...","org_id":"...","snapshot_revision":"opaque","next_cursor":null,"requests":[<request>]}
```
GET `/internal/admin/v1/requests/{id}` returns `<request>`.
Request exact fields:
```
{"id":"32hex","name":"...","email":"...","purpose":"...","created":123.0,"status":"PENDING|APPROVING|APPROVED|REJECTED","identity":null,"reviewed":null,"notification_state":"...","notification_attempts":0,"notified":null,"delivery_state":"NONE|PENDING|RETRY|UNCERTAIN|FAILED|SENT|SENDING","delivery_attempts":0,"delivered":null,"revision":"opaque","actions":["approve","reject"]}
```
Actions list contains only current eligible owner operations (approve, reject, notify, retry-delivery). Error transport strings are excluded. No key recoverability claim. List limit1..100 default50; opaque cursors bind complete snapshot; changed snapshot409, malformedcursor422. Client discards partial enumeration and may restart once.

POST `/internal/admin/v1/invitations` (admin:write):
```
{"command_id":"uuid","actor":"authenticated-central-subject","app_instance_id":"...","org_id":"...","identity":"new-id","name":"Name","email":"canonical@example.com","purpose":"API access"}
```
Create only. Existing registry identity or reserved request identity conflicts; NEVER rotates. Creates a native request and reuses native approval/key delivery. Existing pending email conflicts (use existing queue instead). The adapter does not itself send email; existing owner worker delivers.

POST `/internal/admin/v1/requests/{id}/{approve|reject|notify|retry-delivery}`:
```
{"command_id":"uuid","actor":"authenticated-central-subject","app_instance_id":"...","org_id":"...","expected_revision":"opaque","identity":"new-id"}
```
`identity` required only for approve, omitted for all other actions. Revision checked under owner transaction. Exact same command_id+semantic payload returns/resumes the same command; changed payload409. Intent persisted before effects. Do not issue a new command ID to reconcile a timeout.

GET `/internal/admin/v1/commands/{command_id}` (admin:read) reconciles outcomes. All successful command POST responses and GET outcomes use exact fields:
```
{"command_id":"uuid","app_instance_id":"...","org_id":"...","actor":"...","operation":"invitation|approve|reject|notify|retry-delivery","status":"ACCEPTED|SUCCEEDED|FAILED|RETRYABLE","request_id":null,"identity":null,"error_code":null,"created_at":123.0,"updated_at":123.0}
```
POST200 SUCCEEDED;202 ACCEPTED/RETRYABLE;409 FAILED (same command response schema). GET200 any recorded status;404 unknown command. Validation422/auth401or403/notfound404/scope404/commandIDpayloadconflict409 are `{"detail":"safe_code"}` errors. RETRYABLE has safe error_code and same-command POST can resume; GET is read-only and never reissues. Outcomes do not imply key email success: inspect request delivery separately. Exact successful replay makes no additional issue/delivery. Unknown/ACCEPTED after process loss reconciles via same-command POST only (resumable owner approval), never a new invitation.

No raw/signed URLs, credentials, key hashes or encrypted envelopes enter this API. Requests and commands are bounded to16KiB; strict unknown-field rejection. Inventory responses capped256KiB; very large per-identity history fails unavailable rather than truncating silently. No rotation/revoke operations exist.


## Owner deployment and recovery

Run this separate TLS service from the same reviewed API/admin-compatible image,
with the existing QSIM_DATA PVC writable and QSIM_TOKEN_FILE registry projection
read-only. QSIM_ADMIN_SECRET_DIR supplies the existing envelope encryption key;
no key material is copied to Authconsole. The service account needs get/update on
exactly qsim/qsim-users, using the existing resourceVersion compare-and-set path.
TLS certificate/key and management grants are separate mounted private references.
Do not reuse welcome-only grants or expose this endpoint on the public ingress.
The existing qsim-admin notification loop alone delivers key emails. This service
initializes owner tables but starts no notification loop.

Commands and identity reservations persist in the existing owner database. Each
command's private lock file lives under QSIM_DATA/.admin-command-locks; kernel
locks release on process death. Never discard command history or reservations to
retry an unknown outcome. Read the command, then resume the exact same payload/ID
if ACCEPTED or RETRYABLE. Native APPROVING state resumes the encrypted envelope,
so API/registry reply loss cannot generate a second key. A failed registry CAS may
require operator reconciliation; reservations deliberately remain conservative.
Database-only reject/notice/retry actions commit their outcome atomically with the
native update. A delivery outcome is separate from command success.

A rollback disables management access and restores prior image/config while
preserving database tables, reservations, request state and the hash registry.
The legacy LAN console retains its routes and CSRF boundary. It honors outstanding
management identity reservations. No rotate/revoke endpoints were added.

Validation uses synthetic local identities, an injected registry, an HTTP CAS mock,
and the existing owner delivery tests. Deployment and live emails are separate gates.
