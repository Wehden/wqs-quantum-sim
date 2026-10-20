# CI and local validation

The workflow follows CFD Playground's routing pattern: trusted main pushes and
same-repository pull requests prefer the two-slot local Linux AMD64 pool when fresh status reports an idle slot. Fork pull requests and manual dispatches from non-main branches use GitHub’s
hosted Ubuntu lane. The two runners run on Standpunkt and carry the dedicated `wqs-ci-local` label.
The workflow names both lanes, marks the unused lane skipped, and writes the
selection and reason to its run summary.

Both lanes execute `bash tools/ci/check.sh`: frozen dependency installation,
dependency consistency, all three simulator engines (Clifft, Google qsim and Aer), Python API/controller tests,
and JavaScript console tests. JUnit results are retained for 14 days. Tests use
synthetic identities, temporary storage, and mocked dispatch; no live SkyPilot,
SMTP, or cluster credentials are needed.

The dedicated local runner must provide Python 3.12, uv 0.11.6, and Node 22.
Do not install a kubeconfig or deployment credentials there. Run it with an
unprivileged UID and no mounted host Docker socket or service-account token.
Restrict its network access to DNS and public dependency/GitHub endpoints.
Never enable `pull_request_target` execution of untrusted checkout code.

## Capacity-aware routing and hosted retry

`WQS_LOCAL_CI_ENABLED=true` enables the pool. The trusted host publisher updates
repository variable `CI_LOCAL_RUNNER_STATUS` every 30 seconds with this schema:

```json
{"schema":1,"observed_at":1792497600,"capacity":2,"online":2,"busy":1,"idle":1}
```

`observed_at` is UTC epoch seconds. Routing uses hosted CI when status is missing,
malformed, older than 120 seconds, more than 15 seconds in the future, has
inconsistent counts, or reports no idle runners. Counts must be nonnegative
integers, `capacity` must be 2, and `busy + idle == online <= capacity`.
Only the two ordinary runners labelled `wqs-ci-local` belong in this snapshot;
exclude the legacy `wqs-local` runner and the restricted `wqs-deploy` runner.
The workflow has no inventory credential. The publisher's GitHub credential
stays on its trusted host, outside the ordinary CI pods.

Fork pull requests, non-main manual runs, and explicit `hosted_tests: true`
dispatches stay hosted regardless of capacity. Set `WQS_LOCAL_CI_ENABLED=false`
to bypass local CI. A failed local test or artifact step still retries the same
suite on GitHub-hosted Ubuntu; cancellation does not trigger retry. Both attempts
retain distinct artifacts. Require `CI result and path` in branch protection.

The snapshot is an admission hint, not a reservation: concurrent workflows can
observe the same idle slot, or a runner can fail after routing. Such jobs can
still queue; job execution timeouts do not start while queued. Disable local CI
and start a new run if that race occurs. Missing publisher updates fail closed
to hosted CI after the freshness window.

## Two-slot runner deployment

[`runner.example.yaml`](../tools/ci/runner.example.yaml) preserves the live
ordinary-CI security boundary in namespace `wqs-ci`, with a new StatefulSet named
`wqs-ci`. Its pods register as `wqs-ci-0` and `wqs-ci-1`; each has an independent
8 GiB PVC (`data-wqs-ci-0` and `data-wqs-ci-1`). The entrypoint refuses to start
with cached registration belonging to another slot. It removes the short-lived
registration token from the runner process environment after setup.

Build from the reviewed commit:

```bash
docker build -f tools/ci/runner.Dockerfile -t YOUR_REGISTRY/wqs/ci-runner:REVIEWED_SHA .
docker push YOUR_REGISTRY/wqs/ci-runner:REVIEWED_SHA
```

Render both example image references to the pushed immutable digest. Provision a
fresh `registration` Secret through the established private bootstrap process;
never commit its value. Apply the rendered manifest, verify two GitHub runner
registrations with the new label, and confirm both are online and idle before
enabling the status publisher and workflow routing. Delete the registration
Secret after both slots register; retained per-slot state supports restart.

Only after the new slots pass acceptance, stop the old ordinary `runner`
Deployment and retire its old registration. Preserve its PVC for rollback; do
not copy credential state into either new PVC. Roll back routing by setting the
local-enable variable false, drain the new slots, and scale only the new
StatefulSet to zero. The separate restricted production runner, its credentials,
and CD enablement are not part of this change.

## Packaged release checks

A separate required GitHub-hosted job runs `bash tools/ci/check-release.sh`.
It builds the API, controller and worker targets, verifies non-root service imports,
database initialization and installed SkyPilot configuration, then runs the isolated
three-engine worker checks. Tests also validate actual SkyPilot SDK signatures;
remote dispatch and failure recovery remain mocked in ordinary CI.

Docker stays on the hosted runner. The final result fails if any image check fails.
Successful main push runs retain the exact tested archives and checksum manifests
for 14 days. Images are published only by the separately gated CD workflow; see
[Releases and CD](releases.md). PR runs do not upload release bundles.

For workflow dependency updates, dispatch CI with `hosted_tests: true` to exercise
hosted Node/Python setup, the full suite and artifact upload. This explicit path
skips Standpunkt; the default remains unchanged and manual runs cannot deploy.

## Main-branch integration gate

Target implementation PRs at `main`. If a stacked base is explicitly needed,
retarget each dependent PR to `main` after its parent merges and rerun CI before
merging. A PR marked merged into a feature branch is not integrated into main.

Before reporting a feature complete or starting its release handoff, fetch main
and verify that every contributing PR's merge commit is an ancestor of
`origin/main` with `git merge-base --is-ancestor`. Verify successful push CI for
the exact integrated main SHA separately from the individual PR checks. If a
merge used squash or rebase, reconcile the resulting main commit and diff
explicitly instead of claiming ancestry that does not exist.
