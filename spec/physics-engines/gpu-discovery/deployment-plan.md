# Local qsim CUDA deployment plan

## Decision and scope

The first accelerator candidate is native qsim CUDA selected-basis probabilities on one 12 GiB RTX 3060. Use the frozen 26-qubit, depth-60 fixture and its 24-qubit, depth-20 control for qualification. The four-cell follow-up shows how the selected-probability timings changed with circuit depth on the same hardware. Collect a second seed and service-level timings before defining a broader routing rule. [Discovery results](results.md)

The production canary should send only the dedicated release verifier's fixed fixture to a GPU profile. Existing public job routing uses the CPU profiles while that canary runs. GPU selection stays with the server. Keep the current API request and artifact formats, precision, bit order and probability tolerances.

## Implementation sequence

| Stage | Change | Required evidence |
| --- | --- | --- |
| Discovery | Reuse the pinned wheel and add the 12 GiB fixture profile. Collect both same-worker comparisons with independent receipt capture. | Each case has 20 correct pairs, native CUDA identity, usable and sampled device memory, median and p90. The candidate meets the approved speed threshold with its paired confidence interval. The worker is absent after cleanup. |
| Package | Build a separate CUDA worker image from pinned qsim source, CUDA toolkit, and runtime libraries. Reuse the existing QASM parser and probability artifact writer. Add an internal, immutable GPU execution profile and allow it only for the verifier's frozen circuit and output shape. | CPU worker, API and controller continue to pass existing CI. Hosted CI validates the new image’s source labels, native imports, dependency resolution, and artifact contents. A worker presented with a GPU profile rejects an image, backend, device or fixture mismatch. |
| Stage | Publish the verified CI-built image by digest. Launch the exact image through SkyPilot on one RTX 3060, with eight vCPUs, at least 16 GB RAM and 30 GB ephemeral storage. | One visible RTX 3060, 12 GiB physical VRAM, sufficient free CUDA memory, matching image and native module, synchronized kernel execution, and passing QASM/NPZ checks on the frozen cases. Record complete wall time, peak RSS and VRAM, timeout behavior, and confirmed cleanup. |
| Release canary | Extend the image build, archive validation and release verifier to recognize the separate GPU artifact and the fixed GPU acceptance cell. Pin the accepted image and resource profile in the persisted dispatch context. Allow one GPU canary job during the release hold for the configured smoke identity. | Exact-main artifact identity, fresh encrypted backup and readback, all existing CPU cases, and the new GPU case with its live native job identity and teardown evidence. Write the verified release record before reopening admission. [Release procedure](../../../docs/releases.md) |
| Extend | Repeat the 24- and 26-qubit matched-depth matrix with at least two seeds per cell in a common circuit dialect and enough CPU/GPU pairs to establish the crossover. Measure end-to-end latency with cold provisioning and with an already-ready worker. | Specify an operation and circuit envelope supported by end-to-end evidence before opening the new profile to public jobs. Record queue and scheduling behavior under one-worker capacity. |

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
