# Validation record

Date: 2026-10-13. Local Python 3.12.13, x86_64; pinned Clifft 0.11.0, qsimcirq 0.22.1 and Cirq 1.7.0. No production submissions or deployment were performed.

## Merged source and CI status (2026-10-14 UTC)

PRs #8–#11 are merged. The verified main revision is `2ed33601e9fdd5ab8ba04ef98c831486dbf9205c`.

| PR | Merge revision | Scope |
| --- | --- | --- |
| [#8](https://github.com/Wehden/wqs-quantum-sim/pull/8) | `2f35a6e67e724df53f8ac59763ecfbab526e86d8` | Specification and capability probes |
| [#10](https://github.com/Wehden/wqs-quantum-sim/pull/10) | `55c7c6827a31cd98fe79ff24bae324132fee6068` | QASM features, merged into #9's foundation branch |
| [#9](https://github.com/Wehden/wqs-quantum-sim/pull/9) | `2eab6f04def477934a52ef18dc8627eb28d61dd3` | Foundation and #10 features merged into main |
| [#11](https://github.com/Wehden/wqs-quantum-sim/pull/11) | `2ed33601e9fdd5ab8ba04ef98c831486dbf9205c` | Required worker-image CI and corrected reset fixture |

[Main push CI 38493150281](https://github.com/Wehden/wqs-quantum-sim/actions/runs/38493150281) passed on that exact revision. Both-engine tests ran on Standpunkt; the GitHub-hosted test fallback was skipped. The separate required packaged-worker-image check ran successfully on GitHub-hosted infrastructure. The final CI gate passed.

The reset capability correction is committed in #11: `test_qsim_reset_entangled_qubit_before_any_measurement` resets the entangled qubit before measuring either partner. The earlier measured-partner case remains separately named. The final local suite recorded 235 Python tests and three console tests. The counts below describe earlier checkpoints and are not fresh test runs from this reconciliation.

This verifies merged source and CI only. Approved image publication, compatible warm-worker selection, deployment and live SkyPilot acceptance remain rollout gates. This reconciliation performed no production checks or submissions.

## Historical results (2026-10-13)

- `bash tools/ci/check.sh`: 220 Python tests and three console tests passed. One existing Starlette/httpx deprecation warning remains.
- Actual worker image built from `deploy/Dockerfile`, worker target, with unchanged frozen dependencies.
- `bash tests/check_worker_image.sh wqs-worker:qasm-expanded-local`: passed startup followers, minimal package imports, credential-free module execution, both engines, custom QASM2 sampling with a 63-bit seed, bound QASM3 sampling and bound QASM3 probabilities.
- Seven documentation examples passed through authenticated submission, actual isolated worker execution, callback persistence and protected result download.
- Six dynamic-circuit cases cover reset of an entangled state before any measurement, collapse, repeated writes and independent shots. Classical conditions explicitly fail.
- Security checks demonstrate rejection before parsing for unsafe register sizes, conditions, exponent expressions and include names; native record cells share the output budget with final classical memory. Invalid numeric expressions produce safe failures without native warning output.
- Historical request/hash fixtures remain unchanged, including generic/dedicated route replays, explicit defaults, Unicode names and both engines. Empty bindings do not change prior request identities.

At this checkpoint, the 220-test local result included an unpushed entangled-reset capability correction; the committed suite contained 219 Python tests. The feature PR also had an independent entangled-reset worker test. PR #11 subsequently committed the capability correction, as recorded above.

## Corrective findings

Independent review found and fixed the native 32-bit integer seed restriction: large allowed API seeds now initialize a native RandomState using both 32-bit words; small seeds retain their original native integer path. Actual worker tests prove repeatability at 2**32 and 2**63-1, distinguish high bits from truncation and preserve seed 42 behavior.

Adversarial tests found NumPy parser warnings exposing internal paths. Scoped numeric error handling now converts these into safe failures; assertions were retained. The automated PR #8 finding about a prematurely measured Bell pair was corrected locally at this checkpoint and subsequently merged in PR #11.

## Callback-size review correction

The owner authorized an additional PR #10 review after round two found that wide classical registers could exceed the callback summary limit. QASM counts and preview rows now each have a 512 KiB JSON budget, with complete entries and explicit truncation flags. The NPZ artifact retains all shots. A final serialized-summary check reports a safe resource-limit failure if metadata alone exceeds the callback limit.

Four regressions run actual QASM2/QASM3 workers, submit their callbacks, verify terminal success and download complete artifacts. They cover one shot across 999,998 classical bits and 24 shots across 40,000 bits. Full local CI passed 228 Python tests and three console tests. The rebuilt worker image checks passed. At this checkpoint, the committed suite contained 227 Python tests and the capability correction was still local; PR #11 later merged it.

## Operation-specific shot limits

The owner authorized correction of the next PR #10 finding: probability requests carried an API-default shot count into register limits. Preparation now applies the submitted shot multiplier only to sampling; probability preparation retains the unscaled register-allocation bound. Two authenticated API-to-worker regressions verify that a 1,001-bit unused register succeeds for probabilities and exceeds the sampling budget at the default 1,000 shots. Both callbacks reach their expected terminal states; the probability artifact contains the expected result.

Full local CI passed 230 Python tests and three console tests. The rebuilt worker image checks passed. At this checkpoint, the committed suite contained 229 Python tests while the capability correction was still local; PR #11 later merged it.

## Binding submission bounds

The owner authorized correction of the binding-size finding. Both submission schemas share a bounded binding type: at most 256 entries and names of 1–256 characters. The before-validator checks these bounds before copying or normalizing values; OpenAPI publishes the same limits. Both public guides describe them.

Five regressions cover oversized counts, oversized/empty names, exact boundary acceptance, preserved numeric normalization and generated schemas. Rejected requests leave the jobs table empty and do not echo circuit or binding content. Full local CI passed 235 Python tests and three console tests after correcting a test-only schema lookup. At this checkpoint, the committed suite contained 234 Python tests; PR #11 later merged the remaining capability correction. Worker code and image packaging were unchanged by this correction.

## Legacy timing check

Ten alternating baseline/candidate subprocess runs per engine used the same two-qubit unitary rotation/entangling circuit and frozen environment. Baseline source is origin/main at 0e850ec. The predeclared acceptance threshold is added median time no greater than max(20% of baseline, 0.1 seconds). This checks local subprocess runtime including import overhead, not production SkyPilot dispatch or throughput. A local image build also ran during part of this check; alternating runs limit, but do not eliminate, machine-load noise.

| Engine | Baseline median seconds | Candidate median seconds | Allowed added seconds | Result |
| --- | --- | --- | --- | --- |
| clifft | 0.058212 | 0.051501 | 0.100000 | Passed |
| qsim | 0.662432 | 0.657397 | 0.132486 | Passed |

Raw wall-time samples in seconds:

```json
{
  "clifft": {
    "baseline": [
      0.060445787000162454,
      0.057975001999921005,
      0.05687940899997557,
      0.0635074589999931,
      0.059701810000206024,
      0.05844824700011486,
      0.0575826589999906,
      0.05575470099984159,
      0.05705048300001181,
      0.0631644210000104
    ],
    "candidate": [
      0.05204306799987535,
      0.04963978099999622,
      0.05326244600018981,
      0.05379146700011006,
      0.050962613999899986,
      0.05204028399998606,
      0.04938074099982259,
      0.0498486869998942,
      0.04960481500006608,
      0.05314059499983159
    ]
  },
  "qsim": {
    "baseline": [
      0.7123719369999435,
      0.6723609640000632,
      0.686639679000109,
      0.6729093710000598,
      0.6573540890001368,
      0.6505896200001189,
      0.6491965449999952,
      0.6563326569998935,
      0.65018360199997,
      0.6675094839999929
    ],
    "candidate": [
      0.6710122520000823,
      0.6546251359998223,
      0.6586810799999512,
      0.668218976000162,
      0.6483677969999917,
      0.6555295470000146,
      0.6378390659999695,
      0.6561131720000049,
      0.6721782980000626,
      0.6931077369999912
    ]
  }
}
```

## Release boundary

The specification, foundation, features and CI corrections are merged through PRs #8–#11. TASK-010 remains open for the rollout handoff, including the release record, approved image identity, compatible-worker admission and drain-first rollback procedure. It is no longer blocked on those merges. No deployment handoff or live acceptance is marked complete.

Deployment must publish the approved worker image through the private baseline and ensure compatible worker selection before enabling the new API/controller code. Backout must close new admission and preserve capable workers until accepted jobs drain. No private configuration, public production hostnames or registry credentials are recorded here.

Deferred: classical conditions, arbitrary QASM3 control flow, unsized/other input types, new-path exponent expressions, and incremental sample streaming. QASM2 probability semantics stay on their legacy path. New QASM2 sampling and QASM3 operations use the validated adapter.
