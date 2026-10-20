# WQS console parity correction

Status: operator approved implementation and reviews, 2026-10-20. Deployment requires the final gate.
Baseline: LaminarAuth `origin/main` at `2bc4c76761351c1205fe92b75640075fe721189a`.

## Evidence and cause

The operator reports missing existing WQS API keys and a disabled “Invite API
user” action. Neither symptom establishes that credentials were deleted.

- `console/templates/onboarding.html:17` hardcodes the disabled invitation button.
- `onboarding/catalog.py:292–315` constructs registrations from explicit bindings.
  Parent’s read-only deployed inspection found a Google-only onboarding config,
  with no WQS binding or provider CA/credential mounts. The deployed template also
  contains the disabled button.
- `onboarding/token_provider.py:1,58–61` implements read-only `wqs-welcome/1` for
  sanitized recipients and welcome/resend. It cannot invite users or enumerate a
  complete credential registry.
- The WQS owner’s live read found six registry identities (five active, one
  inactive), but only four approved requests, all with delivery state SENT.
  Two identities have no approved request. A request-based list omits them.
- Existing approval resumes for the same request and identity using transactional
  envelope/hash reservation, registry compare-and-set and APPROVED completion.
  Conflicting identity/operator selections return 409. Delivery retry requires
  APPROVED, retained encrypted material and FAILED/RETRY/UNCERTAIN delivery state.
  Successful SMTP delivery clears the encrypted material permanently.
- `scripts/invite.py` creates or rotates a private-file key. No HTTP revoke,
  rotate or complete key-list API exists. Calling that CLI as an invitation
  shortcut could rotate an existing identity’s credential.

The cause is incomplete central-console integration: missing deployed read
configuration and an intentionally unimplemented command interface. Earlier
welcome requirements explicitly describe a disabled token page as staging.

## Scope and approval

Base spec Gates 1–4 are approved; the console usability amendment permits bounded
implementation and review without repeated intermediate gates. ON-04A/ON-04A-C
already cover the existing-scope read-only provider. However,
`spec/welcome-packet/tasks.md:174–209` explicitly excludes central key issue,
approval and delivery retry until independently verified.

Approve this corrective packet and its verification/security/final reviews for
the new command scope. Preserve earlier read-only approval. Deployment remains a
separate final gate with exact images, configuration patch and rollback evidence.
Only the existing explicitly mapped WQS bootstrap organization is in scope.
No new tenant, registry migration, revoke/rotate feature or Google-flow change.

## Recommended contract

Add a WQS-owned service-authenticated management adapter. Reuse the established
private service-auth mechanism and WQS storage/approval/delivery authority.
The following `/internal/admin/v1` paths are proposed, not existing endpoints.

| Proposed operation | Contract |
| --- | --- |
| `GET /capabilities` | Reports supported inventory and command operations for the configured application/bootstrap scope. |
| `GET /identities` | Cursor-paginated registry-wide metadata, with optional request/delivery history. Include inactive identities and identities without requests. Use the authoritative identity ID; do not invent per-key IDs for a registry containing one hash per identity. |
| `POST /invitations` | Accept recipient, identity ID and idempotency key. Create only; return 409 for an existing identity and never rotate. Reuse WQS request, approval and key-delivery operations. Return command status and stable references, never key material. |
| `GET /commands/{id}` | Return the authoritative outcome for reconciliation after timeout or retry. |
| `POST /requests/{id}/approve` | Preserve existing approval conflict, revision and resumability semantics. |
| `POST /requests/{id}/reject` | Preserve the existing rejection rules and audit identity. |
| `POST /requests/{id}/notify` | Retry the existing request notice under its current eligibility rules. |
| `POST /requests/{id}/retry-delivery` | Preserve approved-state, retained-material and delivery-state checks. Successful prior delivery does not imply recoverable key material. |

WQS binds the configured application/bootstrap scope independently of caller
input. Commands carry the authenticated operator attribution and command ID.
Persist invitation intent before side effects; repeated identical idempotency
keys return the recorded operation, conflicting payloads return 409. Reserve the
identity atomically so concurrent invitations cannot overwrite or rotate it.
A lost reply leads to command lookup, not a second issuance attempt.

The console lists registry metadata joined with optional history. Keep identity,
request delivery and welcome status distinct. Welcome resend never retries key
delivery. Add reviewed forms for invitation and existing request actions, using
verified capabilities to enable them. Missing/unavailable integration shows a
reason rather than an empty inventory. Preserve the legacy LAN admin console.
Token values, hashes, encrypted envelopes and service credentials never enter
central views, logs, welcome content or command responses.

## Implementation and acceptance

1. Pin current WQS source/image identity and document the adapter schema against
   existing owner operations. Implement the proposed contract without changing
   native credential lifecycle behavior. Connect the existing read-only provider.
2. Test registry enumeration against the observed shape: six identities, including
   two without approved requests. Assert current live counts separately, since
   they may change. Cover pagination, inactive entries and bootstrap-scope checks.
3. Test create-only invitation success, existing-identity 409, concurrent reservation,
   idempotency replay/conflict, delivery failure and lost-response reconciliation.
   Prove no rotation and no secret material in any central output.
4. Test existing approval/rejection/notice/retry-delivery parity, stale revisions,
   authorization and audit attribution. Preserve the successful-delivery rule that
   encrypted delivery material has already been cleared.
5. Verify browser metadata visibility, review/confirmation target, capability-based
   availability and truthful error recovery. Run console/onboarding and WQS
   credential-lifecycle regressions, then independent security and final review.
6. Present exact tested diff and deployment patch for the final gate. Deploy
   compatible owner support first. Verify authenticated live metadata against the
   authoritative inventory and the operator’s normal console path. A live invite
   requires an explicitly approved recipient and expected delivery; enabling the
   page must not send a campaign or issue test credentials automatically.

Done means existing registry metadata is accounted for and invitation/request
operations work through WQS authority with audited, reconciled outcomes. An
active button or green unit suite alone does not establish parity.

Rollback disables the new command capability and restores prior image/config
references while preserving keys, requests, audit and delivery records. Reconcile
in-flight commands before retrying; never implicitly revoke an accepted invite.

## Implementation candidate

The optional internal-only `LAMINARAUTH_WQS_MANAGEMENT_CONFIG` points to a JSON
object containing `endpoint` (HTTPS origin), absolute `ca_file` and
`credential_file` references, `app_instance_id` and `org_id`. No production config
is changed by this candidate. Existing console Host/Origin and request-header
checks protect commands. Actor attribution uses the existing shared internal
operator principal; it does not establish individual human attribution.

Registry enumeration and the independent request queue are both required because
pending requests need approval before a registry identity exists. Browser command
intent persists in session storage before POST. Recovery reads the recorded
command or explicitly resumes the same ID/payload; it never creates a replacement
ID automatically. Raw key values remain entirely within WQS authority.


## WQS implementation receipt

- [x] Isolated owner worktree and exact management wire contract published.
- [x] Registry-wide metadata and independent request queue with opaque snapshots.
- [x] Durable, attributed command intent; create-only invitation and existing actions.
- [x] Native approval/CAS/delivery semantics retained; no duplicate SMTP worker.
- [x] Focused owner approval, welcome-provider and new management regression tests.
- [ ] Independent security/regression review and final correction verification.
- [ ] Deployment approval with exact tested image/config/rollback evidence.

No live invitation, registry mutation, mail send, merge or deployment is authorized
by this implementation receipt. A later live invitation needs the approved recipient.
