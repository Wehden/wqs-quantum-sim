# Contributing

Use Python 3.12 on Linux AMD64. Run `bash tools/ci/check.sh` before opening a pull
request. With Docker available, also run `bash tools/ci/check-worker.sh` to build
and check the isolated worker image; its temporary image tag is removed on exit.
Standpunkt is the default test runner, with GitHub-hosted fallback. Image checks
run as a separate required GitHub-hosted job because Standpunkt's runner has no
Docker daemon.
Keep engine-specific behavior covered with numerical and API tests.
Do not include real credentials, deployment addresses, kubeconfigs, job data,
or operator reports. Use reserved example domains and generated test credentials.
Live cluster smoke tests are opt-in and require the operator's private configuration.
