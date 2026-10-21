# Configured cross-node backup creation

`python -m tools.backup.live --config /private/backup/config.json` connects the
reviewed v2 creation path to an API PVC on another Kubernetes node. It is a
creation hook, never a restore command. Configuration examples are intentionally
incomplete: supply actual object names, public recipients, tool hashes, storage
configuration and the existing image-retention reference privately.

## Release parent and source identity

Install the hook as an executable script that uses `exec`, so Python remains the
release parent's direct child:

```sh
#!/bin/sh
set -eu
umask 077
mkdir -p /backup-work/private
exec python3 -m tools.backup.live --config /private/backup/config.json
```

The parent supplies `WQS_BACKUP_SET_ID`. The child opens the parent's existing
lock file without acquiring another flock, verifies its inode and kernel lock
owner, and checks the holding ledger. It does not simulate that lock on the API
node. The normal coordinator then creates a snapshot, encrypts, uploads and
verifies readback. A successful stdout response contains only `evidence_type`,
`created`, and the requested `backup_id`. Failures return one sanitized failure
result and a nonzero exit; no private diagnostics are printed.

The source resolver requires one ready API pod owned by the selected deployment's
ReplicaSet, a complete PVC mount, a bound named PVC, matching immutable deployment
and pod image references, and a matching runtime container image ID. Pod and
Deployment template `wqsim-release-sha` annotations must agree. The current
annotation is captured as provenance and rechecked across capture; it advances
with normal CD releases. Do not invent a Git revision for an unknown image.

The fixed standard-library exporter is sent through Kubernetes' authenticated
TLS exec channel to that specific container. It uses the actual API settings'
data path and verifies the matching nonsmoke hold, zero uncleaned jobs, and no
in-flight approval/delivery leases under a SQLite IMMEDIATE transaction. It
copies SQLite and every completed result without migrating the source schema.
The parent separately reads only the configured named registry/delivery Secrets,
binds their UID/resourceVersion/bytes, and combines these with its real ledger,
receipts, image inventory and registry access. Native validation verifies the
pending-delivery keys and results against the actual database.

Source checks before encryption and after storage include the real parent lock,
unchanged ledger, pod/PVC/runtime identity, Secret versions and bytes, logical
SQLite contents and completed-result bytes. Partial archives, changed sources,
transport failure or failed native validation cannot produce creation success.
Historical v1 source interfaces remain available separately.

## Deadlines and plaintext

The exporter announces its monotonic clock before accepting a request. After
startup the parent computes its remaining budget and sends a conservative
absolute deadline on that remote clock. There is no assumption that the nodes'
wall clocks match or that Kubernetes exec starts promptly. Before receiving the
request the exporter holds no database lock and has a 60-second startup timeout.

The exporter owns its timer and handles termination. Nonblocking, deadline-bound
stdout writes prevent TAR finalization from hanging after timeout or disconnect.
Private temporary files and the SQLite transaction are cleaned up on exit. If
transport fails, the parent fails closed and retains admission; closing kubectl
alone is not claimed as evidence that the remote process has already exited. The
remote deadline still bounds its lifetime. Force-kill or node loss may leave
scratch files until the pod's ephemeral storage is discarded; no durable data
volume is used for exporter scratch.

Mount a memory-backed `emptyDir` at `/backup-work` in the runner. Its root may
be root-owned and group-accessible under the pod's `fsGroup`; do not chmod that
mount root. The hook wrapper above creates a runner-owned `0700` child on every
invocation, including after a pod restart. Configure `work_dir` as
`/backup-work/private`. Existing insecure directories or symlinks remain rejected
by the coordinator; `mkdir -p` does not relax those checks. Configure the mount's size/resources for the bounded archive and encryption/readback
copies. The current backend accepts at most 512 MiB plaintext TAR and 512 MiB
expanded members per NPZ. A failed or interrupted capture may leave encrypted
objects, but never promotes based on a partial object or old receipt.

## Tools, credentials and storage

The runner image builds age-plugin-yubikey 0.5.1 from its pinned upstream commit
and locked dependencies. It installs only the public-recipient plugin and its
runtime library. No device, PIN, passphrase or private recovery identity belongs
in this runner. Record hashes of the installed age and plugin binaries in private
configuration; a mismatched executable fails before encryption.

Project configuration and existing public recipients through a Secret volume.
Kubernetes atomic-version symlinks are resolved once for explicitly named config,
recipient and registry-access inputs. Registry and delivery Secrets remain named
API reads; their values are never added to logs or public artifacts.

The object-store account needs only conditional object creation and exact
object/version reads in the dedicated private bucket, plus any required bucket
location/versioning reads. Do not give it delete or bucket-administration rights.
Bucket versioning must be enabled: the hook rejects missing, empty or `null`
upload version IDs before readback, so a suspended/unversioned bucket cannot
produce a successful creation result.
Use a trusted TLS endpoint and verify an actual conditional PUT collision and
exact version readback before installation. A compatibility probe is not an
application backup. The existing MinIO administrative account is used only during
provisioning, never in the runner configuration.

After successful capture, the hook writes a private
`<state_dir>/backup-locators/<backup_id>.json` containing endpoint, bucket, exact
key/version, ciphertext size and SHA-256. It fsyncs the file and containing
directories before reporting success. Existing IDs cannot be overwritten.
Failure to persist this locator fails creation, even if upload succeeded.
These locators are operational records; they do not constitute restore evidence.

## Installation and acceptance

Use `deploy/backup-hook.example.json` as the configuration shape and
`deploy/backup-reader-rbac.example.yaml` for the additional identity reads. The
existing runner retains its namespace-scoped pod discovery/exec and named Secret
reads. Add only ReplicaSet GET and GET on the actual source PVC; do not grant
Secret list, wildcard Secret reads, or cluster administration.

Update only the reviewed runner image, private backup projections, memory-backed
scratch mount, and needed identity-read Role/Binding. Preserve the registration
identity, deployment-group restrictions, existing credentials and Docker state.
Verify public-recipient encryption without hardware access inside the image.

Before any full release promotion, perform a real capture of the current live
API database and completed results, persist and read back its exact storage
locator, and exercise failure behavior while admission stays held. Preserve the
current source image and recovery point. Keep `bootstrap_verified=false` until
all remaining runtime parity and application acceptance checks are satisfied.
Source tests and a storage-only probe do not clear that gate. Restore remains an
explicit separate action; this hook never overwrites a running service.
