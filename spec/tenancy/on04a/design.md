# ON04A: WQS read-only welcome provider

Status: **implementation and reviews approved**, 2026-10-16; see [approval receipt](approval.md). This is a smaller first increment of the separately drafted tenancy design, not authorization for its token-authority migration. Merge, credentials, deployment, activation and mail remain gated.

## Overview

Expose fresh, sanitized welcome eligibility for one explicitly configured legacy WQS app instance and Wehden organization. WQS remains the token authority and sends original API-key mail. Central authconsole sends credential-free welcome/resend and stores its receipts. No issuance, revocation, rotation, schema migration or tenant authorization change is included.

This mapping is onboarding metadata only. It does not establish organization isolation in the job API. Report `organization_enforcement=false`, `additional_organizations=false` and `first_use_tracking=false` truthfully.

## Architecture (components, responsibilities, and boundaries)

A separate private `qsim.welcome_provider` ASGI process uses the existing API image and dependencies. It does not mount its router in either public API or browser admin app. It reads an operator-approved mapping, SQLite through a read-only connection, and the current Kubernetes invitation Secret through the API. A dedicated service account can GET only the named invitation Secret; no list/watch/update permissions.

Use a separate Deployment and ClusterIP Service, no public ingress. Share the existing database PVC read-only, preserving its placement constraints. Never import adminweb, start its notification loop, or call store.init. The existing LAN Host/Origin policy is irrelevant to this process and must not be spoofed or relaxed.

## Data Flow (step-by-step; include a Mermaid sequence diagram when useful)

1. Authenticate the central service and check its configured audience, scopes and exact app/org grant before loading applicant data.
2. Read and validate the approved immutable mapping revision. Open SQLite `mode=ro`, set `query_only=ON`, and start a bounded read transaction.
3. GET the current named invitation Secret through Kubernetes HTTPS with service-account authentication and cluster CA verification. Do not use the API pod's projected invitation file, whose propagation may lag revocation.
4. Join each mapped identity to exactly one approved request and current registry entry. Require active registry state and an internal constant-time match between its hash and the approved request's token hash. No hash leaves WQS.
5. GET the Secret resourceVersion again and recheck the mapping revision before responding. If either changed, retry the whole read once; continued change or upstream failure returns 503 without contacts.
6. Return sanitized eligibility and an opaque revision. Central re-fetches the individual subject immediately before enqueueing each welcome/resend, requiring the same revision and `eligible=true`. Mail retries after expiry require a new read.

The read proves registry state during its observation interval, not a guarantee against a revocation immediately afterward. A credential-free welcome conveys no access. Do not label this an atomic send-versus-revoke protocol or a test of a plaintext bearer credential.

## Public Interfaces (APIs, CLIs, schemas, config)

### Exact proposed protocol

Contract version `wqs-welcome/1`. Three private GET routes only:

- `/provider/v1/capabilities`
- `/provider/v1/apps/{app_instance_id}/orgs/{org_id}/contacts?limit=50&cursor=...`
- `/provider/v1/apps/{app_instance_id}/orgs/{org_id}/subjects/{subject_id}`

All routes require `Authorization: Bearer <service-credential>`. The instance/org must exactly match both configured binding and credential grant. Return 404 for unknown/out-of-grant scope or subject; never enumerate other scopes. Reject unknown query fields and invalid identifiers. No request may choose recipient email or identity alias.

Capabilities include configured app/org IDs, `auth_mode=api_tokens`, `read_only=true`, contract version, `welcome_eligibility=true`, `organization_enforcement=false`, `additional_organizations=false`, `first_use_tracking=false`, and supported GET operations. No token-management capability is advertised.

Contact schema: `subject_id`, `membership_id`, `app_instance_id`, `org_id`, `display_name`, `recipient_email`, `eligible`, `reason`, `revision`, `observed_at`, `expires_at`, `first_use="unknown"`, and sanitized `key_delivery` containing state and nullable SMTP-accepted timestamp. Membership ID is a stable mapping ID, not a claim that a native WQS membership table exists. The schema explicitly records `binding_kind="operator_reviewed_legacy_mapping"`.

List returns only eligible mapped subjects, sorted by immutable subject ID, plus `next_cursor` and `snapshot_revision`. Limit 1–100. Individual reads return a mapped subject's eligibility even after revocation, with `recipient_email=null` when ineligible. Reasons are a fixed enum: `eligible`, `inactive_identity`, `unapproved_request`, `binding_mismatch`, `delivery_not_sent`, `mapping_disabled`. Unknown subjects return 404. Malformed/ambiguous registry or mapping is 503, not an empty successful list.

Eligibility requires active current key proof AND APPROVED request AND `delivery_state=SENT` AND approved mapping. A previously approved but rotated key fails the hash-binding check until an operator reviews a new mapping to its authoritative request. Historical SENT is only original key-mail SMTP acceptance, not inbox delivery. There is no "never logged in" filter.

### Central adapter normalization

Central normalizes the provider's opaque HMAC `revision` as `source_revision`; it does not parse it or substitute an email, request UUID or token identifier. The bootstrap mapping's explicit revision supplies `policy_revision`. A digest of the canonical, versioned capabilities payload supplies `capability_revision`; exclude observation timestamps and transient health fields from that digest. These revisions identify onboarding policy and reported capabilities, not organization isolation. Capability values remain `organization_enforcement=false` and `additional_organizations=false`.

The server-side central adapter registration binds the authentication registration, backend namespace, verified TLS endpoint, service credential reference and exact app/org IDs. Browser input cannot override these bindings or choose a different backend. The provider independently checks its configured app/org grant on every request. Request UUIDs and subject/membership mapping IDs remain opaque identifiers; central must not decode them, infer scope from them or equate them with email addresses. No user key, token hash or encrypted key payload is exported through normalization or registration.

### Authentication/configuration

Use TLS directly at the provider process with a private server certificate and key, served by existing Uvicorn SSL support. Central pins the private CA and verifies the configured DNS name; insecure TLS and plaintext HTTP are unsupported. The implementation exposes no HTTP-to-HTTPS redirect carrying bearer credentials.

Mount a private credential-verifier file containing `credential_id`, SHA-256 digest of a random service token, `audience="wqs-welcome:<app_instance_id>"`, exact org grant, scopes `welcome:capabilities` and `welcome:read`, and active state. Compare digests in constant time. The server chooses expected audience from its own config, not a caller header. A token provisioned for another instance must fail there. Support two explicit active verifier entries for future controlled credential rotation; provision/rotation is outside implementation approval.

Configuration paths: `WQS_WELCOME_BINDING_FILE`, `WQS_WELCOME_AUTH_FILE`, `WQS_WELCOME_INTEGRITY_KEY_FILE`, `WQS_WELCOME_TLS_CERT_FILE`, `WQS_WELCOME_TLS_KEY_FILE`, and existing `QSIM_DATA`. Fixed configured Kubernetes namespace/Secret name come from the binding file, never request input. The integrity key signs revisions/cursors and is distinct from user, cookie and encryption keys. Central holds only its service token and TLS CA. Kubernetes Secret remains the chosen store; no new vault or library.

Authenticated invalid method returns 405; bad/missing bearer returns 401; missing operation scope returns 403. Responses use `Cache-Control: no-store`. Do not expose OpenAPI, browser session endpoints or health details through public routes. A minimal local health/readiness probe must not expose contact data.

## Data Model & Storage (including migrations and idempotency)

No database writes or migration. The mapping is a private versioned file: schema version, revision UUID, one app ID, one legacy org ID and entries with immutable central subject/membership IDs, WQS identity alias, exact approval request ID, reviewed email/name, enabled flag and review provenance. Email must match the stored request; do not infer it from identity/domain. One-to-one mapping and duplicate rejection are mandatory. Unknown/manual identities without an approved request are excluded pending explicit discovery, not silently assigned.

The operator reviews the complete mapping before installation. Changing an ID's meaning is forbidden; disable old bindings and create new IDs. Stable IDs survive credential changes; eligibility revision does not.

Revision is an HMAC of contract version, app/org/subject, full mapping revision/digest, registry resourceVersion, request ID/status/review timestamp, delivery state/timestamp and eligibility outcome. No key/hash/cipher is embedded in the returned token. Including registry resourceVersion invalidates revisions after any registry write, including revoke/reactivate; harmless invalidation of other subjects is accepted. Revisions remain stable for unchanged evidence; observation timestamps are not included.

Cursors are signed, expire after five minutes, and bind the same scope, mapping/registry snapshot, limit and last subject ID. Each page still rechecks current registry evidence. Changed evidence returns 409 `snapshot_changed`; central restarts enumeration. Contact proofs expire after 30 seconds. No server-side contact cache or receipt store. Central owns welcome deduplication and retention; GETs have no side effects or command idempotency requirement.

## Concurrency, Ordering, and Consistency (if relevant)

SQLite read transaction supplies a consistent request view; two registry reads bracket it. Validate mapping stability before returning. Abort on disagreement instead of mixing versions. Limit each Kubernetes call to five seconds, with at most one full observation retry and a 15-second provider request budget. Missing readable WAL/shared-memory support fails readiness; never copy a database and represent it as current. Validate read-only PVC/WAL behavior in an isolated pod before rollout.

Central revalidates the single subject before enqueue and stores the provider revision/expiry with its own welcome receipt. Outbox send after proof expiry revalidates again; on ineligibility it suppresses the greeting. This bounds staleness without claiming distributed atomicity. There are no simulation timeouts or concurrency changes.

## Failure Modes & Recovery (timeouts, retries, circuit breakers, degraded modes)

Missing/malformed mapping, unknown contract, unreadable DB, duplicate identities, TLS/auth failure or unavailable Kubernetes evidence fails closed. No fallback to mounted registry data, previous contact cache, public form or browser session. Existing WQS jobs and original mail continue independently if the provider is down. Retry read failures with bounded central backoff; never replay approval to recover welcome eligibility.

## Security Model (authn/z, permissions, secrets handling, injection defenses)

Read-only service permissions do not make contacts public: email/name are protected applicant data. Require scoped service authentication and TLS on every provider route and restrict ingress to the central workload and probe needs. Restrict egress to the Kubernetes API; no SMTP or simulation access. Read only the necessary request columns; never select key_cipher. Hash comparison remains internal and logs must never include registry payloads or auth headers.

No organization authority derives from email domain, headers, user-chosen IDs or historical requester identity alone. The explicit mapping is an operator-reviewed onboarding assertion for the one legacy org, not multi-org job isolation. Existing private admin behavior is unchanged.

## Observability (signals, dashboards, alerts; what you will measure)

Count authentication failures, unsupported scope, evidence-read failures, snapshot changes, eligibility reasons and latency. Protected audit records include credential ID, operation, opaque scope/subject IDs, revision and outcome. Never log email/name, token/hash/cipher, request purpose or circuit contents. Central records mail outcome separately. Alert on persistent provider unavailability; do not block simulations.

## Rollout Plan (staged rollout, feature flags, backout)

Packet approval permits implementation and isolated tests only. After PR review and owner merge, a separate rollout approval must cover: reviewed mapping, provisioning scoped service credentials/integrity key and TLS, dedicated RBAC/NetworkPolicy, PVC read-only validation, and private Deployment/Service. Deploy disabled from central first; verify approved test subjects and denial cases without sending mail. Enable central provider registration only after evidence review. No CD enablement is part of this work.

Rollback unregisters/disables central provider, then stops its dedicated deployment. No database restoration or credential changes are needed to undo code. Central must suppress pending sends that cannot revalidate. Removing provider credential material is a separately scoped operational action. Never roll back WQS API/job deployments for this read-only provider.

## Alternatives Considered (at least 2, with pros/cons)

- Proxy existing browser admin sessions: fewer endpoints but shared LAN identity, host spoofing and unbounded reads; rejected.
- Central reads Secret/DB directly: simple wiring but exports authority material and storage coupling; rejected.
- Full tenancy/token-database migration: stronger long-term model but unnecessary for one-org credential-free welcome; deferred to the parent gate.
- Do nothing / static owner-reviewed contact list: smallest interim approach, but cannot prove revocation freshness; do not advertise automated eligibility.

## Open Design Questions

No protocol behavior is left to the caller. Deployment inputs remain gates: owner-approved legacy mapping and IDs; central private DNS/CA choice; dedicated workload identity/network selectors; live PVC/WAL permission validation. The central owner must confirm exact contract consumption and outbox revalidation before implementation approval. If current central conventions require a different authentication envelope, revise this packet before code, not at deployment.

## Simplicity Budget (maximum new modules, maximum new public interfaces, dependency addition policy, and a required "do nothing / smaller change" alternative that was evaluated)

Two new runtime modules maximum: `qsim/welcome_provider.py` and `qsim/welcome_evidence.py`. Three private business routes, no public API changes or mutations. Maximum 600 non-test Python lines; exceeding the budget requires a design check-in. No new dependencies, migrations or persistent server-side cache. Tests/docs/private deployment templates do not count toward runtime lines. Static manual contacts remain the smaller non-automated alternative.

## Rejected Abstractions (abstractions considered and explicitly rejected with rationale)

No generalized tenant service, auth-mode framework, key-management API, welcome-mail renderer in WQS, browser-session broker, or distributed lock protocol. Keep existing key delivery separate from central welcome. Do not call eligibility metadata a native membership system.

## Verification Strategy (mapping to requirements and test strategy)

See [verification](verification.md) for exact gates and [tasks](tasks.md) for the bounded write lease. Required coverage: audience/scope denial, zero secret export, stale-proof invalidation on revoke/reactivate, recipient binding, pagination snapshot changes, DB read-only enforcement and no side effects. Negative isolation evidence must pass before central registration; no live revocation/issuance is authorized for a test.
