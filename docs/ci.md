# CI and local validation

The workflow follows CFD Playground's routing pattern: trusted main pushes and
same-repository pull requests prefer a dedicated local Linux AMD64 runner when
`WQS_LOCAL_CI_ENABLED=true`. Fork pull requests and manual dispatches use GitHub's
hosted Ubuntu lane. The local runner carries the generic `wqs-local` label; its
placement and registration remain private operator configuration.

Both lanes execute `bash tools/ci/check.sh`: frozen dependency installation,
dependency consistency, both simulator engines, Python API/controller tests,
and JavaScript console tests. JUnit results are retained for 14 days. Tests use
synthetic identities, temporary storage, and mocked dispatch; no live SkyPilot,
SMTP, or cluster credentials are needed.

The dedicated local runner must provide Python 3.12, uv 0.11.6, and Node 22.
Do not install a kubeconfig or deployment credentials there. Run it with an
unprivileged UID and no mounted host Docker socket or service-account token.
Restrict its network access to DNS and public dependency/GitHub endpoints.
Never enable `pull_request_target` execution of untrusted checkout code.

The source tree contains no live runner hostname, registry, token, or generated
runner manifest. Bootstrap registration credentials belong in an operator-owned
secret store and should be removed after registration. Disable the repository
variable to select hosted CI when the local runner is unavailable.
