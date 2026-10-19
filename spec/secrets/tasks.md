# Secret-management implementation tasks

## Task Graph Overview (short)

TASK-001 discovery precedes TASK-002 preflight and TASK-003 operating procedures. TASK-004 verifies their combined result. No parallel executors are required.

## Tasks (the full list)

### TASK-001: Reconcile consumers and existing controls

- Goal: establish current mounts, writer permissions, credential reload and backup limits (S1, S4, S6).
- Areas: private inventory outside public repository; spec/secrets evidence status.
- Steps: inspect Secret references without values; inspect updater and consumer source; identify backup/encryption evidence gaps.
- Verification: compare private inventory against live deployment references and namespace RBAC; record unknowns instead of assuming encryption or restore success.
- Backout: read-only discovery; remove only newly written local evidence if incorrect.
- Change budget: max_files 2, max_new_symbols 0, interface_policy none.
- write_lease: `^spec/secrets/tasks\.md$`; private evidence remains outside tracked source.
- Risk: Low, metadata-only discovery. Recommended Mode: security-sentinel.

### TASK-002: Enforce required Secret references in CD

- Goal: fail before publication/hold and recheck before deployment when a required Secret or key is unavailable (S2, S3).
- Areas: tools/cd/release.py, tests/test_cd_release.py, workflow timeout commentary.
- Steps: require a bounded allowlist in private release config; validate names/keys; fetch named resources only; suppress raw command diagnostics; integrate two checks without credential mutations.
- Verification: focused pytest covers success, malformed/empty config, missing keys, denied reads, timeout, retry exhaustion, non-disclosure and deployment ordering.
- Backout: revert code PR; do not change credentials or delete release state.
- Change budget: max_files 3, max_new_symbols 16 including tests, interface_policy extend-only (private config requires migration).
- write_lease: `^tools/cd/release\.py$`, `^tests/test_cd_release\.py$`, `^\.github/workflows/cd\.yml$`.
- Risk: Med, missing config blocks future releases. Recommended Mode: executor.

### TASK-003: Document provisioning, rotation and recovery

- Goal: document the private inventory and acceptance gates (S1, S4, S5, S6, S7).
- Areas: docs/secrets.md, docs/releases.md, docs/deployment.md, spec/secrets.
- Steps: document exact preflight config; distinguish file and environment reload; keep encryption-key recovery separate from image rollback; record unresolved live gates.
- Verification: match docs against actual code; inspect diff for private inventory/values; validate boundary JSON.
- Backout: revert documentation only.
- Change budget: max_files 10, max_new_symbols 0, interface_policy none.
- write_lease: `^docs/(secrets|releases|deployment)\.md$`, `^spec/secrets/[^/]+$`.
- Risk: Low. Recommended Mode: executor.

### TASK-004: Verify and open implementation PR

- Goal: demonstrate code behavior without production mutation (S2, S3, S7).
- Areas: tests and verification record.
- Steps: run focused tests then existing full CI command; scan diff; open PR for owner merge; allow automatic review, maximum two rounds.
- Verification: exact-head CI green, default Standpunkt path reported, no unresolved actionable review findings. An isolated-cluster drill and production acceptance remain separate pending work.
- Backout: keep PR unmerged on failure; report CI failure to owner.
- Change budget: max_files 1, max_new_symbols 0, interface_policy none.
- write_lease: `^spec/secrets/verification\.md$`.
- Risk: Low. Recommended Mode: test-engineer.

## Definition of Done Checklist

- [x] Private inventory reconciled and gaps recorded.
- [x] Preflight and failure-path tests pass.
- [x] Operating procedures and config migration documented.
- [x] Exact-head CI and clean automatic review complete; owner merged PR #22 as `1375c078dee7b658d42f94aa0ce3fd5f2a3c5152` on 2026-10-17. Main CI passed.
- [x] Live synthetic projection/revocation and named Secret access/denial drill passed (46.65 seconds observed projection delay).
- [x] Synthetic encrypted recovery and historical archive database-integrity drills passed within their documented scopes.
- [x] Restricted deployment runner registered; zero-replica infrastructure and named-resource RBAC checks completed.
- [ ] Install the private release configuration and hooks, and verify the real runner execution path. Registration alone does not close deployment acceptance.
- [ ] Current complete encrypted backup, encryption-at-rest/key-custody evidence and production recovery acceptance remain pending.
- [ ] Credential rotation explicitly deferred by owner; no rotation performed or required to close the non-rotation checks.

## Execution Notes (tooling, environment, checkpoints)

Use a clean worktree from origin/main; preserve unrelated spec drafts. Use tools/ci/check.sh for full checks and pytest tests/test_cd_release.py for focused checks. The normal CI route remains Standpunkt-first. PR #22 made no Kubernetes mutations, credential changes, mail or CD activation. On 2026-10-17 the owner authorized operational acceptance without credential rotation; the follow-up used an isolated namespace and synthetic fixtures only. Production encryption and backup verification need operator evidence; do not close these items from unit tests.

## Traceability (REQ IDs -> TASK IDs)

S1: 001,003. S2: 002,004. S3: 002,004. S4: 001,003. S5: 003. S6: 001,003 and separately approved live drill. S7: 003,004.


## Operational acceptance follow-up (2026-10-17)

Owner authorized reconciliation, operational acceptance and coordination with Laminar on encryption/backups, while excluding credential rotation. See verification.md for evidence and remaining gates. A projection-delay sample is an observation, not a promised revocation SLA. The private required_secrets fragment is prepared but not installed; no production release configuration exists to update. Do not mark bootstrap_verified or enable CD from these checks.

## Reconciliation, 2026-10-19

PR #24 is merged. The deployment runner is registered but remains stopped. Application acceptance hooks are implemented locally; the full local suite passed 390 Python tests and three console tests, with a separate runner-image smoke check. PR review, merge, installation and live acceptance remain pending. LaminarAuth now publishes the v1 recovery contract and schema; WQS adapter integration is the next implementation task. Custody, encryption and exact-set recovery evidence remain required. CD stays disabled, and credential rotation stays deferred.

## Provider implementation follow-up, 2026-10-19

PR #25 (application acceptance) and PR #26 (capture adapter) are merged. Concrete
snapshot, recipient/encryption and conditional object-storage providers are now
implemented for review, with isolated tests and a pinned age tool in CI. They are
not installed. The mounted-source coordinator requires an approved arrangement for
application data and parent runner state, or a separately reviewed capture transport.
The v1 contract also blocks empty result/history collections; this was reported to
LaminarAuth rather than filled with fabricated evidence. Gate 1 operational
acceptance and gate 2 custody/authority clearance remain open. CD is disabled and
credential rotation remains deferred. See docs/backup-providers.md for exact limits.
