# Physics simulation engines

Requirements, design and G1 decisions are approved. Tranche A is merged in
PR #80 at `87304d249005ad65db6a256bf970e04ede1c1288`; exact-main CI passed with
Standpunkt tests and hosted image/runner checks. All three CPU backends now
share the existing API, queue, SkyPilot dispatcher, isolated packaged worker,
callback/result validation and encrypted recovery path.

The owner approved the [integrated delivery tranches](tasks.md#integrated-delivery-tranches-approved-2026-11-08).
Tranche B combines release qualification, tested public documentation and the
combined audit in one PR. Production physics is not certified by a source merge
or local image test. Runtime `/api/capabilities` remains the availability authority.

| Gate | Status and evidence |
| --- | --- |
| Requirements/design/G1 | Approved; [requirements](requirements.md), [design](design.md), [decisions](decisions.md) |
| G2 CPU integration | Tranche A merged. All Lindblad families, four scqubits classes/export, JoSIM native/history proofs and joined lifecycle/recovery are implemented; [CPU evidence](discovery/cpu-acceptance.md) |
| G3 device disposition | Approved CPU-only first release; empty enabled-GPU matrix and scoped prior discovery conclusions in [GPU acceptance](discovery/gpu-acceptance.md) |
| G4 qualification | Tranche B implements mandatory no-skip proofs, the version-2 21-case acceptance contract, tested docs and [author security audit](security.md); current-head CI/review and owner merge remain required |
| TASK-034 production | Pending exact-main CI artifacts, real fresh encrypted backup/recovery gates, held deployment, restricted real-SkyPilot smoke, full matrix/native cleanup and durable verified receipt before general admission |

[Validation](validation.md) maps PHY-001 through PHY-020 to component evidence
and the remaining live-release obligations. [Tasks](tasks.md) retain original
IDs as traceability units, not separate PR boundaries. TASK-030/031/032/033 and
the conditional GPU disposition are consolidated in Tranche B; TASK-034 is the
existing controlled CD rollout after its merge, not another feature lane.

Public documentation is the [developer guide](../../qsim/static/index.html),
[physics API guide](../../docs/physics-engines.md) and downloadable
[agent instructions](../../qsim/static/agents.md). Examples are executed against
the packaged worker. Numerical compatibility and source/build provenance remain
separate from production placement and recovery acceptance.

The source proposal is `jheckey/fluxxedup` commit
`ae0cea45d244f9a6ee3204516fe6aef44340a34f`. WQS retains its existing architecture.
All GPU work uses SkyPilot; no GPU cell is advertised for this first release.
Existing credentials, recovery keys, backup/release gates and unrelated
workloads remain unchanged. Automatic reviews remain limited to two rounds
unless the owner authorizes an exception; the owner merges PRs.
