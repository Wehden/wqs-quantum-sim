# Contributing

Use Python 3.12 on Linux AMD64. Run `bash tools/ci/check.sh` before opening a pull
request. Keep engine-specific behavior covered with numerical and API tests.
Do not include real credentials, deployment addresses, kubeconfigs, job data,
or operator reports. Use reserved example domains and generated test credentials.
Live cluster smoke tests are opt-in and require the operator's private configuration.
