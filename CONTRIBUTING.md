# Contributing

Use Python 3.12 on Linux AMD64. Run `bash tools/ci/check.sh` before opening a pull
request. Docker must be installed and accessible. This command runs the numerical,
API and console tests, then builds the worker image and checks both engines through
the isolated worker entry point. Its temporary image tag is removed on exit.
Standpunkt is the default CI runner; the GitHub-hosted fallback runs the same checks.
Keep engine-specific behavior covered with numerical and API tests.
Do not include real credentials, deployment addresses, kubeconfigs, job data,
or operator reports. Use reserved example domains and generated test credentials.
Live cluster smoke tests are opt-in and require the operator's private configuration.
