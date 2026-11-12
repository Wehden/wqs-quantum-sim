# Physics validation and release ledger

PR #80 is merged at `87304d249005ad65db6a256bf970e04ede1c1288`. Exact-main
push CI `42535051613` passed, including Standpunkt child `42535074189` and hosted
image/runner checks. Tranche B adds versioned promotion qualification; its own
PR CI/review and production acceptance are separate pending stages.

## Requirement coverage

| Requirement | Executed component evidence | Release obligation |
| --- | --- | --- |
| PHY-001 compatibility | Frozen compatibility baseline, transactions, worker lifecycle, all eight circuit cases retained | Eight circuit cases in version-2 live receipt |
| PHY-002 one job system | Placement and joined HTTP/native lifecycle tests | Real SkyPilot job IDs, cleanup and owned admission |
| PHY-003 submissions | Physics API normalization, explicit/generic-route replay and owner tests | Named canonical request and smoke-owner binding |
| PHY-004 input boundary | Schema/model, pointer, finite/shape and raw-preflight tests | Current deployed schemas and admission |
| PHY-005 no caller code | JoSIM security, strict model/operator allowlists and callback archive negatives | Pinned worker/native identity |
| PHY-006 units | Analytic Lindblad phase/decay, charge-basis scqubits and JoSIM SI/SFQ checks | Same independent math in live verifier |
| PHY-007 models | Lindblad operators, Hermiticity, tensor order and drive tests | Certified CPU profile/context |
| PHY-008 states | Pure/density/thermal-state tests; release density trace/PSD checks | Real native fixture output |
| PHY-009 evolution | SE/ME/MC native families; MC PCG64 survival and seeded-repeat checks | Named evolve and repeat cases |
| PHY-010 other operations | Native spectrum, unique steady state and Hilbert/Liouville propagators | All four operation families in receipt |
| PHY-011 resources | Constrained CPU image certificates, workspace queries and raw-input preflight | Actual refreshed CPU allocation and immutable profile |
| PHY-012 results | Result/schema/security tests and actual complex/Unicode encrypted recovery | Download/store artifact digest equality |
| PHY-013 transport | Stored-plan callback/header/byte tests; final-callback-loss reconciliation | Complete results and native cleanup |
| PHY-014 discovery | API schema/capability solver-free checks; documented-schema tests | Runtime capabilities remain authority |
| PHY-015 docs and CI | Published JSON/schema tests and all 11 packaged documented examples | Exact-head configured CI and protected checks |
| PHY-016 additional engines | All four scqubits classes/export; JoSIM frozen/history/capacity proofs | scqubits/export round trip and JoSIM SFQ |
| PHY-017 scqubits API | Spectrum/matrix/export native and owner/idempotency tests | Independent charge-Hamiltonian/eigenpair checks |
| PHY-018 JoSIM API | Allowlisted netlist, original/candidate exact equivalence and real SFQ | Full native waveform digest, trace units/order and SFQ math |
| PHY-019 GPU discovery | Prior SkyPilot GPU discovery; corrected timing and fallback evidence | Empty enabled-GPU matrix, no new GPU pass claim |
| PHY-020 placement | Immutable image/profile, pool separation, capacity retry and native reconciliation | Refreshed actual worker image/resources and native completion |

The named joined tests are `test_joined_lindblad_operation_families`,
`test_joined_scqubits_native_spectrum_and_matrix_elements`,
`test_joined_scqubits_export_to_lindblad_is_a_client_sequence`,
`test_joined_josim_sfq_and_circuit_pool_separation`, callback-loss/tamper tests and
actual native-result backup/restore. The package executes original/candidate
JoSIM source equivalence and largest-shape CPU proof separately.

## Version-2 release contract

`qsim.release_probe.release_cases()` defines 21 named requests: eight unchanged
circuit cases, seven Lindblad solver/operation families, an additional MC seeded
repeat, scqubits spectrum/matrix elements/export, the dependent exported
Lindblad request, and JoSIM SFQ. The inventory controls the probe bound. It is
not an arbitrary caller-provided job list or a new global job cap.

The export child is submitted only after validating the parent's actual returned
numeric handoff. MC acceptance derives survival from eight PCG64 thresholds,
`mean(threshold < exp(-t/3))`, in addition to exact seeded repetition. For JoSIM,
the frozen original row-major network-byte-order double digest is
`698addcdc6ff152b94d4d908fd4023738560622fc31ab306a2b0e407fe5beb04`.
This preserves native output-time selection, including its nonuniform retained
cadence; no uniform resampling or waveform tolerance was introduced.

Missing/extra/false case members, a historical/unknown receipt version, wrong
owner/image/profile/source/result identity, pending/failed native work and
incomplete cleanup fail promotion. Acknowledgement/final-persistence failures
attempt owned-hold recovery and leave failed receipts. Unknown recovery is not
green.

## Commands and evidence scope

Canonical commands remain `bash tools/ci/check.sh`,
`bash tools/ci/check-worker.sh` and `bash tools/ci/check-release.sh`.
The image harness mandatorily executes lifecycle, release-case and documented
example modules, rejects unittest skips/expected failures/zero execution, and
requires no skipped pytest proofs plus actual successful native-process counts.
CI retains that JUnit receipt even on failure. Standpunkt-first routing,
protected names and hosted fallback remain unchanged.

Local packaged proofs use inspected Docker image identity and an explicitly
synthetic SkyPilot placement reference. They do not prove a live cluster or
registry deployment. Exact-main artifacts, real backup/recovery and actual
SkyPilot acceptance remain TASK-034. Current credentials and all existing
backup/release gates are unchanged.

## Restored-cluster live attempt and launcher correction, 2026-11-10

After the disk restore, exact-main CD run `42819837848` attempt 2 passed fresh
encrypted creation/readback, deployed source `479fa99` and admitted only its smoke
identity. Eight circuit cases succeeded. Twelve physics cases failed before
callback, so the dependent export-to-Lindblad case was not submitted. Native
SkyPilot job logs reported `bash: -n: command not found` (exit 127).

The pinned SDK's `ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD` defines `sudo()` as a
pass-through for root shells. The existing bare `sudo -n` invoked that function.
The correction uses the installed `/usr/bin/sudo` binary explicitly. Mandatory
joined image tests now execute the controller's actual physics task command
under that SDK prefix, starting with the trusted root launcher and checking the
transition to UID 1000 before worker execution. Only test-launcher SETUID/SETGID
capabilities are added; native simulation remains UID 1000 under the existing
CPU, memory, file and child-environment constraints.

Local correction evidence: 96 focused placement/dispatcher/worker tests passed;
101 joined lifecycle, release and documentation image tests passed with zero
failures/errors/skips in 235.37 seconds. This is still synthetic placement,
not a successful new live release. All failed live smoke jobs settled and cleaned
without forced cancellation; the owning hold remains and pending count is zero.
Failed receipts, job/native identities and the original fresh backup evidence
are retained privately. TASK-034 remains open pending reviewed correction,
exact-main CI, controlled recovery and successful full live acceptance.

## Distributed native verification checkpoint, 2026-11-12

PR #82 merged at `a058714`; exact-main CI `43044212765` attempt 2 passed, with
101 joined image tests and zero failures/errors/skips. Controlled recovery kept
admission held, preserved failure evidence and passed a fresh encrypted backup
with exact storage readback and a durable locator before rolling out all roles.

One native JoSIM setup failed on an incomplete SkyPilot SDK wheel ZIP. All 20
submitted jobs settled and cleaned, including 19 successes. A complete unchanged
image retry then produced 21 successful, cleaned jobs. This includes the actual
scqubits export handoff, JoSIM and seeded Lindblad repeat. Final acceptance still
failed: serial forced allocation refreshes exceeded the native probe's existing
120-second bound. The release is held with zero pending jobs; no successful
release receipt exists for this attempt.

The correction submits queue reads asynchronously before waiting and performs
one forced inventory refresh for the selected allocations. It preserves exact
native job IDs, native status, image and per-allocation CPU/RAM/node/accelerator
checks. Regression tests distribute the 21 named cases across distinct clusters,
return inventory out of order and inject individual allocation/native failures.
A read-only candidate diagnostic verified all 21 jobs on the actual 16
allocations in 18.2 seconds. This measurement changes no installed source or
admission and is not a completed production acceptance gate.

TASK-034/G4 remain open until the correction reaches main, fresh exact-main CI
passes and the standard full release verifier succeeds. See
[the recovery checkpoint](../../docs/cd-recovery.md#physics-recovery-checkpoint-2026-11-12)
for the preserved failure sequence and backup receipt handling. Existing
credentials and backup/release gates remain unchanged.
