# Release hooks and remaining backup dependencies

## Application acceptance

`python3 -m tools.cd.acceptance --config /private/release.json` implements the
existing CD acceptance contract. It uses `WQS_RELEASE_SHA` and the private config's
`acceptance` fields: `api_url`, `admin_health_url`, and `token_file`. The token must
belong to `smoke_owner`; the hook checks `/api/me` before submitting jobs. No token
is issued or rotated by this implementation.

Install the private `verify` wrapper only after the runner image containing NumPy
and application/controller images containing `qsim.release_probe` are available:

```sh
#!/usr/bin/env bash
set -euo pipefail
exec python3 -m tools.cd.acceptance --config /private/release.json
```

The CD workflow invokes the wrapper from its exact-SHA checkout. It requires the
parent deployment ledger to match that SHA and be in `deploying` state. The
database must already hold admission for that release and permit the configured
smoke identity. The hook never changes admission, the parent ledger or credentials.

The hook verifies all four role deployments before and after the simulations:
observed generation, single updated/ready/available replica, immutable image
reference, and one ready matching pod with a runtime image ID. Pod image references
must match the expected immutable images; runtime image IDs are not compared to
OCI index digests because runtimes may report the selected platform manifest.
The controller's worker-image setting must match the release ledger too.

It checks API readiness and admin health, then submits eight tiny deterministic
cases: Clifft Stim and QASM2 probabilities; Google qsim sampling and probabilities
for QASM2 and QASM3; Aer sampling for both formats. Aer cases include reset and
classical feed-forward. Probability requests omit shots and seed. Each result's
engine, operation and numerical summary must match the downloaded NPZ arrays.
Artifacts are bounded and loaded with NumPy pickle support disabled. HTTP
redirects are rejected, so bearer credentials are not forwarded to another URL.

Local read-only probes require controller heartbeat and complete observer queues
no older than 180 seconds. The final probe runs in the controller image, resolves
each owned dispatch request to its native SkyPilot job ID, and checks that exact
job in its native queue, including finished jobs. A callback alone cannot pass.
Controller progress and completed dispatcher cleanup are required. A missing or
expired native queue/request fails acceptance; the hook does not infer success.

The hook has a 540-second working budget inside the parent's unchanged 600-second
process timeout. On timeout or failure it leaves admission held and never cancels,
deletes or resubmits jobs automatically. Private `acceptance-<sha>.json` evidence
records submission intentions, idempotency nonce and returned job IDs so an
operator can investigate an uncertain submission. It does not acquire the
parent's held lock. Only the final sanitized acceptance JSON goes to stdout;
raw API/kubectl errors and secret values are suppressed.

Installation and live acceptance remain pending. The deployed runner stays at
zero replicas and `WQS_CD_ENABLED=false`. The runtime-overlay/protocol bootstrap,
dedicated smoke identity, complete backup and recovery gates remain independent.

## Backup integration checkpoint, 2026-10-19

LaminarAuth publishes the [v1 recovery contract](https://github.com/Wehden/LaminarAuth/blob/2bc4c76761351c1205fe92b75640075fe721189a/docs/backup-recovery-contract.md)
and its JSON schema. This supersedes the earlier planning-only dependency.
WQS adapter implementation must pin the reviewed contract and validate both its
structural schema and normative policy. Publication does not establish key custody,
an operational trust authority or current recovery acceptance.

The adapter must capture drained application data and the contemporaneous holding
ledger, registry, matching delivery-encryption material, verified release receipts
and recoverable image dependencies. It must not reacquire the parent's held lock.
Capture/readback and an independent capture checkpoint do not permit final success.
Only an exact-set full restore and independent owner acceptance may produce
`{"verified":true,"backup_id":"<set_id>"}`.

Private decryption identities remain outside the runner. If accepted evidence
cannot arrive within the existing 600-second invocation, backup fails closed and
the deployment remains held. Existing rerun rejection remains intact; this change
does not introduce a manual resume protocol. Backup adapter implementation and
current-data recovery acceptance remain separate pending work.
