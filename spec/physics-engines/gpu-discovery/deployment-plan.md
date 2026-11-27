# Local qsim CUDA deployment plan

## Decision and scope

The first accelerator candidate is native qsim CUDA selected-basis probabilities on one 12 GiB RTX 3060. Use the frozen 26-qubit, depth-60 fixture and its 24-qubit, depth-20 control for qualification. The two-seed OpenQASM 2 screen confirmed that circuit depth changes the fresh-child speedup on this hardware. Measure service-level latency before defining a broader routing rule. [Discovery results](results.md)

The production canary should send only the dedicated release verifier's fixed fixture to a GPU profile. Existing public job routing uses the CPU profiles while that canary runs. GPU selection stays with the server. Keep the current API request and artifact formats, precision, bit order and probability tolerances.

## Implementation sequence

| Stage | Change | Required evidence |
| --- | --- | --- |
| Discovery | Reuse the pinned wheel and add the 12 GiB fixture profile. Collect both same-worker comparisons with independent receipt capture. | Each case has 20 correct pairs, native CUDA identity, usable and sampled device memory, median and p90. The candidate meets the approved speed threshold with its paired confidence interval. The worker is absent after cleanup. |
| Package | Build a separate CUDA worker image from pinned qsim source, CUDA toolkit, and runtime libraries. Reuse the existing QASM parser and probability artifact writer. Add an internal, immutable GPU execution profile and allow it only for the verifier's frozen circuit and output shape. | CPU worker, API and controller continue to pass existing CI. Hosted CI validates the new image’s source labels, native imports, dependency resolution, and artifact contents. A worker presented with a GPU profile rejects an image, backend, device or fixture mismatch. |
| Stage | Publish the verified CI-built image by digest. Launch the exact image through SkyPilot on one RTX 3060, with eight vCPUs, at least 16 GB RAM and 30 GB ephemeral storage. | One visible RTX 3060, 12 GiB physical VRAM, sufficient free CUDA memory, matching image and native module, synchronized kernel execution, and passing QASM/NPZ checks on the frozen cases. Record complete wall time, peak RSS and VRAM, timeout behavior, and confirmed cleanup. |
| Release canary | Extend the image build, archive validation and release verifier to recognize the separate GPU artifact and the fixed GPU acceptance cell. Pin the accepted image and resource profile in the persisted dispatch context. Allow one GPU canary job during the release hold for the configured smoke identity. | Exact-main artifact identity, fresh encrypted backup and readback, all existing CPU cases, and the new GPU case with its live native job identity and teardown evidence. Write the verified release record before reopening admission. [Release procedure](../../../docs/releases.md) |
| Extend | Use the two-seed matrix to select which cells to repeat on the verified candidate image. Measure end-to-end latency with cold provisioning and with an already-ready worker. | Specify an operation and circuit envelope supported by end-to-end evidence before opening the new profile to public jobs. Record queue and scheduling behavior under one-worker capacity. |

## Canary tasks and evidence

**QSIM-CUDA-001 — Package and check the native CUDA worker.** Build a separate
`worker-qsim-cuda` target from the same worker source and lock as the CPU
target. Build the pinned qsim commit for SM 8.6 from the pinned CUDA 12.8.1
toolkit. Lock its build inputs. Keep the regular worker as the default build
target. The private canary accepts two fixed probability cases and an 8-qubit
bit-order control. Its host checks exercise the normal parser and NPZ writer.
The packaging checks confirm that the wheel contains native CUDA, the CPU
modules and their required libraries.

**QSIM-CUDA-002 — Qualify the candidate digest in isolation.** Save and hash
the build artifact and record the commit and image ID. Publish the candidate to
the approved private staging registry. Invoke that immutable digest through
SkyPilot on one RTX 3060, with eight vCPUs, 16 GB RAM and 30 GB ephemeral
storage. Use a wall-time bound for the experiment and verify teardown. Require
one visible device, its 12 GiB physical capacity, enough free memory, and
successful native CUDA execution. Match each NPZ against the same-image CPU
result and an independent bit-order control. Retain device, native module,
memory, timing and failure receipts. A local package run provides candidate
evidence. A production release still needs the eligible CI artifact by digest.

**QSIM-CUDA-003 — Run the API timing study on an isolated service.** Use the
candidate API and existing dispatcher with a disposable state directory, a
synthetic invite identity and private callback reachability. Use the fixed
26-qubit depth-60 case and 24-qubit depth-20 control from discovery. First
collect three cold and ten ready-worker samples per case and backend. Run one
simulation at a time. Submit fresh idempotency keys for each sample. Include a
separate interrupted-client replay and a two-job queue check. A cold sample
requires SkyPilot `launch` and proof that no compatible worker existed at
submission. A ready sample requires `exec` on the same available worker and a
fresh numerical child. Alternate CPU and CUDA cohort order across blocks and
report actual host identities privately. Do not claim a same-host comparison
unless SkyPilot placed both cohorts on the same host.

A cold trial starts with a new worker allocation. The runtime may already
have its immutable image cached. Record that distinction with the placement
receipt. Ready trials keep one verified worker and execute each request in a
fresh worker child. The queue check exercises the study's single-worker
constraint. It does not measure capacity of the production pool.

Record client monotonic time before POST, after the 202 response, on each SSE
state transition and after the result download. Join it to the persisted job,
SkyPilot request and native queue timestamps. Report API admission, queue wait,
placement and setup, execution, callback, result download, total latency,
median and p90 together with all failed or timed-out trials. Three cold
samples are for screening. Repeat at a larger sample size before treating
their p90 as an operational estimate. Use the existing CPU service profile
as the primary comparator. Keep the same 1.5× and 0.5-second fresh-child
screening thresholds and record any provisioning cost separately.

Use the [timing driver](../../../tools/canary/qsim_api_timings.py) with an
empty private state directory, the locked API and controller environments,
the verified worker digests, and the CUDA module hash from the qualification
receipt. Supply a preflight command that checks the current workload policy
and scheduler capacity before each cold cohort. The driver accepts callbacks
from the private worker network and restricts client routes to its local
address. The default timing matrix has three cold and ten ready samples per
cell. Pass `--serial-queue-check` to record the separate queue trial.
Check free host CPU and RAM alongside accelerator slots before placement.
Retain any capacity failure and verify retirement before using `--cohort`
to resume only a missing cohort. Record the actual order after a resume.
Keep the JSON lines output and recheck the owned SkyPilot allocations after
the run. The API qualification uses a disposable SQLite database and a
synthetic invitation with a new token per run.

**QSIM-CUDA-004 — Integrate the release verifier.** Once the candidate and API
checks pass, add the held-release policy for the single fixed canary, a
persisted context tied to the image digest, and validation of its actual
SkyPilot assignment, native result and retirement. The release must pass the
existing CPU cases and fresh encrypted backup gate. The production verifier
owns admission and reopening. An absent or mismatched GPU case fails a release
that requests the canary. Keep normal client dispatch on the CPU policy.

The GPU release must qualify the artifact produced from the eligible main CI
run. Extend the artifact manifest with a separate worker target whose source
SHA, archive digest and config digest are checked at publication. Bind the
held-release context to that worker digest, the native module SHA, the
26-qubit fixture fingerprint, the measured resource profile and the
configured verifier identity. A replay returns the same job and context.
One native success must include the expected device, exact image, completed
NPZ checks, and verified worker retirement before reopening admission.
Prove failure handling for wrong identity, fixture, digest, missing backup
evidence and interrupted retirement. The earlier private staging receipt
qualifies the candidate behavior. Live release acceptance needs its own
receipt for the exact promoted artifact.

Stages 001–003 use only owned temporary resources and synthetic inputs. Keep
provider identifiers, private URLs, credentials and raw receipts out of Git.
Record each qualified cell and every unmet gate before considering stage 004.

## CUDA image scope

The first candidate needs the native `qsim_cuda` module for selected
probabilities. Build it from the pinned qsim source for the RTX 3060 target.
The discovery wheel also contains a cuStateVec module, which links
`libcustatevec.so.1` and `libcublas.so.12`. Leave that backend out of the
candidate because its sampling cells did not qualify. Check the candidate's
ELF dependencies and Python imports in CI. On the allocated device, require
the requested CUDA module, a successful driver initialization and a completed
kernel before accepting the artifact.

## Integration tests

Use the existing submission transaction to persist the server-selected profile and test that idempotent replays keep it. Exercise the worker's real request, simulation and result path with the two frozen circuits. Check OpenQASM 2 and 3 bit order, requested basis ordering, finite probabilities and the complete downloadable NPZ. Include invalid device, insufficient memory, wrong image, worker restart and interrupted-retirement cases.

Record build and release changes alongside the tests that enforce them. The [hosted image check](../../../tools/ci/check-release.sh) owns the package evidence. The [controller](../../../qsim/controller.py) owns placement and retirement. The [release verifier](../../../tools/cd/acceptance.py) and [native probe](../../../qsim/release_probe.py) must agree on the admitted profile, worker digest, native job, device assignment and cleanup before accepting a GPU cell.

## Isolation and failure handling

- Select placement with a SkyPilot resource request. Verify physical device capacity, free memory and actual allocation at startup. Check scheduler reservations and physical occupancy separately on time-shared devices.
- Apply the larger address-space bound only to the verified GPU child. Retain the CPU process bound, output cap, sanitized environment and unprivileged child. Recheck actual usable CUDA memory and conservative workspace estimates before execution.
- Keep GPU jobs in a separate pool identity keyed by the worker digest and immutable resource profile. A retry keeps its admitted profile. Leave the job queued on unavailable capacity or report a bounded failure. Record an unexpected CPU execution as a failed GPU attempt.
- Derive eligibility from authenticated verifier identity and server-validated canonical fixture content. Reject user-supplied backend, image and resource overrides. Verify the resulting artifact and native placement separately from the callback status.

## Release and backout

Promote only the test artifacts from eligible main CI. Use the existing release lock and admission hold. Keep the previous verified images and private encrypted backup locator available. Complete GPU and CPU acceptance through the verifier, including proof that all owned native resources were retired. Retain transient, failed and completed receipts for the exact candidate SHA.

If the GPU profile fails, stop new jobs on that profile. Let the controller reconcile or cancel only its owned jobs through SkyPilot. Preserve the failed receipt and apply the standard release recovery procedure with the prior verified CPU artifact identities and acceptance. Treat image rollout, verified acceptance and reopened admission as separate checkpoints. [Recovery procedure](../../../docs/cd-recovery.md)
