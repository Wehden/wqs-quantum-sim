# WQS secret management design

Status: approved by owner on 2026-10-16; implementation authorized after clarification that Kubernetes Secrets is the selected store.

## Overview

Use Kubernetes Secrets as WQS's current runtime credential store. Keep application releases independent of credential provisioning. The work closes gaps in inventory, CD preflight, permissions, rotation and recovery without introducing Vault or changing API-token authority.

This design is based on the observed cluster resources and current application interfaces. It does not establish that Kubernetes storage is encrypted or that an upstream secret manager is absent. Those remain operational discovery gates; they do not block implementing the read-only preflight.

## Architecture (components, responsibilities, and boundaries)

Kubernetes holds namespace-scoped runtime secrets. An operator owns provisioning through the existing private administrative process. Application pods receive only the files or keys they consume. WQS remains responsible for invitation-key hashing and authentication.

The application repository owns reference validation, examples and tests. Private operations configuration owns actual values, resource bindings, backup locations and operator access. CD checks dependencies and deploys immutable application images; it is not a credential issuer.

Existing roles remain distinct: the invitation updater needs narrowly scoped registry mutation; API consumers normally read projected files; workers and observers receive no new Secret access. Deployment permissions themselves can enable secret exposure through changed pod specifications, so the trusted deployment runner remains a privileged boundary even without direct Secret-read RBAC.

## Data Flow (step-by-step; include a Mermaid sequence diagram when useful)

1. An operator provisions or updates a credential using the approved private process.
2. Kubernetes projects the selected Secret keys into the consuming workload.
3. Before application rollout, CD validates that required references and key names exist. Validation emits names and pass/fail results only.
4. The application reads the credential using its existing interface. Rotation follows the consumer-specific procedure below.
5. Post-deployment acceptance verifies authentication, invitation handling and relevant integration health without logging credentials.

Secret-volume propagation and process reload are separate events. Environment variables require a restart. File consumers must be checked for caching and subPath behavior before their rotation procedure is approved.

## Public Interfaces (APIs, CLIs, schemas, config)

No new public API or credential format. Preserve QSIM_TOKEN_FILE, QSIM_ADMIN_SECRET_DIR and QSIM_SMTP_PASSWORD_FILE and their existing file contracts. Preserve the currently required qsim-users registry binding until a separately approved compatibility change.

The internal release preflight accepts an allowlisted set of required namespace/name/key references from private deployment configuration. It returns a nonzero exit status on missing or inaccessible references and prints no values, encoded payloads or credential digests. Do not persist Kubernetes Secret JSON as an artifact. Where the API returns a full Secret, parse in memory with shell tracing disabled and bounded diagnostics.

Boundary artifacts accompany this design. Empty module lists mean there are no new Python exports or import boundaries in this configuration-focused design; update them before approval if implementation needs runtime code.

## Data Model & Storage (including migrations and idempotency)

No database migration. Keep invitation hashes in the existing registry, encrypted pending-delivery payloads in their existing storage and their encryption key separate from the database. No raw API-key archive is introduced.

Maintain a private inventory containing reference, owner, consumers, key names, rotation procedure, last verification and backup dependencies. Never include values. Preserve Secret names during the initial rollout. Preflight is read-only and repeatable; it must never create a missing Secret as a side effect.

## Concurrency, Ordering, and Consistency (if relevant)

Retain the existing invitation updater as the registry writer. Confirm its resourceVersion conflict handling before altering registry management; do not add a competing sync controller over qsim-users.

Serialize planned rotation per credential. Verify a new credential before retiring the old one where the upstream service supports overlap. For invitation keys, test projected-volume propagation and record the observed revocation window. Security-sensitive immediate revocation needs a separately verified mechanism; this draft does not claim one.

## Failure Modes & Recovery (timeouts, retries, circuit breakers, degraded modes)

Missing or malformed auth configuration must fail closed using the existing authentication behavior. Preflight denial or timeout stops deployment, with bounded retries for transient API failures. It must not retry by generating credentials or broadening RBAC.

SMTP failure retains the existing delivery-state and retry behavior. Never regenerate the encryption key while retained payloads depend on it. Loss of that key is not fixed by rolling back an application image.

A failed rotation restores the prior configuration only if the credential is still valid and has not been revoked for compromise. Recover encrypted payloads only with the corresponding encryption material. If either is unavailable, stop delivery and use an explicitly approved recovery procedure.

## Security Model (authn/z, permissions, secrets handling, injection defenses)

Treat Kubernetes Secrets as access-controlled storage; base64 encoding provides no encryption. Verify control-plane encryption configuration and key custody without printing key material. Encrypt off-cluster backups and restrict access to backup decryption keys.

Use projected service-account credentials and narrow resourceNames permissions when the operation supports them. Do not grant namespace-wide Secret list/watch merely to implement preflight. Validate namespace and resource names as data; do not interpolate arbitrary values into shell commands.

Keep secrets out of Git, container layers, build arguments, screenshots and test reports. Use synthetic test credentials. Public examples use reserved names and placeholders. Operational inventories remain private.

## Observability (signals, dashboards, alerts; what you will measure)

Record preflight outcome, missing reference category, authentication-configuration failures, invitation delivery failures and rotation verification time. Do not log tokens, hashes, ciphertext or secret-derived checksums. Use a non-sensitive rotation identifier for correlation.

Record the measured revocation propagation interval during acceptance and the date/outcome of the restore exercise. Alert on failed preflight and delivery failures through existing operational channels; no new monitoring service is needed.

## Rollout Plan (staged rollout, feature flags, backout)

1. Reconcile the private inventory and identify any external provisioning tool. Verify storage encryption and backup arrangements before deciding whether infrastructure changes are needed.
2. Add documentation and synthetic tests through a PR. Owner merges. Observe the normal two-round automatic review limit.
3. Implement the internal preflight against an isolated namespace, including denied access and missing-key cases. Use Standpunkt for normal CI and the existing hosted fallback.
4. Obtain deployment approval, run preflight against production and verify existing consumers without rotating credentials.
5. Schedule individual rotations and an isolated restore exercise only after their procedures are reviewed.

Backout restores the previous application/configuration revision. It does not restore obsolete or compromised credentials. No blanket Secret deletion, database restore, CD activation or cluster encryption-key rotation is authorized by this design.

## Alternatives Considered (at least 2, with pros/cons)

- Keep the current setup unchanged. Lowest effort, but leaves provisioning, rotation and recovery assumptions unverified.
- Introduce Vault. Supports centralized policy and credential lifecycle, but adds service availability, initialization, recovery and maintenance obligations. No current WQS requirement justifies it.
- Use External Secrets with an existing external backend. Useful if an established authoritative store is discovered; adds reconciliation and ownership concerns, especially for the application-written invitation registry. Reconsider after discovery, without making two writers authoritative over the same object.

## Open Design Questions

- Discovery Needed: cluster operator must confirm control-plane encryption at rest, key custody, backup encryption and tested restore status. Inspect private operational configuration, not public example manifests.
- Discovery Needed: identify the existing provisioning source, if any, for all four observed Secrets. An external store was not ruled out by the Kubernetes inventory.
- Discovery Needed: inspect current deployment mounts and qsim/adminweb.py plus notification consumers for startup caching, subPath mounts and restart requirements.
- Discovery Needed: inspect current release scripts and private deployment-runner RBAC to choose the smallest reference-preflight integration point. No new privileged CI token is presumed.
- Discovery Needed: agree a measurable revocation acceptance window and identify upstream overlap support for SMTP and tunnel credentials.

## Simplicity Budget (maximum new modules, maximum new public interfaces, dependency addition policy, and a required "do nothing / smaller change" alternative that was evaluated)

Maximum zero new application modules, zero public interfaces and one internal preflight helper only if existing release tooling cannot express the checks. No new runtime dependencies. Prefer documentation plus existing Kubernetes client tooling. The smaller documentation-only change is acceptable if discovery proves equivalent preflight already exists; the verification and restore evidence still remain required.

## Rejected Abstractions (abstractions considered and explicitly rejected with rationale)

Reject a generic multi-provider secret abstraction: WQS has no demonstrated need for interchangeable backends. Reject an in-application credential broker and automatic rotation controller: both expand token authority and recovery behavior beyond this task. Reject a second declarative writer for the live invitation registry because it could overwrite approvals or revocations.

## Verification Strategy (mapping to requirements and test strategy)

| Requirement | Evidence |
| --- | --- |
| S1 | Existing configuration and authentication tests pass; dependency diff is empty. |
| S2 | Diff and release-artifact scan; canary credentials absent from stdout, stderr and artifacts in negative tests. |
| S3 | Isolated tests for present/missing keys, forbidden access, transient errors and timeout; preflight issues no mutations. |
| S4 | Private inventory reconciles to deployed consumers; RBAC positive and negative authorization checks. |
| S5 | Synthetic approval/authentication/revocation tests and observed projection/restart behavior; pending delivery survives supported recovery. |
| S6 | Operator evidence of storage/backup encryption plus an isolated restore exercise with matching encryption material. |
| S7 | Owner approval recorded before implementation; implementation PR, CI and deployment approvals tracked separately. |

Do not report production acceptance from unit tests or green CI alone. The live rotation and recovery checks require their own approved execution and evidence.

Implementation detail: `required_secrets` is mandatory private configuration, limited to 16 named references and 64 keys per reference. Each of the two passes permits two 10-second attempts per reference with a one-second delay. Inventory completeness is operator-reviewed. No automatic reference discovery or application modules are introduced.
