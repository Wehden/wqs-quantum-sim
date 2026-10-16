# ON04A verification gates

Status: planned, no tests or production changes claimed by this document.

| Gate | Evidence required |
| --- | --- |
| V1 Authentication | Missing/invalid service bearer rejected; instance-B credential cannot read A; missing scope and other org rejected; browser cookie/Host/Origin confer no access; TLS hostname/CA failure stops central connection |
| V1a Central normalization | Exact `api_tokens` enum; opaque revision becomes source_revision; mapping revision supplies policy_revision; stable canonical capabilities digest supplies capability_revision; browser input cannot override adapter auth registration/backend namespace/app/org/endpoint; no isolation claims or credential export |
| V2 Binding | Exact reviewed subject/request/identity/email match; duplicate mapping/registry rejected; unknown/manual/unapproved identities excluded; no domain inference; request input cannot override recipient |
| V3 Active proof | Synthetic revoked identity becomes ineligible and revision changes; revoke/reactivate changes revision even if final active state matches; old request hash with new registry key is ineligible; no token/hash/cipher exported |
| V4 Delivery | Only APPROVED+SENT+current active key is eligible; failed/pending delivery excluded; SMTP acceptance labelled correctly; welcome does not mutate original delivery fields |
| V5 Pagination | >500 synthetic contacts enumerate without omissions; bounded page sizes; tampered/expired/cross-scope cursor rejected; registry/mapping change returns snapshot_changed and requires restart |
| V6 Consistency | Registry update between bracketed reads retries once then fails closed; SQLite write during observation cannot yield a mixed request snapshot; no fallback on API outage/malformed registry |
| V7 Read-only | SQLite opened mode=ro/query_only; before/after database and registry unchanged; no store.init, mail, Secret PUT, enrollment or background worker called; capture side-effect mocks |
| V8 Freshness | Proof expiry 30 seconds; central send revalidates expired proof and suppresses ineligible recipient; demonstrate residual post-read revocation race without claiming atomic delivery |
| V9 Retention/security | GET responses no-store; captured responses/logs contain no raw/hash/cipher, applicant purpose or circuit; only intended recipient fields on eligible records; no server contact cache |
| V10 Existing behavior | Full existing test suite, local-first routing and required image checks green; job API/auth behavior unchanged |
| R1 Approved rollout only | Dedicated exact-name GET RBAC; no broad Secret access; TLS verification; central-only ingress; SQLite WAL with read-only mount; unknown binding fails readiness; no mail or live credential mutation |
| R2 Backout | Disable registration; pending mail cannot use stale proof; stop provider; original WQS service/jobs/mail unaffected; no DB restore |

Baseline refreshed 2026-10-16: origin/main `21a6473594d0a2b63a92eb3d4b569ed0952dbdb2`; deployed admin image digest `sha256:62485824b8641e9add89f29f39abf67833e6dd4f98d53e0b50710450371ebb06`. Existing evidence in parent context covers deployed ownership schema and mail behavior. Refresh live source again at rollout, especially registry writers and request/delivery fields.

Maintained references: qsim/adminweb.py Registry.ensure, approve_request and request reads; qsim/auth.py identity; qsim/notifications.py deliver_one; qsim/store.py key_requests. No private credentials or recipient inventory belong in public fixtures.

## Implementation evidence (2026-10-16)

The separate implementation worktree contains two new runtime modules (416 lines),
three private business GET routes, synthetic golden responses and placeholder
manifests. No existing user API, token authority, mail code, schema, dependencies
or CD workflow changed. The packet's parent remains a separate local draft.

Local focused verification: 45 tests passed, including a real localhost TLS
server, trust/hostname rejection, service-grant revocation, synthetic user-key
revocation/reactivation, evidence races, 501-contact pagination, read-only SQLite,
output/log redaction, deadline failure and the no-mutation route boundary. The
first complete local run passed 383 Python tests plus 3 console tests; a final
complete run follows the added timeout/contract/template checks. Final counts and
exact-head CI/image identities are recorded in the PR handoff, not inferred here.

Security review performed during implementation: verified public/admin routers
are untouched; service auth ignores browser cookies/proxy scheme headers; registry
transport is GET-only with CA verification and trust_env disabled; SQL access is
read-only/query-only; no initialization/outbox imports exist; responses and errors
exclude hashes/ciphertext; cursor/proof signatures use a separate integrity key;
unknown tenant contexts fail closed. Automatic PR review remains a separate gate.

A deadline test exposed exception propagation after a timed-out synchronous
handler. Evidence work now runs through a bounded async wrapper that returns a
sanitized 503 while late worker exceptions remain internal. SQLite connections
are explicitly closed after rollback, including observation retries.

Not verified or authorized: real mapping/credential installation, cluster RBAC,
TLS trust deployment, read-only PVC/WAL behavior, central outbox implementation,
live welcome eligibility, mail delivery or multi-org enforcement. V8's central
outbox behavior and R1/R2 require their respective owners' later gates.

Automatic review round 1 identified applicant mapping parsing before bearer
validation. Authentication now checks the verifier and immutable startup audience
and org before reading mapping records. A regression test asserts that invalid
bearers and missing read scope cannot trigger the mapping reader. A separate
configuration-race test verifies sanitized failure without leaking exception data.
