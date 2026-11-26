# WQS specification status

This index records the repository's current scope and links to the evidence
that closes each release checkpoint.

| Specification | Status |
| --- | --- |
| [QASM support](qasm-support/README.md) and [Aer classical conditions](qasm-support/classical-conditions/design.md) | The documented CPU subsets are implemented and deployed. [Exact release, cases and backout](qasm-support/release.md) |
| [CPU physics engines](physics-engines/README.md) | Lindblad, scqubits and JoSIM are in the verified 21-case release. [Native acceptance](physics-engines/validation.md#verified-cpu-release-2026-11-25) |
| [GPU discovery](physics-engines/gpu-discovery/context.md) | The A100 trial and internal RTX 3060 profile are covered in the [results](physics-engines/gpu-discovery/results.md). The [deployment plan](physics-engines/gpu-discovery/deployment-plan.md) defines the exact-image canary before public routing. |
| [Secret management and recovery](secrets/tasks.md) | The live release has fresh encrypted backup/readback evidence. Credential rotation and a real production restore are deferred. Cluster datastore encryption remains a separate infrastructure check. |
| [ON04A welcome](tenancy/on04a/verification.md), [read-only WAL](welcome-readonly-wal/verification.md) and [Authconsole parity](authconsole-parity/plan.md) | The configured bootstrap providers passed live authenticated reads. Command lifecycle tests use synthetic identities. The parent tenancy/token-authority migration remains deferred. |

The [CI runbook](../docs/ci.md) records the actual runner paths and release
smoke requirements. Use the [release procedure](../docs/releases.md) and
[recovery record](../docs/cd-recovery.md) for deployment evidence.
