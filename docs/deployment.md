# Deployment

This repository supplies application images and example SkyPilot configuration.
It does not contain a live cluster inventory or an unattended deployment installer.

Build `api`, `controller`, and `worker` targets in `deploy/Dockerfile` and push them
to your own registry. Pin the deployed images by digest. The controller image
includes the SkyPilot client. The worker includes both simulation engines.

Run one API and one controller with a shared persistent data directory. SQLite
and the controller's file lock require storage with compatible locking semantics.
The API serves port 8080. The controller runs `python -m qsim.controller`.

| Setting | Purpose |
| --- | --- |
| `QSIM_DATA` | Persistent job database and result directory (default `var`) |
| `QSIM_TOKEN_FILE` | Private invitation hash registry (default `var/users.json`) |
| `QSIM_CALLBACK_URL` | API origin reachable by workers; loopback default is development-only |
| `QSIM_WORKER_IMAGE` | Published immutable worker image; default `worker:local` is not a deployment image |
| `QSIM_SKY_INFRA` | SkyPilot infrastructure selection, default `k8s`; supply your context |
| `SKYPILOT_CONFIG` | Path to your private SkyPilot configuration |
| `QSIM_TRUSTED_PROXY_CIDRS` | Explicit trusted proxy ranges, when using forwarded client addresses |

Copy `deploy/sky-config.yaml` into ignored `local/`, then configure your SkyPilot
API server, Kubernetes context, and workload namespace there. Set `SKYPILOT_CONFIG`
to that copy. Confirm scheduling and image access before accepting submissions.
No arbitrary node placements are encoded in the application.

Publish only the API and documentation through your authenticated TLS ingress.
Block `/internal/` at the public boundary: it carries worker callbacks and must
be reachable only through the worker network. Never route the separate operator
console to the public ingress. Scope service-account permissions and network
policies to the application's needs.

## Optional operator console and mail

The console (`qsim.adminweb:app`) trusts its protected operator network. A Host
allowlist is not an authentication boundary: restrict access at the network or
identity-aware proxy. Set `QSIM_ADMIN_ORIGINS` to exact comma-separated origins.
Mount independent `session-key` and Fernet `encryption-key` files under
`QSIM_ADMIN_SECRET_DIR`. Never regenerate the encryption key with pending deliveries.
The Kubernetes invitation updater currently expects namespace `qsim` and Secret
`qsim-users`; provision those with least-privilege RBAC if using this console.

Configure `QSIM_REVIEW_EMAIL`, `QSIM_SMTP_SENDER`, `QSIM_SMTP_HOST`,
`QSIM_SMTP_PORT` (default 587), `QSIM_SMTP_USERNAME`, and
`QSIM_SMTP_PASSWORD_FILE`. Delivery uses STARTTLS. `QSIM_PUBLIC_URL`,
`QSIM_CONSOLE_URL`, and `QSIM_MAIL_DOMAIN` set email links and message identity.
The static developer and agent guides contain reserved example base URLs;
replace them in your private deployment build. No operator credentials belong
in frontend assets.

## Backups and upgrades

Back up the SQLite database consistently together with results and private
invitation/encryption material. Keep backups outside Git. `scripts/backup.py`
is an optional helper for deployments following its documented resource names.
Test restoration independently. Roll back image digests while preserving data.
Workers are reused only when their image matches the configured digest; old
workers complete and expire through normal SkyPilot lifecycle handling.

The console navigation separates Jobs (`#jobs`) from Approvals (`#approvals`).
Jobs opens by default. Existing `#request-<id>` notification links open Approvals
and focus the request directly. Only the active page polls its data.

## Jobs by hostname

The Jobs page groups active simulation jobs by the actual Kubernetes node name.
The query covers every active job, independent of the paginated job table.
Queued or unassigned work is counted separately; missing, ambiguous, or stale
placement is shown as unknown. Failed/completed pods are excluded from current
placement. Idle workers count as zero jobs; this is not a lifetime job history.

Run the observer with the `qsim-observer` service account, a projected service-account
token, and read-only pod-list permission in the worker namespace. See
`deploy/observer-rbac.example.yaml`; adapt namespace names to your deployment.
Set `QSIM_WORKER_NAMESPACE` if different from `qsim-jobs`. Allow the observer to
reach the Kubernetes API through its egress policy. Do not grant it Secret,
node, scheduling, or pod mutation access. The public API requires no extra RBAC.
If this permission is absent, existing SkyPilot metrics remain available while
the console explicitly reports placement data as unavailable.
