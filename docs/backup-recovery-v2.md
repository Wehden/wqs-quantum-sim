# Backup creation and direct recovery v2

V2 replaces the former independent checkpoint, authority, per-route proof,
custody-mapping approval, staging restore, and owner-acceptance requirements.
A backup is encrypted to the existing public recipients. Either the existing
YubiKey identity or the existing fallback identity may decrypt it. No new
recovery credential is created or required.

Creation and restoration are different operations:

- `tools.backup.create.create(...)` invokes actual capture, encryption, storage,
  and byte-exact readback through the configured providers. It returns exactly
  `{"evidence_type":"creation_result","created":true,"backup_id":"set-id"}`.
- The CD parent supplies `WQS_BACKUP_SET_ID` to its configured backup command.
  Only an exact successful creation result for that invocation allows image
  application. Extra/duplicate fields, wrong types/IDs, restore results, and
  nonzero exits fail. This result does not claim recovery was tested.
- `python -m tools.backup.restore` explicitly decrypts the actual backup,
  validates its archive and native WQS contents, and directly loads configured
  filesystem destinations. Only completed loading returns
  `{"evidence_type":"final_result","verified":true,"backup_id":"set-id"}`.
  The manifest supplies the ID; `--set-id` is an optional mismatch check.

There is no implicit restore during creation or CD. The default creation module
has no production provider configuration and returns `created:false` with a
nonzero exit. Installing a real capture coordinator remains operational work.
The concrete snapshot/coordinator emits v2 manifests and returns creation results
bound to the requested ID. It requires explicit CD configuration provenance and
configured source, encryption and storage providers; see [provider setup](backup-providers.md).

## Explicit direct restore

Provide the encrypted file, an existing age identity file, the absolute age
executable and its SHA-256, and a JSON destination configuration:

```sh
python -m tools.backup.restore --backup /private/backup.age \
  --identity /private/existing-age-identity \
  --age /usr/local/bin/age --age-sha256 "$AGE_SHA256" \
  --destinations /private/restore-destinations.json
```

Age handles the selected existing identity, including an installed YubiKey plugin
when that identity requires it. The test suite exercises actual age encryption
and either of two disposable software identities; it does not claim hardware
YubiKey or production recovery acceptance.

Destination JSON contains exactly `data_dir`, `state_dir`, `registry_file`,
`delivery_key_file`, `image_inventory_file`, and `registry_access_file`, all
absolute paths. These are the actual final destinations, not a staging service.
Stop their writers and checkpoint SQLite first; existing SQLite sidecars cause
refusal. The command neither stops services nor starts them after restoration.

The archive is decrypted into bounded memory, not extracted into a staging tree.
Every inventoried file is checked before writes. The native loader validates
SQLite integrity, completed-job result coverage, invitation registry and pending
key consistency, runner ledger and receipts, image dependencies, and registry
access JSON. It directly writes and fsyncs the destination files, reads them back,
and reopens the actual destination SQLite database. Archived source PVC and
release information are provenance; the old source need not still exist.

A direct load is not atomic across files. Failure can leave a partial destination;
it always returns nonzero and `verified:false`. Do not start the application after
a failed load. Existing unrelated files are not deleted. The backed-up release
hold and ledger are restored as data; the command does not reopen admission.

This backend is filesystem-native. It does not mutate Kubernetes Secrets, make
read-only Secret mounts writable, restart deployments, or prove Kubernetes
service activation. A destination using Kubernetes-managed auth files needs an
explicit native integration before a cluster restore can be claimed.

Failures use `ineligible` for invalid content/key or failed loading, `unavailable`
for missing input/tools, and `timeout` for bounded decryption failure. Diagnostics
never print decrypted data or credentials. V1 schema and fixtures remain intact
as historical records; they do not authorize v2 creation or restoration.

No live deployment, restore, credential rotation, bootstrap flag, or CD switch
change is part of this source implementation.
