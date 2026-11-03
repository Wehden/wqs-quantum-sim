# Secret-management verification

Status: implementation and existing circuit CD/application acceptance verified; no production restore or credential rotation performed. Cluster-wide datastore encryption is not established by these application checks.

## Implementation evidence

- PR #22 head `83461cf19be17ba70010bf091f203704bf1ad7ce` passed CI: Standpunkt tests, GitHub-hosted image checks, hosted test fallback skipped. Automatic code review completed with no findings.
- Owner merged PR #22 as `1375c078dee7b658d42f94aa0ce3fd5f2a3c5152` on 2026-10-17. Post-merge main CI passed; CD was skipped.
- Full local CI passed: 364 Python tests, 3 console tests and dependency consistency. Focused CD tests: 49 passed.
- No runtime dependencies, public APIs, Secret names or application authentication behavior changed.

## Authorized operational checks (2026-10-17)

The owner authorized operational acceptance and Laminar coordination, explicitly excluding credential rotation. Private evidence remains outside this repository; the table contains no production credentials or topology.

| Check | Result and limit |
| --- | --- |
| Required Secret preflight | Merged implementation passed against the existing production references using the operator kubeconfig. This is not proof for a deployment-runner identity. |
| Private release configuration | Required-secret inventory fragment prepared. Repository runner inventory has only the ordinary test runner; no deployment runner or configured release-config path. The fragment has not been installed as a functioning CD configuration. |
| Existing workload permissions | Operator authorization checks confirm API/default and observer identities cannot directly read the invitation registry; the invitation updater can get/update its named registry but cannot list Secrets or get the mail Secret. |
| Isolated in-cluster permissions | Synthetic probe with a mounted service-account credential received 200 for its allowed Secret and 403 for another. Merged preflight also accepted the allowed reference and failed closed on the denied reference under reader impersonation. |
| Projected registry update | Synthetic identity changed from active to inactive without changing its key. Mounted-file observation followed 46.65 seconds after the update request started. One sample confirms eventual propagation, not a maximum or instantaneous-revocation guarantee. |
| Synthetic encrypted restore | Online SQLite snapshot, encrypted archive round trip, matching-key pending delivery recovery and artifact preservation passed. Actual auth code accepted the restored active identity and denied it after synthetic revocation. Wrong encryption key and tampered archive were rejected. No mail sent. |
| Historical application backup | Existing older archive's isolated SQLite integrity check passed. It has no approval table or registry/encryption-key files and no archive-level encryption. Underlying filesystem encryption remains unverified. It is not a complete current recovery set. |

The synthetic restore used temporary local fixtures, removed after verification. The cluster drill used a dedicated owned namespace with no production volumes, non-root execution and API-only network egress. Deletion of the owned namespace was verified and recorded in the private receipt. Production keys, deployments and job data were untouched.

## Remaining acceptance gates

- The operator subsequently confirmed control-plane Secret encryption is disabled. LaminarAuth now owns key custody and backup coordination, including encrypted copies and tested recovery. No encryption changes or credential rotation were performed by WQS.
- Install the separately approved trusted deployment runner and private release configuration before testing the real deployment identity. A prepared reference fragment does not satisfy this gate.
- Demonstrate restoration of a current complete encrypted recovery set, including application data, registry and matching delivery-encryption material. Synthetic and historical drills do not substitute for that evidence.
- Agree an operational revocation target and verify it against the actual consumer path if a bounded guarantee is required. The measured projection delay is not an SLA.
- Credential rotation remains deferred by owner. No credential is rotated as part of acceptance.

Do not enable CD, claim production deployment, or mark secret-management acceptance complete from the merged PR or these partial results.

## Deployment runner preparation

The runner package and private configuration template are now prepared separately
from activation. See [deployment runner bootstrap](../../docs/deployment-runner.md).
The public repository requires an organization runner group restricted to the CD
workflow on main. Current operator access cannot administer that group. The
template remains at zero replicas; the private release config remains unverified
and uninstalled. Image/tool tests and configuration tests do not close the real
deployment-identity or production recovery gates.

## Historical reconciliation, 2026-10-19

This update supersedes the earlier runner-preparation status above. PR #24 is merged at `fb47a0800f80aa96f4e8287282e65d646fda0edc`. The restricted deployment runner is registered, with infrastructure and named-resource RBAC checks completed. It remains at zero replicas and `WQS_CD_ENABLED=false`. Private configuration and hook installation, actual runner execution, and production recovery acceptance are still pending.

Application acceptance source passed the full local suite (390 Python tests and three console tests) and the runner-image smoke check. These are source checks, not live acceptance. LaminarAuth has published `docs/backup-recovery-contract.md` and its v1 schema; their publication does not establish custody, trust authority or accepted recovery. Credential rotation remains deferred.

## Current operational reconciliation, 2026-11-02

Main `8d8b2d0486e0feba6b0cc7514836f67256d83cb5` passed
[CI 41589604153](https://github.com/Wehden/wqs-quantum-sim/actions/runs/41589604153).
The configured restricted deployment runner, private release configuration,
named Secret preflights and actual backup/verification hooks are installed and
exercised. The runner's merged permission policy preserved the locator
directory at `0700` through replacement. These live observations supersede
the historical uninstalled/stopped/disabled checkpoints above.

[CD 41590212314](https://github.com/Wehden/wqs-quantum-sim/actions/runs/41590212314)
created a new encrypted backup with exact-version readback and a durable private
locator before image mutation. Placement saturation delayed acceptance past its
normal deadline. The operator retained failed evidence, retired only 13 proven
idle obsolete acceptance clusters through SkyPilot, waited for cleanup, checked
the stored backup version/hash/size and unchanged role images, then invoked the
existing verifier under the release lock. All eight circuit cases, native
completion, controller/observer and role evidence, and cleanup passed. The
ledger is verified, pending is zero and admission reopened. The same-SHA CD
retry completed green and published the existing verified receipt; it does not claim a new backup
or another acceptance run. Private before/after and retirement evidence remain
outside the repository. See [the runbook](../../docs/cd-recovery.md).

This closes installed capture and existing circuit application acceptance, not
physics runtime acceptance or a production restore. No existing credential or
recovery identity was changed and rotation remains deferred. Datastore
encryption-at-rest status was not reverified here; application backup encryption
is distinct evidence. The approved v2 creation/direct-load policy remains in
force without reintroducing its superseded independent custody/checkpoint gates.
