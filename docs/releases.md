# Releases and CD

CD follows STL.Hub's successful-main workflow pattern. CI builds and tests API,
controller and worker images. The CD workflow downloads those exact archives,
then a protected local runner publishes them to a private registry and deploys
immutable digests. It never rebuilds production images. API/admin share the API
image; controller/observer share the controller image; new SkyPilot workers use
the worker image digest.

## Activation and bootstrap

CD is disabled unless `WQS_CD_ENABLED` is `true`. Do not enable it merely because
this PR is merged. First:

1. Configure the GitHub `production` environment to permit only main deployments.
   Use a dedicated trusted runner labelled `wqs-deploy`, with Docker, kubectl,
   Python 3.12+, gh, local registry access and namespace-scoped credentials.
   This is separate from the isolated `wqs-local` CI runner. No cluster credentials
   are placed in hosted jobs or public configuration.
2. Inventory the private deployment patch. Move required behavior into reviewed
   generic code or runtime configuration before retiring it. Static guide URLs
   still need a runtime/configuration solution: do not rebuild CI images privately
   and claim they are the tested artifacts. Record parity evidence locally.
3. Bootstrap the admission protocol in a separately controlled rollout. Existing
   versions lack `qsim.release_control` and fail CD preflight without changing
   admission or images. Keep the autonomous workflow disabled until the existing
   service has protocol 1, persistent shared storage and exactly one replica of
   each service role. The controller must use `Recreate`; the default rolling
   strategy can start a surge pod that cannot acquire the controller lock and is
   rejected before any image or admission mutation. Preserve encryption material, callback secrets and job data.
4. Install the private config and backup/verification hooks described below.
   Test backup restoration independently. Rehearse interrupted deployment and
   recovery with synthetic jobs. Confirm registry retention preserves successful
   images and every rollback target beyond GitHub artifact expiry.
5. Set `WQS_RELEASE_CONFIG` to the private config's absolute path on the deployment
   runner. Mark `bootstrap_verified` only after the above evidence is recorded,
   then enable CD. The next successful main push CI qualifies for promotion.

The implementation PR does not install a runner, enable CD, mutate production,
or submit live simulation jobs. Those bootstrap actions remain explicit rollout
work. Do not create a second deployment writer alongside an existing operator
process: application image ownership must be assigned to this workflow first.

## Eligibility and artifact identity

Only successful CI **push** runs for the same repository's current main SHA are
eligible. PR, fork, manual test and failed runs cannot deploy. Recheck current
main before mutation; once mutation starts, finish or hold that pinned release.
The workflow serializes production deployments and does not cancel a rollout
when a newer merge arrives. A local file lock also rejects concurrent operators.

Each archive has a schema-1 JSON manifest containing `source_sha`, `target`,
`archive`, `archive_sha256`, `image_ref`, `config_digest`, `image_id` and `platform`. Require all three targets,
Linux/amd64, matching source labels and verified archive hashes before loading.
After loading, compare runtime configuration and uncompressed layer digests with
the verified archive, push without rebuilding, and resolve the
registry digest. An image ID, archive checksum and registry digest are different
identities and are recorded separately. Docker backend IDs can differ; the
archive config digest and loaded content checks establish portable identity. CI archives expire after 14 days; the
private registry must retain qualified deployment/rollback images independently.

## Private configuration contract

Keep this JSON outside the repository, readable only by the deployment operator.
No real hostnames, credentials, registry paths or kubeconfig belong in examples.
Required fields:

| Field | Meaning |
| --- | --- |
| `bootstrap_verified` | Boolean acknowledgement of the bootstrap checks above |
| `namespace` | Application namespace; credentials must be scoped to it |
| `registry` | Private image repository prefix, without a tag |
| `state_dir` | Persistent private absolute directory, shared by all invocations |
| `smoke_owner` | Dedicated API identity used only for release verification |
| `roles` | Exactly `api`, `admin`, `controller`, `observer`, each with distinct `deployment` and named `container` |
| `backup_command` | Nonempty argv array for the installed private backup hook |
| `verify_command` | Nonempty argv array for the installed private acceptance hook |

Hooks are trusted local programs executed as argv, never interpolated shell text.
They receive `WQS_RELEASE_SHA`. Credentials remain in private files/environment.
Both have a ten-minute verification deadline. Hook stdout must be JSON; captured
output is never copied into public CI logs. Backup returns
`{"verified":true,"backup_id":"private-record-id"}` only after a consistent,
recoverable backup of database, results and required invitation/key material.

The verifier must check readiness and actual deployed image identities for all
four roles, observer freshness and controller progress. It uses the dedicated
identity to submit small deterministic circuits, poll completion, compare numeric
results and download each protected NPZ artifact. Its stdout contains:

```json
{
  "sha": "FULL_DEPLOYED_SOURCE_SHA",
  "roles_healthy": ["admin", "api", "controller", "observer"],
  "controller_progress": true,
  "cases": {
    "clifft_stim": true,
    "clifft_qasm2_probability": true,
    "qsim_qasm2_sample": true,
    "qsim_qasm2_probability": true,
    "qsim_qasm3_sample": true,
    "qsim_qasm3_probability": true,
    "aer_qasm2_sample": true,
    "aer_qasm3_sample": true
  }
}
```

Only return true after that case's result and artifact checks pass. The deployer
requires every case, role and exact SHA. Retain job IDs and detailed evidence
privately. Verify that SkyPilot reports the owned smoke jobs completed; any worker
termination needed for the live lifecycle drill uses SkyPilot and only resources
created for the drill. Never terminate unrelated or active user jobs.

## Admission and rollout sequence

The local CLI stores a durable hold in the same SQLite transaction domain as job
admission. A new submission during the hold gets HTTP 503 and `Retry-After: 60`.
An existing idempotent request still returns its original job; reads, callbacks,
results and cancellation remain available. The controller continues dispatching
already-accepted queued jobs. There is no new job concurrency cap or job deadline.

1. Validate CI, all artifacts, configuration and the existing admission protocol.
2. Capture prior role images and worker image, publish new images, record the hold.
3. Wait for all accepted jobs to finish and dispatcher cleanup to complete. After
   15 minutes without a drain, stop deployment and leave the hold for the operator.
   Do not cancel jobs to make progress.
4. Take the verified backup. Recheck the pinned release is still current main.
5. Update controller (including its new worker digest), observer, admin, then API.
   Wait for each rollout. Old idle workers are never selected for new-image work;
   their normal SkyPilot idle lifecycle remains unchanged.
6. Admit only the dedicated smoke identity, run complete acceptance, and wait for
   those jobs' cleanup. Then reopen general admission and record verified status.

This first version drains before replacing the API because callback delivery has
bounded retries. It does not claim seamless rolling upgrades or zero downtime.
Read/result/callback routes remain logically enabled during a hold; API replacement
can briefly interrupt connections after the drain. A permanently stuck job or
saturated cluster can prevent a drain indefinitely across operator retries.

## Deployment time budget

The CD job allows 240 minutes. Its configured external-call budgets total about
179 minutes when every stage approaches its limit:

| Stage | Budget |
| --- | --- |
| Three image loads/pushes, including inspect/tag calls | 78 minutes |
| Two drain waits, including the final status call and polling delay | About 34.2 minutes |
| Backup and live acceptance hooks | 20 minutes |
| Four deployment patches and rollout waits | About 29.3 minutes |
| Admission control and deployment inventory calls | 16 minutes |
| Three GitHub eligibility/current-main reads | 1.5 minutes |

The remaining roughly 61 minutes covers checkout, artifact transfer, archive
verification, local state writes and receipt upload. This is an allocation, not a
guarantee against stalled storage or runner failure. Per-stage deadlines remain
unchanged; simulations still have no execution deadline. Interrupted rollouts
retain the recovery behavior below.

## Failure and operator recovery

The private state directory stores `deployment.json`, prior images, stage, CI run,
backup identity, acceptance and one immutable-by-convention `<sha>.json` receipt
for each verified deployment. Writes use atomic replacement and fsync. Only SHA,
CI run, schema and status are uploaded in the public receipt.

An interrupted or failed deployment blocks the next invocation. Do not delete its
marker to force retry. Inspect live images, the durable hold and accepted jobs,
then recover or roll forward under operator supervision. The local control CLI is:

```sh
python -m qsim.release_control status --release FULL_SHA
python -m qsim.release_control hold --release FULL_SHA
python -m qsim.release_control smoke --release FULL_SHA --smoke-owner PRIVATE_IDENTITY
python -m qsim.release_control open --release FULL_SHA
```

Execute in the API environment with its shared data directory. `open` requires
zero uncleaned jobs and the owning release SHA. After an interrupted open, inspect
admission before taking any further action; it may already be open. Preserve the
failed marker/evidence while an operator reconciles it. There is no automatic
rollback across unknown schema or capability changes and no database restore over
new jobs. A rollback needs compatible old images, a fresh drain and verification.

## Version records

Publishing a version records a previously verified deployment; it does not rebuild
or deploy. Use `vMAJOR.MINOR.PATCH`, initially `v0.1.0`. During 0.x, use minor for new
capabilities/compatibility changes and patch for compatible fixes. This application
release version is distinct from historic per-role private image labels; never
relabel old deployments.

After reviewing the private captured receipt, explicitly run:

```sh
python3 tools/cd/release.py record --config /PRIVATE/config.json --sha FULL_SHA --version v0.1.0
```

The command requires the captured verified receipt and complete acceptance,
creates an immutable lightweight tag at that SHA and a GitHub release with its CI
link. Retrying never moves a tag or edits an existing release. A tag already pointing
elsewhere is an error. Private images, hostnames, hook output and secrets are omitted
from public release notes. Preserve full digest and rollback evidence locally.

## Dependency CI (#17)

Standpunkt remains the default test runner. CI now tests installed SkyPilot task
serialization and the SDK signatures used by launch/exec/status/cancel/down/get,
plus existing fake-client recovery cases. Required hosted checks build all three
images and test API/controller imports, database initialization, runtime user,
dependency consistency and the isolated three-engine worker.

Use CI's manual `hosted_tests: true` input on main to exercise Node/Python setup,
tests and artifact upload on the hosted lane. This does not qualify a deployment.
The default stays Standpunkt-first with hosted fallback after a failed attempt.
An offline queued runner remains a known routing limitation, not an availability
probe. Live launch/status/cleanup validation remains a bootstrap/release gate;
ordinary PR CI requires no cluster credentials or live provisioning.
