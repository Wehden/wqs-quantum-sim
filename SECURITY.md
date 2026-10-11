# Security

Report suspected vulnerabilities privately through this repository's GitHub
security reporting facilities when available. Do not put credentials, private
circuit data, or infrastructure details into public issues. If private reporting
is unavailable, request a private contact channel without publishing the details.

Keep the operator console and worker callback paths off public ingress.
Only trusted code may run on the private CI runner; external pull requests use
the hosted CI lane. Deployment credentials are not required by the test suite.
