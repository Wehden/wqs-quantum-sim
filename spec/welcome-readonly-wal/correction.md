# Live read-only welcome SQLite correction

Status: proposed bounded corrective implementation; no production deployment from this packet alone. Current human instruction authorizes addressing WQS activation and verifying existing tokens. Existing token validation is a separate read-only task.

## Context and reproduced failure

The reviewed welcome provider mounts the existing database read-only and opens normal SQLite WAL snapshots using mode=ro/query_only. Live storage has WAL header bytes2/2 but no -wal/-shm sidecars when short-lived owner connections close. SQLite3.40.1 then fails SELECT with SQLITE_CANTOPEN14 on the read-only mount. Actual UID/path/PVC checks and isolated immutable-image read-only bind reproduction distinguish this from missing database or permission errors.

A synthetic experiment showed one existing writer-side query-only connection, kept open after a schema SELECT, retains sidecars. Independent commits and WAL TRUNCATE checkpoints remain possible and become visible to a fresh reader on an actual read-only Docker mount. Closing the anchor removes sidecars normally. This experiment did not access production data.

## Corrective requirements

- The existing native admin process must retain one query-only SQLite connection for its lifetime after normal schema initialization. It must release that connection on normal and exceptional shutdown.
- The anchor must hold no active transaction or cursor across requests; it must not alter journal mode, keys, identities, data or token hashing. It uses the native admin's existing writable PVC solely for normal SQLite sidecar setup.
- The welcome provider remains unchanged with a true read-only PVC, mode=ro and query_only. If native owner shutdown removes sidecars, welcome fails closed until the owner restarts; no stale snapshot or immutable shortcut.
- Existing native notification worker count remains exactly one. Existing user/API keys and envelopes remain byte-preserved; no issuance, revocation or rotation during acceptance.

## Design and file lease

Add a small context manager in qsim/store.py: open settings.DATA/jobs.sqlite3 with the existing owner permissions, enable query_only, execute and exhaust a schema SELECT to establish WAL sidecars, yield without an active transaction, close in finally. Do not call store.connect because its transaction context and PRAGMA journal_mode behavior are unnecessary for this lifetime helper.

In qsim/adminweb.py lifespan, after store.init and signer/cipher checks, enter that context for the entire service lifespan. Put notification task cancellation in finally so both task and anchor clean up when the app fails or stops. No new service, broker, endpoint, dependency or background thread.

Allowed runtime files: qsim/store.py and qsim/adminweb.py. Tests: tests/test_welcome_wal_lifecycle.py and existing owner/provider tests. Documentation: docs/welcome-provider.md and this corrective packet. No physics, API/job/worker/controller, token, release-control or CD-gate edits.

## Verification and review

1. Regression reproduces no-sidecar read-only failure, then proves the helper retains sidecars during independent commits/checkpoints and releases its connection on success and exception. Use actual read-only mount/process behavior for deployment acceptance; do not count writable-path mode=ro as proof.
2. Native lifespan startup/shutdown tests prove one existing worker and anchor cleanup on exception; key material remains mocked/captured in isolated tests.
3. Run focused management/legacy approval/welcome tests and existing full owner suite where practical; inspect token/notification changes for regressions. Independent correctness and security reviews before push/deploy, with existing two automatic review-round limit.
4. Reviewed source-only PR targets fresh main and preserves concurrent physics work. Owner coordination must determine permitted image promotion rather than bypass release bootstrap. The deployment candidate must preserve current unrelated runtime behavior, existing token registry, DB and single mail worker.
5. After approved native-owner deployment, resume the unchanged reviewed welcome provider. Verify actual read-only PVC/WAL readiness, live TLS/SAN, missing/wrong bearer denials, wrong scope, exact capabilities and three existing eligible recipients before central binding activation. No mail or new key is needed.

## Rollback

Disable central welcome binding first if activated, then scale only welcome provider to0. Keep source and existing-user evidence. Native owner rollback requires reconciliation if management writes have ever been enabled; this packet does not enable management commands. Never restore or delete SQLite, keys, registry or request history to reverse the change. Release admission/bootstrap state follows the owner path and is not changed by this corrective patch.

## Implementation evidence

The bounded implementation adds store.keep_wal_available and wraps native-admin lifespan with it, including notification-task cleanup in finally. The helper exhausts and closes both setup cursors and retains no active transaction.

Focused verification passed86 tests: WAL lifecycle, management provider, welcome provider/evidence and legacy approvals. Six new lifecycle cases include normal/exception shutdown with notification enabled/disabled, query-only write refusal, and independent writer commit plus TRUNCATE checkpoint visibility. On hosts enforcing a directory write refusal SQLite may return SQLITE_READONLY_DIRECTORY rather than the production read-only mount's SQLITE_CANTOPEN; both are the same missing-sidecar precondition, and the actual mount was independently tested.

A separate network-disabled container using the immutable published welcome image and a genuine read-only bind successfully read a synthetic database while the actual new helper retained sidecars, then observed an independent committed insert after a successful TRUNCATE checkpoint. Closing the helper removed the sidecars. No production data was used. Deployment, merge and source promotion remain pending final review and the owner-approved path.
