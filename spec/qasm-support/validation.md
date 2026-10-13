# Validation record

Date: 2026-10-13. Local Python 3.12.13, x86_64; pinned Clifft 0.11.0, qsimcirq 0.22.1 and Cirq 1.7.0. No production submissions or deployment were performed.

## Results

- `bash tools/ci/check.sh`: 220 Python tests and three console tests passed. One existing Starlette/httpx deprecation warning remains.
- Actual worker image built from `deploy/Dockerfile`, worker target, with unchanged frozen dependencies.
- `bash tests/check_worker_image.sh wqs-worker:qasm-expanded-local`: passed startup followers, minimal package imports, credential-free module execution, both engines, custom QASM2 sampling with a 63-bit seed, bound QASM3 sampling and bound QASM3 probabilities.
- Seven documentation examples passed through authenticated submission, actual isolated worker execution, callback persistence and protected result download.
- Six dynamic-circuit cases cover reset of an entangled state before any measurement, collapse, repeated writes and independent shots. Classical conditions explicitly fail.
- Security checks demonstrate rejection before parsing for unsafe register sizes, conditions, exponent expressions and include names; native record cells share the output budget with final classical memory. Invalid numeric expressions produce safe failures without native warning output.
- Historical request/hash fixtures remain unchanged, including generic/dedicated route replays, explicit defaults, Unicode names and both engines. Empty bindings do not change prior request identities.

The 220-test local result includes the corrected entangled-reset capability fixture pending the owner's decision on PR #8's review-round limit. The feature PR independently includes its own passing entangled-reset worker test. Until that correction is pushed, the feature PR's committed full suite contains 219 Python tests. This distinction is intentional; no unpushed correction is represented as a reviewed commit.

## Corrective findings

Independent review found and fixed the native 32-bit integer seed restriction: large allowed API seeds now initialize a native RandomState using both 32-bit words; small seeds retain their original native integer path. Actual worker tests prove repeatability at 2**32 and 2**63-1, distinguish high bits from truncation and preserve seed 42 behavior.

Adversarial tests found NumPy parser warnings exposing internal paths. Scoped numeric error handling now converts these into safe failures; assertions were retained. The automated PR #8 finding about a prematurely measured Bell pair is corrected locally and awaits the owner's review-limit direction.

## Callback-size review correction

The owner authorized an additional PR #10 review after round two found that wide classical registers could exceed the callback summary limit. QASM counts and preview rows now each have a 512 KiB JSON budget, with complete entries and explicit truncation flags. The NPZ artifact retains all shots. A final serialized-summary check reports a safe resource-limit failure if metadata alone exceeds the callback limit.

Four regressions run actual QASM2/QASM3 workers, submit their callbacks, verify terminal success and download complete artifacts. They cover one shot across 999,998 classical bits and 24 shots across 40,000 bits. Full local CI passed 228 Python tests and three console tests. The rebuilt worker image checks passed. The committed suite contains 227 Python tests; the separate PR #8 capability correction remains local pending its review decision.

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

Implementation uses separate PRs based on the specification and foundation branches. Standpunkt-first CI and automatic review remain required before merge. The owner merges. TASK-010 remains pending those merges; no deployment handoff is marked complete.

Deployment must publish the approved worker image through the private baseline and ensure compatible worker selection before enabling the new API/controller code. Backout must close new admission and preserve capable workers until accepted jobs drain. No private configuration, public production hostnames or registry credentials are recorded here.

Deferred: classical conditions, arbitrary QASM3 control flow, unsized/other input types, new-path exponent expressions, and incremental sample streaming. QASM2 probability semantics stay on their legacy path. New QASM2 sampling and QASM3 operations use the validated adapter.
