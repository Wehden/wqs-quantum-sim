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
- [ ] Exact-head CI and automatic review complete; owner merge pending.
- [ ] Separately approved live projection/revocation and encrypted restore drills complete before claiming operational completion.

## Execution Notes (tooling, environment, checkpoints)

Use a clean worktree from origin/main; preserve unrelated spec drafts. Use tools/ci/check.sh for full checks and pytest tests/test_cd_release.py for focused checks. The normal CI route remains Standpunkt-first. No Kubernetes mutations, credential creation, mail or CD activation in this implementation PR. Production encryption and backup verification need operator evidence; do not close these items from unit tests.

## Traceability (REQ IDs -> TASK IDs)

S1: 001,003. S2: 002,004. S3: 002,004. S4: 001,003. S5: 003. S6: 001,003 and separately approved live drill. S7: 003,004.
