# QASM rollout handoff

TASK-010 and classical-conditions TASK-019 are complete for the supported CPU
subsets. PRs #8–#11 integrated the qsim importer and sampling path. PR #18
brought the Aer stack from #12–#14 onto main. The merged revisions are ancestors
of the verified release below. [qsim implementation evidence](validation.md) ·
[Aer implementation evidence](classical-conditions/validation.md)

## Verified release

On 2026-11-25, the release ledger and deployed roles matched #84, source
`42a52154a9070c73eb16dc5820c402bc6726eeec`. The exact-main
[CI run](https://github.com/Wehden/wqs-quantum-sim/actions/runs/45327906729)
passed local tests and hosted image checks. The release verifier accepted
worker digest `sha256:45df5a21a4acf21dc60c523b0474d58fb1353fdf4483d5c7f534590c032f9b87`.

The first CD attempt failed after the encrypted backup and during live
acceptance. Recovery used the matching deployed worker after the
[SkyPilot worker pin](https://github.com/Wehden/laminar/pull/236) and reran the
complete 21-case verifier. All eight circuit cases passed, including qsim QASM2 and
QASM3 sample/probability fixtures, and Aer reset with classical feed-forward in
both formats. The receipt records complete numeric results, protected
artifacts, actual native jobs and cleanup. Admission was open with zero pending
jobs on readback. [CD attempt and receipt replay](https://github.com/Wehden/wqs-quantum-sim/actions/runs/45329948675)

## Capability disposition

| Target | Released and verified scope |
| --- | --- |
| Classical registers and measurement | qsim and Aer sampling preserve classical destinations and final memory. |
| Reset | qsim and Aer sampling. Entangled reset, mid-circuit measurement and shot independence are covered by the worker tests. |
| Classical conditions | Explicit Aer on documented QASM2 and QASM3 sampling forms. qsim and Clifft keep their documented engine limits. |
| Custom gate declarations | Bounded qsim QASM2 sampling and qsim QASM3 paths. |
| Parameter binding | Standard gate parameters and supported QASM3 sized input bindings on qsim, with exact request and idempotency validation. |
| OpenQASM 3 | qsim probabilities and sampling, plus explicit Aer sampling in its documented subset. |

The public guides and authenticated capabilities response define supported
combinations. The deployed capabilities endpoint returned HTTP 200 for the
release identity and listed all six supported engines. Keep arbitrary control flow, unsupported input types and
incremental measurement streaming outside this release. The pinned tests
exercise custom gates, bindings, entangled reset and teleportation-style
correction beyond the small live smoke circuits.

## Incremental measurement streaming

On 2026-11-25, a separate CPU check exercised upstream Clifft 0.14.0 with a
seeded Bell circuit. Its documented Python sampler returned a completed
`SampleResult` with 128 rows. Repeating the 128-shot call reproduced the array,
while concatenating two 64-shot calls with the original seed yielded a different
array. WQS retains progress and final-result events. Incremental measurement
delivery still needs a native streaming interface or a chunk contract that
defines seed semantics and reproduces the final artifact across retries.
[Upstream sampling guide](https://unitaryfoundation.github.io/clifft/stable/guide/simulation/)

## Promotion and backout

Build and validate the worker alongside the exact-main API and controller in
CI. The existing [release flow](../../docs/releases.md) publishes the
compatible worker digest before applying it to the controller. Hold new
admission and drain accepted jobs before a release switches worker images.
Verify installed images, all circuit and physics smoke cases, complete native
results and resource cleanup before reopening admission.

For backout, preserve the accepted jobs and their capable worker until they
drain. Reconcile the prior verified artifact identities and follow the
[release recovery procedure](../../docs/cd-recovery.md). Preserve the encrypted
backup locator, owner data and release receipt. Use the same verifier to
validate the restored service before opening admission.
