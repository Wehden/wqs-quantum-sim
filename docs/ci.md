# CI and local validation

The workflow follows CFD Playground's routing pattern: trusted main pushes and
same-repository pull requests prefer a dedicated local Linux AMD64 runner by default. Fork pull requests and manual dispatches from non-main branches use GitHub’s
hosted Ubuntu lane. The local runner runs on Standpunkt and carries the `wqs-local` label.
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

The source tree names Standpunkt but contains no private addresses, registry
credentials, tokens, or generated runner manifest. Bootstrap registration credentials belong in an operator-owned
secret store and should be removed after registration. Disable the repository
variable to select hosted CI when the local runner is unavailable.

## Standpunkt first, GitHub-hosted retry

No runner-status credential is required. Eligible events attempt Standpunkt first.
A failed local test or artifact step triggers the same suite on GitHub-hosted
Ubuntu. The final `CI result and path` check reports which paths ran or were
skipped and passes only if the chosen path succeeds. A failed local attempt
remains visible even when the hosted retry passes. Both attempts retain distinct
artifacts. Routing and final reporting use small GitHub-hosted jobs.

Set `WQS_LOCAL_CI_ENABLED=false` to bypass Standpunkt. Fork pull requests and
manual runs from non-main branches always use hosted CI. Explicit cancellation
does not trigger retry. Configure branch protection to require `CI result and path`.

An offline runner may leave its job queued; execution timeouts do not start while
queued. This failure-based fallback does not detect offline queues immediately.
An operator can disable local CI and start a fresh run in that situation.

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
