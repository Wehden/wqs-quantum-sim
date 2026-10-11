# wqsim

CPU quantum simulation service built with FastAPI, SkyPilot, and Kubernetes.
Submit a circuit, follow its durable job status, and download measurement samples
or selected basis probabilities. Clifft and Google qsim share the same API,
invitation authentication, and worker pool.

## Supported inputs

| Engine | Input | Operation |
| --- | --- | --- |
| Clifft 0.11.0 | Stim with supported Clifft extensions | Measurement sampling |
| Clifft 0.11.0 | Unitary OpenQASM 2 | Selected basis probabilities |
| Google qsim 0.22.1 | Unitary OpenQASM 2 through Cirq | Selected basis probabilities |

Clifft is the default. Set `"engine": "qsim"` on a QASM request to use Google's
full state-vector simulator. Its single-precision results should be compared
with numerical tolerances. This integration does not expose every upstream
feature or accept executable Python.

See the [developer guide](qsim/static/index.html), [agent API guide](qsim/static/agents.md),
and [deployment guide](docs/deployment.md). Example addresses use reserved domains
and must be configured for your installation.

## Dev bootstrap

Python 3.12 on Linux AMD64 is the supported target. Install
[uv](https://docs.astral.sh/uv/) and Node.js 22; dependencies are locked in `uv.lock`.

```sh
uv sync --frozen --extra worker --extra controller
bash tools/ci/check.sh
```

The checks run both native simulator engines, API authorization and idempotency,
SSE replay, worker lifecycle logic, and JavaScript console tests. They do not
launch cloud or Kubernetes resources. See [CI](docs/ci.md) for local and hosted lanes.

## Run the API

```sh
uv run python scripts/invite.py developer --output var/developer.key
uv run uvicorn qsim.api:app --host 127.0.0.1 --port 8080
```

The API alone queues jobs. Execution requires a configured SkyPilot controller
and a worker image reachable by the cluster. Do not start the controller until
its deployment settings are supplied.

Submit with your invitation key in the `Authorization: Bearer` header.
`POST /api/jobs/stim` and `POST /api/jobs/qasm2` require an `Idempotency-Key`.
Reusing a key with the same normalized body returns the original job;
a changed request returns HTTP 409.

`GET /api/jobs/{id}/events` streams durable progress and the final result summary.
Resume using `Last-Event-ID`. Download the NumPy archive from
`GET /api/jobs/{id}/result`. Streaming does not provide incremental samples.
Clients manage batches, dependencies, and quantum state between requests.

## Architecture

- The API persists owner-scoped jobs, idempotency records, and events in SQLite.
- A single controller submits jobs through SkyPilot launch/exec and reuses
  compatible idle workers. Capacity failures queue and retry.
- Each simulation runs in a fresh bounded subprocess. Circuit input is data.
- An optional private operator console handles invitation review and resource
  observations. It trusts its protected network; it must not be publicly exposed.

The current worker profile requests 2 CPUs and 4 GiB. Simulation subprocesses
have a 300-second deadline and 3 GiB address-space limit. Clifft supports active
width up to 22; Google qsim supports up to 26 declared qubits. There is no fixed
active-job or saved-job count cap. Completed jobs expire after 30 days.

## Operations and configuration

Keep deployment addresses, credentials, kubeconfigs, generated manifests, and
operational reports in ignored `local/` or `var/` directories. The repository
contains source and example configuration only, with no live deployment inventory.

Use [the deployment guide](docs/deployment.md) for settings, public/private route
boundaries, and backup considerations. See [CONTRIBUTING.md](CONTRIBUTING.md) for
checks before submitting changes and [SECURITY.md](SECURITY.md) for reporting issues.

## License and acknowledgments

This service uses the [MIT license](LICENSE). Dependencies retain their own
licenses. Thanks to [Unitary Foundation's Clifft](https://github.com/unitaryfoundation/clifft),
[Google qsim](https://github.com/quantumlib/qsim) (Apache 2.0),
[Cirq](https://github.com/quantumlib/Cirq), and [SkyPilot](https://github.com/skypilot-org/skypilot).
