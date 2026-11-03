# CD recovery and acceptance record

Normal production CD is automated. Operator recovery is required when an earlier
release leaves a failed ledger or an admission hold. This procedure preserves
that evidence and uses the same backup and acceptance checks as normal CD.

## Automated path

| Stage | Implementation | Required evidence |
| --- | --- | --- |
| Main CI | `.github/workflows/ci.yml` | Successful exact-main push CI, recorded local/hosted path, tested image archives |
| Release gate | `.github/workflows/cd.yml`, `tools/cd/release.py` | Eligible repository, workflow, event, branch and commit |
| Artifact promotion | `Deployer.deploy`, `validate_bundle`, `Deployer.publish` | Archive/content/revision checks and immutable published image identities |
| Admission and drain | `Deployer.control`, `Deployer.wait_drain` | Owning release hold and zero uncleaned jobs |
| Pre-rollout backup | Configured backup command, `verify_creation` | New invocation ID, encrypted storage readback and durable private locator |
| Rollout | `Deployer.apply` | Controller, observer, admin and API at expected immutable images |
| Acceptance | Configured verification command, `tools/cd/acceptance.py` | Eight real cases, results, native completion, controller progress and cleanup |
| Completion | `Deployer.deploy` | Verified private ledger/receipt, zero pending jobs, admission open and successful CD run |

Tests default to the configured local runner path. The hosted test fallback is
conditional; separate hosted image/tool checks are still required. A green
workflow or ready pod alone does not replace application acceptance.

## Operator-only recovery

Do not delete a failed ledger, fabricate a successful receipt, reuse old backup
creation output, or change a job back to queued to force a retry. Do not edit
credentials or bypass an interactive privilege boundary. Preserve unrelated jobs
and host workloads. GPU work, when needed, must remain scheduled through SkyPilot.

Use the trusted deployment-runner environment and its existing private release
configuration. The paths, workload names, credentials and backup locators remain
private. `state_dir` below means the directory named by that configuration.

1. **Pin the state.** Read current main and its CI/CD runs. Record the intended
   release SHA and the failed ledger's SHA, CI run, stage and backup identity.
   Read admission through `Deployer.control('status', sha)`. Keep admission held
   on ambiguity. Confirm which image revision actually reached each live role,
   including the controller's worker image, instead of assuming rollout completed.
2. **Preserve evidence and serialize.** Before any ledger change, acquire the
   existing `state_dir/lock` with nonblocking exclusive `flock`. Stop if another
   release owns it. Preserve the exact failed `deployment.json` and prior
   `acceptance-<sha>.json` under unique private filenames. Use `write_record`
   from `tools/cd/release.py` for durable JSON transitions. Never claim that a
   preserved failed attempt was successful.
3. **Check backup provenance.** For an already-mutated release, verify its original
   pre-rollout backup locator: requested ID, bucket/object version, ciphertext
   size and SHA-256. Read that exact stored version and compare bytes/hash.
   Availability of storage is not backup evidence. A later release still needs
   its own fresh backup through normal CD.
4. **Repair only the proven blocker.** Keep backup validation enforced. For an
   insecure locator directory, verify the runner owns a real directory through
   a no-follow directory descriptor before narrowing that directory to `0700`.
   Do not recursively chmod the PVC or change its mount root. Reconcile the
   runner's merged `fsGroupChangePolicy: OnRootMismatch` setting and verify the
   private child remains `0700` after replacement. A root ownership mismatch can
   still trigger recursive permission changes; see [runner configuration](deployment-runner.md).
   Host-policy repairs belong to the infrastructure owner. Preserve enforcement,
   use reviewed narrow rules and approved sudo installation, and require live
   confined checks before retrying affected launches.
5. **Reconcile owned jobs.** Identify jobs from the preserved acceptance receipt
   and verify their release/owner. If cancellation is necessary, use their
   authenticated `/api/jobs/{id}/cancel` route. Let the controller perform
   SkyPilot cancellation and cleanup. Require zero pending/uncleaned jobs before
   resuming or reopening. Never delete worker pods or modify unrelated jobs as a
   substitute for SkyPilot cleanup.
6. **Choose recovery based on actual mutation.** If no role changed, verify all
   live role and worker images against the prior verified receipt before
   restoring that baseline record and releasing its hold. If roles changed,
   finish acceptance for that exact deployed revision; do not substitute the
   prior receipt or relabel it as current main. An incompatible/mixed rollout
   needs a separately reviewed roll-forward or rollback plan.
7. **Resume the deployed revision.** Under the release lock, verify expected role
   images with `Acceptance.roles`, required Secret keys with
   `Deployer.preflight_secrets`, and matching hold/pending state. Preserve the
   failed receipt, record the operator reason/time, and restore the matching
   ledger's active `deploying` stage. Enable only its configured smoke identity
   through `Deployer.control('smoke', sha)`. Invoke the existing configured
   verification command with `WQS_RELEASE_SHA` set to that same SHA. It submits
   new idempotent jobs through WQS, not direct simulator or infrastructure calls.
8. **Accept or retain the failure.** Require `verify_acceptance`, native job
   success, controller/observer evidence, healthy exact role images and zero
   pending jobs. Write `acceptance_passed` before opening admission, then write
   `verified` and the immutable-by-convention `<sha>.json` receipt. On any error,
   retain a failed ledger, private diagnostics and the hold; reconcile actual
   admission again if a response was lost. Never infer completion from a
   disconnected client. The normal verifier remains bounded by its deadline.
9. **Return to automated CD.** Require successful exact-current-main push CI.
   Allow its normal workflow-run CD trigger, or rerun the eligible failed CD run
   for that exact SHA after recovery. Normal CD must create a new backup and run
   acceptance again before reopening. Do not merge PRs or manually request bot
   reviews as part of recovery.

This is an operator procedure using existing release primitives, not an automatic
failed-ledger retry policy. Shell history alone is insufficient: retain the
private before/after records, backup readback evidence, owned job selection and
final receipt.

## Verified recovery sequence

The recovery completed after [#62](https://github.com/Wehden/wqs-quantum-sim/pull/62)
and [#63](https://github.com/Wehden/wqs-quantum-sim/pull/63).

- An earlier backup completed encryption/readback but refused locator persistence
  because the directory had group permissions. The operator preserved the failed
  ledger, narrowed only the verified directory and proved a new encrypted backup
  and durable locator. The merged runner permission policy survived replacement
  with the locator directory still `0700`.
- The next rollout reached all roles at `5bfcff9392a24da50a5adf42971e21ee81bfb3f0`
  but acceptance launches hit host runtime signal denials. Only the eight owned
  acceptance jobs were cancelled through WQS and cleaned through SkyPilot.
  Admission stayed held. The owner installed the approved infrastructure policy;
  confined signal and rsync checks passed on both affected hosts. The source and
  validation evidence are in [Laminar #185](https://github.com/Wehden/laminar/pull/185).
- Main CI also exposed a backup termination-cleanup failure. An unchanged test
  rerun passed, while a deterministic second-signal regression reproduced a
  plaintext cleanup interruption. #63 made cancellation idempotent and disarmed
  the remaining timer, retaining the original cleanup assertions.
- Operator recovery verified the deployed role images and exact stored backup
  version, preserved the failed acceptance records and invoked the standard
  verifier for `5bfcff9`. All eight cases, native checks and cleanup passed;
  the ledger became verified and admission reopened.
- Exact-main [CI run 41586264089](https://github.com/Wehden/wqs-quantum-sim/actions/runs/41586264089)
  passed for `20a2a93de9e6aa1727774e43e59f2a0dbcbaa80b`. Tests ran on the local
  runner; hosted test fallback was skipped and hosted image/tool checks passed.
- The workflow automatically started [CD run 41586786569](https://github.com/Wehden/wqs-quantum-sim/actions/runs/41586786569).
  CD verified/promoted its tested artifacts, created a fresh readback-verified
  backup, deployed all four roles and invoked acceptance. No current-main jobs
  were submitted manually.
- The final private receipt and ledger are verified at `20a2a93`. All eight cases
  below passed, including native SkyPilot completion and cleanup. Pending count
  is zero, admission is open, and CD completed successfully. Existing credentials,
  unrelated workloads and backup/release gates were unchanged by this recovery.

The eight required cases remain defined in `tools/cd/release.py::SMOKE_CASES`:
Clifft Stim, Clifft QASM2 probabilities, Google qsim QASM2 samples/probabilities,
Google qsim QASM3 samples/probabilities, and Aer QASM2/QASM3 samples. This record
covers the existing circuit service. It does not enable gated physics backends.
