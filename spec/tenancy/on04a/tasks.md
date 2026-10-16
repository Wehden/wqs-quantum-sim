# ON04A tasks and approval gate

Status: implementation and reviews approved on 2026-10-16; see [approval receipt](approval.md). Merge and operational actions remain gated.

## Write lease / budget

Implementation branch from verified current main after reconciling prerequisite merges; do not build from unrelated PR #20 or include existing untracked spec packets accidentally.

Allowed: `qsim/welcome_provider.py`, `qsim/welcome_evidence.py`, `tests/test_welcome_provider.py`, `tests/test_welcome_evidence.py`, `deploy/welcome-provider.example.yaml`, `docs/welcome-provider.md`, and this packet. Runtime <=2 new modules, <=600 non-test Python lines, 3 business routes, zero new dependencies. No edits to auth.py, API/job/worker/controller paths, store migrations, notifications, admin browser logic, release/CD workflows, real inventory or credentials. If these limits cannot support the design, stop and amend the packet.

1. **ON04A-1 Evidence reader:** strict one-instance/org mapping; read-only SQLite; fresh Kubernetes GET with exact-name RBAC contract; approved-request/hash/recipient binding; signed stable revisions/cursors. Unit tests with synthetic data.
2. **ON04A-2 Provider:** standalone authenticated TLS ASGI service, three read-only routes, strict grants/audience, pagination and fail-closed schemas. Reuse existing dependencies; do not import startup code with writes/mail.
3. **ON04A-3 Verification/docs:** central adapter contract fixtures, expiry/race/failure tests, example manifests with placeholders, operational disable procedure, no-secret assertions. Full existing local-first CI and image tests; no cluster credentials in CI.
4. **ON04A-4 Review:** owner reviews PR; automatic reviewer triggers normally, maximum two rounds then check in. Report CI failure and return the reviewed PR with exact-head CI evidence for a concrete ship decision. Implementation/review approval does not authorize merge or auto-merge. Merge, deployment and central registration remain behind explicit ship approval; no live deployment is part of this task.
5. **ON04A-R Rollout gate, separate approval:** reviewed mapping, TLS/service credentials, exact RBAC/network policy and read-only PVC validation. Private dry-run and synthetic read-only acceptance, then explicit central registration. No mail test unless separately authorized.

## Exact approval question

“Approve ON04A's read-only WQS welcome-provider design and implementation/review tasks: three private GET routes, one explicitly mapped legacy app/org, scoped service-token authentication over TLS, fresh registry-backed eligibility and no credential export? Approval permits code, isolated tests and a reviewable PR only; merge requires concrete ship approval. Mapping installation, credential provisioning, deployment, central activation and mail retain separate gates.”

Central ON01–03 may proceed independently with the WQS provider unavailable. UI must not enable WQS actions based on a contract stub or merged code alone.
