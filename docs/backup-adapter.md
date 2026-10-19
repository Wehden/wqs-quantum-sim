# WQS backup capture adapter

Status: capture-only source implementation. Concrete provider source is documented
in [backup providers](backup-providers.md); no production providers or hook are installed. CD remains disabled; credential rotation is excluded.

The adapter pins LaminarAuth's reviewed PR #29 merge,
`6f4674e2ae02a6e9fe8ced33117ac5a17b77fc78`. Its
[contract](https://github.com/Wehden/LaminarAuth/blob/6f4674e2ae02a6e9fe8ced33117ac5a17b77fc78/docs/backup-recovery-contract.md)
defines the normative policy in addition to JSON Schema. The vendored schema and
portable synthetic fixtures retain upstream MIT attribution. Metadata parsing,
canonicalization and structural checks are adapted from its conformance reference;
no synthetic trust verifier is imported as production code.

## Implemented boundary

`tools.backup.capture.capture` accepts an owner-prepared coherent snapshot directory,
a complete WQS manifest, independently supplied expected context, a private working
directory and explicit providers. It checks required dependency roles, inventory
coverage, the held release declarations, recipient fingerprints and canonical
metadata. It packages only declared files plus the canonical manifest, checking
actual file sizes and hashes while reading. File opens reject symbolic links in
all relative components, hard-linked files and special files. The source directory
itself must be an operator-controlled directory with trusted ancestors.

Providers validate approved recipient identities and the coherent source boundary,
encrypt the complete archive, create a new storage object, and read it back. The
adapter compares the readback bytes' SHA-256 and length with the encrypted output,
rechecks the boundary and emits a schema-valid `capture_readback` receipt. Temporary
plaintext is created inside a private temporary directory and removed on ordinary
success or exceptions. This is filesystem deletion, not secure erasure; the private
working volume needs an approved encryption and crash-cleanup policy before use.

Concrete providers are implemented separately, but none are installed or selected by default. The synthetic
tests do not perform encryption, upload data, authenticate custody or establish
snapshot consistency. Provider callbacks are trusted code, not a security sandbox.

## Remaining production integration

The coordinator must hold the parent's existing release lock and obtain a drained,
coherent snapshot of current PVC database/results, invitation registry, matching
delivery key, runner ledger, verified release receipts and retained image/registry
dependencies. It supplies a validated cursor and independently expected context.
The child must not reacquire the parent's flock. Boolean manifest claims alone do
not establish that boundary. The boundary provider must validate it before and
after capture, including actual database admission and contemporaneous ledger.

Recipient validation must check canonical encoding/checksum, decoded-key identity,
plugin versions, exact approved membership and independent custody. The encryption
provider must cover every approved recipient and the complete payload. The storage
provider must create without overwrite, use opaque references and retrieve from
the configured destination. Provider processes must be bounded within the parent's
600-second budget. These providers require review before any current-data capture.

LaminarAuth confirmed that no authenticated checkpoint/owner-acceptance verifier
exists yet. A successful capture must never become `verified=true`. Running
`python -m tools.backup.capture` therefore always emits the contract's
`final_result` with `verified=false`, `failure=unavailable`, and exits nonzero.
This entry point deliberately does not attempt capture and is not installed in CD.

An independent checkpoint, exact-set full restore with every approved route proven,
and independent owner acceptance remain required for promotion. Restore must keep
admission/CD held and mail/controllers disabled. This change does not implement
restore, ledger-update anchoring, manual resume or credential rotation.
