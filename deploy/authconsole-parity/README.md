# WQS Authconsole deployment candidate

Prepared from owner code f1190b5; NOT approval to apply. All three processes use
`laminarflow:30500/qsim/admin-console@sha256:95d63f5e25295dc9a78b9d6e99709a948895fcbc75b3eff1a1625fd68774a188`.
The root build/import check passed without network. Management and legacy tests:
31 passed; prior full suite: 639 passed, 6 subtests; isolated real HTTP/TLS/browser
acceptance passed using synthetic registry, fake SMTP and a real API key check.
Independent owner security review passed. Live TLS/network/SMTP remain untested.

## Fixed proposed connection

Scope is `wqs-main` / `wehden`, subject to final operator approval. Management
origin is `https://wqs-management-provider.qsim.svc:8443`; separate welcome origin
is `https://wqs-welcome.qsim.svc:8443`. Both schedule on Laminarflow with existing
`qsim-data-evac-20261016`. Management reuses `qsim-admin` service account and existing
`qsim-admin` encryption material; only native admin runs the SMTP worker. Welcome
has its own GET-only registry Role and read-only database mount. No public ingress.

| Owner Secret | Keys | Consumer |
| --- | --- | --- |
| `qsim/wqs-management-auth` | `binding.json`, `auth.json`, `integrity-key` | Management `/run/welcome` |
| `qsim/wqs-management-tls` | `tls.crt`, `tls.key` | Management `/run/welcome-tls` |
| `qsim/wqs-welcome-auth` | `binding.json`, `auth.json`, `integrity-key` | Welcome `/run/welcome` |
| `qsim/wqs-welcome-tls` | `tls.crt`, `tls.key` | Welcome `/run/welcome-tls` |
| `laminarauth/wqs-management-client` | `ca.crt`, `token` | Central management binding |
| `laminarauth/wqs-welcome-client` | `ca.crt`, `token` | Central welcome binding |

Paths retain the shared provider loader conventions; management uses the separate
`WQS_ADMIN_*` variables and independently scoped grants. File mode0440/fsGroup1000.
Do not mount `qsim-smtp` into either provider. The welcome mapping is not derived
from management inventory at runtime.

## Private input preparation after final approval

Use a private temporary directory (umask077) on the approved custody host. Generate
independent 32-byte random service credentials and integrity keys with Python
`secrets.token_urlsafe(32)` / `secrets.token_bytes(32)`, writing directly to files,
never stdout or command arguments. SHA256 each service credential into its owner
`auth.json`; central gets the corresponding plaintext token file. Grant array
entries contain `credential_id`, `sha256`, `audience`, `org_id`, `scopes`, `active`.
Management audience `wqs-admin:wqs-main`, scopes `admin:read,admin:write`; welcome
`wqs-welcome:wqs-main`, scopes `welcome:capabilities,welcome:read`; org `wehden`.
Use distinct credentials for both services; no user API key reuse. Begin management
with read-only scope until native admin rollout and authenticated inventory pass.

Issue separate server certificates using the approved private CA with serverAuth,
SANs `wqs-management-provider.qsim.svc` and its `.cluster.local` form, and likewise
`wqs-welcome.qsim.svc`. Keep CA signing keys outside pods. Transfer only each server
certificate/key to its owner Secret and CA certificate to central. Verify chain,
SAN and expiry locally. Secret creation is a later approved custody action, using
`kubectl create secret generic ... --from-file=... --dry-run=client -o yaml` into
private files for review, then approved apply; never commit those generated files.

The management binding candidate has no member map. The welcome mapping proposal
is held privately at `/tmp/wqs-welcome-mapping-candidate.private.json` (0600).
Read-only authoritative registry/SQLite inspection found 6 registry identities,
3 active approved matching identity/request/hash candidates and 3 inventory-only
identities. This is PROPOSED_NOT_REVIEWED, not a binding receipt. Review each
recipient/name/identity/request, establish stable IDs, remove `delivery_state`,
and add actual `reviewed_by`/`reviewed_at` only upon approval; wrap in schema 1
binding with reviewed revision and fixed scope/registry. Do not invent receipt
fields or include unmatched identities. Registry values/hashes were not exported.

## Ordered activation and rollback

1. Refresh live admin deployment and compare the guarded JSON patch tests. Capture
   current owner/central image and config refs. Review exact mapping, TLS/grants,
   central config preserving Google entries, and both network policies.
2. Apply `admin-image.candidate.json` as JSON patch ONLY after ship approval. It
   changes only image and guards container name/prior pin, preserving every native
   env/volume/probe/worker setting. Wait for admin readiness before central writes:
   old admin does not honor new management identity reservations.
3. Install approved private inputs and provider manifests. Management readiness
   checks DB/schema/grants/encryption; welcome checks read-only SQLite/WAL/registry.
   Confirm service DNS TLS and wrong/missing/scope-limited grant denial. Compare
   complete registry/request metadata with owner counts; never print keys/hashes.
4. Central owner applies matching console egress and independent management/welcome
   connections. Prove metadata UI and welcome eligibility separately; activate write
   grant only after review. No live invitation without approved recipient.
5. Rollback disables central command admission first, reconciles ACCEPTED/RETRYABLE
   outcomes, restores central config/image and stops providers. Keep updated admin
   until all identity reservations/outcomes are reconciled; do not restore an old
   reservation-unaware writer while commands may resume. Preserve additive tables,
   requests, delivered keys and audits; no DB deletion or automatic key revocation.

The JSON patch prior-image test deliberately fails if deployment changed. The
candidate NetworkPolicies require API endpoint 192.168.1.205:6443 and DNS; validate
actual CNI service translation during approved rollout. No broad SMTP egress.

Welcome registration proposal: `backend_namespace=qsim`,
`policy_revision=wqs-main-wehden-v1`, `capability_revision=`
`8710b80c8153be9d6ea320d33c8bdca5ed884a1d8f22962babda3c0205a567c1`.
This digest derives from the fixed capability schema and proposed scope/revision;
verify against the authenticated running provider before registration. The mapping
is still awaiting review; these proposed identifiers are not a mapping receipt.
