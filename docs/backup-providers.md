# Backup provider implementation and installation gates

These providers implement the v2 creation pipeline in source. They are not
installed production hooks and do not enable CD, change admission, or rotate
credentials. The unconfigured creation entry point returns `created:false` and
exits nonzero. Creation never restores a destination.

## Snapshot boundary

`tools.backup.snapshot.Snapshot` requires a descriptor for the parent's already-held
release lock. It checks the lock inode and Linux kernel lock owner; it never
reacquires the parent's flock. `MountedVolume` verifies the executing capture pod,
whole-volume mount, bound PVC UID, immutable image and release revision against
independently supplied source context. Replacement PVCs and source pods fail closed.

The provider acquires a SQLite write transaction, requires a matching nonsmoke
admission hold and zero uncleaned jobs, and rejects in-flight approvals or email
leases. While that transaction blocks application writers, it snapshots SQLite,
checks integrity, and reads every successful job's immutable result. It reads the
named authoritative registry and delivery-key Secrets before and after capture,
requiring identical UIDs, resource versions and bytes. Pending delivery ciphertext
must decrypt with that key and match the stored request and registry hash. No mail
is sent and no key is changed.

It also includes the holding deployment ledger, verified per-release receipts,
previous/current immutable image references, an operator-supplied retention-policy
reference and registry access configuration. The image inventory records recovery
dependencies; the filesystem loader restores this inventory without pulling images.
The source cursor binds the actual inventory and Secret versions. Hash validation
before encryption detects changed staged files. Payload size is bounded by policy.

## Encryption and storage

`AgeEncryption` accepts exactly the configured public recipient set.
It validates canonical Bech32 encoding/checksum, padding, curve points, fingerprints
and duplicate decoded keys. Native X25519 low-order and noncanonical encodings are
rejected. Released YubiKey PIV recipients use compressed P-256 encoding as defined
by [plugin 0.5.1](https://github.com/str4d/age-plugin-yubikey/blob/9329e31dd3d0819d5d7b739aef59e455e10fcfa1/src/p256.rs).
Encryption needs only public recipients; direct restore selects either existing identity.

The age executable must match an explicitly approved SHA-256. A hardware recipient
also requires an explicitly pinned `age-plugin-yubikey` binary reporting version
0.5.1. No plugin is downloaded or selected implicitly. The encryption subprocess
receives no HOME, cloud credentials, agent sockets or private identity variables.
Its owned process group is killed on failure or timeout, including a blocked plugin.
Every configured recipient is passed to encryption of the complete payload.

`S3Storage` uses one bounded `PutObject` with `IfNoneMatch='*'`; it never falls back
to an overwriting write or an unguarded multipart upload. Collision and ambiguous
write failures propagate. Readback uses the exact generated object key and returned
version, when available, with byte-count and full ciphertext hash checks. Object
references in receipts are opaque; bucket names and credentials are not included.
The provider supports at most 5 GiB in a single PUT, with a 512 MiB default policy.
The endpoint must support [conditional S3 writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).
Compatibility must be tested against the selected destination before installation.

`s3_client` requires an explicit TLS endpoint and credential file containing only
`access_key_id` and `secret_access_key`. It disables ambient credential lookup,
proxy inheritance and SDK retries, and uses bounded connection/read timeouts.
Destination credentials must be provisioned through the approved secret-management
path. No such credentials were provisioned by this change.

## Orchestration and limits

`capture_new_set(snapshot, encryption, storage, private_work_dir, requested_id)` shares a maximum
540-second budget, enforced by a process alarm inside the unchanged 600-second
parent limit. It runs in the main thread of a dedicated capture process. It stages
files inside a private temporary directory and cleans them on normal exceptions
and completion. A process crash may leave private plaintext staging files; approved
working-volume encryption and crash cleanup remain installation gates. Failed
uploads may leave an opaque object; the adapter never deletes retained storage.

The source provider requires access to both current application data and the
parent's runner-state filesystem in the same verified capture environment. The
current runner deployment does not supply that arrangement. Installation must
resolve volume placement or use a separately reviewed authenticated capture
transport. This PR does not mount volumes, change pod placement, add RBAC or wire
the backup hook into production. It must not be installed by copying a configuration
example and declaring bootstrap verified.

## Contract and configuration

The concrete snapshot emits the pinned v2 manifest, including empty result and
receipt collections. Construct `Snapshot(..., cd_enabled=<actual boolean config>)`;
the value records configuration, while this provider's held-lock/transaction checks
establish source consistency. The configured hook passes its `WQS_BACKUP_SET_ID`
as `requested_id`. The coordinator binds that ID before capture and returns exactly
`{"evidence_type":"creation_result","created":true,"backup_id":"<requested_id>"}`
only after full archive/native-format validation, encryption and storage readback.
No independent recovery approval is required by v2.

Explicit direct restore accepts either existing identity, decrypts, validates and
loads its configured filesystem destination. It is a separate operation; storage
creation does not invoke it. Live source placement, storage configuration and
restore destinations still need operational configuration. No credentials or
production restore are provisioned by this source change.

## Validation

CI installs the pinned upstream age 1.2.1 Linux amd64 archive after SHA-256 checking.
Tests exercise real encryption and full payload decryption with each of two isolated
synthetic native identities. These identities are temporary test material, not
operational recipients or custody evidence. Hardware public-key parsing is tested;
no hardware recovery is claimed. S3 tests use SDK stubs and storage doubles, without
network writes. SQLite fixtures exercise snapshot integrity, mismatched delivery
keys, in-flight operations, changed Secrets, lock ownership and current-volume
identity. The runner-image smoke checks the installed encryption and Python tools.

Source workload and storage IDs use `k8s-pod:<sha256>` and `k8s-pvc:<sha256>`.
The digest covers the canonical JSON array `[namespace, name, uid]` using the
contract canonicalizer. Configure expected IDs with `source_identity()`; the
verifier recomputes them from the current Kubernetes objects. This binds the
complete identity while respecting the contract ID character and length limits.
