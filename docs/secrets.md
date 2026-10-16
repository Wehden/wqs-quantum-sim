# Secret management

WQS uses Kubernetes Secrets for runtime credentials. Application CD checks
pre-provisioned references; it never creates, overwrites or rotates them. This
change adds no Vault, external secret controller or runtime dependency.

## Private inventory and deployment permissions

Maintain a private inventory outside Git with each Secret's namespace/name,
required key names, owner, writer, consumers, mount/environment configuration,
rotation procedure, backup dependencies and verification date. Store no values
in the inventory. Use reserved placeholders in shared examples.

The release runner's `required_secrets` configuration must include every
credential required for acceptance. Validate it against deployment volumes,
projected volumes, environment references and supporting services. CD updates
images only; the private inventory remains operator-maintained and is not
automatically inferred from a subset of deployments. A complete inventory is a
bootstrap requirement.

```json
{
  "required_secrets": [
    {"name": "example-auth", "keys": ["users.json"]},
    {"name": "example-admin", "keys": ["session-key", "encryption-key"]},
    {"name": "example-mail", "keys": ["app-password"]}
  ]
}
```

These example names are not a production manifest. References use the existing
release namespace. The list is mandatory and bounded to 16 distinct Secrets;
each entry contains 1–64 distinct key names. Missing, empty or inaccessible keys
block rollout. This check cannot establish that a password is valid at its
upstream service or that a token has suitable permissions.

Grant the deployment identity `get` only for the named Secrets it checks. Do not
add list/watch or mutation permission for preflight. Pods normally consume
projected files and need no direct Secret API access. The invitation updater
requires its existing named registry `get`/`update`; preserve its single-writer
ownership. Its read/replace operation retains resourceVersion and rejects
concurrent updates rather than silently losing them.

Deployment patch permissions still make the runner privileged: someone who can
change a pod can redirect its mounted secrets. Keep the deployment runner
separate from ordinary untrusted PR test runners.

## Consumer behavior and rotation

| Credential | Current consumer behavior | Rotation requirement |
| --- | --- | --- |
| Invitation registry | API reads `QSIM_TOKEN_FILE` for authentication; active entries contain hashes | Preserve existing identities. Measure volume propagation and revocation; do not assume instantaneous updates. |
| Admin session key | Console reads `session-key` when signing/verifying sessions | Expect existing sessions to become invalid. Verify operator access after a planned rotation. |
| Pending-delivery encryption key | Console loads `encryption-key` to encrypt/decrypt retained API-key delivery payloads | Preserve the key while pending ciphertext depends on it; do not replace it as part of image rollout. |
| SMTP password | Notification code reads `QSIM_SMTP_PASSWORD_FILE` for delivery | Coordinate upstream password validity and file propagation; use an approved delivery test. |
| Supporting-service tokens | May be injected as environment variables | Inspect the actual consumer; environment values require a controlled restart to refresh. |

Projected volumes can lag the Secret API. A subPath mount does not provide the
same update behavior as a full Secret volume. Confirm actual mounts and consumer
reload behavior before approving rotation. No background rotation, raw API-key
archive or competing registry reconciliation writer is introduced.

For each planned rotation, capture a non-sensitive rotation identifier, consumer
list, rollback eligibility and an acceptance procedure. Use overlap only where
the upstream service supports it. Never reactivate a compromised credential as
rollback. Do not rotate credentials during CD's preflight-to-acceptance window.

## Backup and recovery

Verify encryption at rest at the control plane, its key custody, encryption of
backup copies and access controls independently. A Kubernetes Secret's base64
representation and a compressed database backup do not establish encryption.
Do not copy encryption configuration or secret values into CI evidence.

A recoverable application backup includes a consistent database snapshot,
results, invitation registry and the matching encryption material required by
pending deliveries. Keep backup decryption keys separately controlled. A cluster
state backup does not automatically include application PVC data.

Run restoration in isolation with synthetic credentials before claiming recovery
coverage. Verify a pending encrypted payload can be recovered without sending
mail, then verify authentication and revocation against the restored registry.
Do not replay an older registry into production: it may resurrect revoked keys.
Loss of a delivery encryption key cannot be repaired by reverting an image.

## Verification boundary

CI tests malformed inventory, missing keys, access failure, timeouts, bounded
retry, diagnostic suppression and ordering around publication/admission/rollout.
They use synthetic inputs and require no cluster credentials. The normal CI
route remains Standpunkt-first with its existing hosted fallback.

Production approval remains separate. Live projection/revocation measurements,
a denied-access drill under the real deployment identity, credential rotation,
backup encryption evidence and an isolated restore exercise are operational
acceptance gates. A green implementation PR does not complete those gates.
