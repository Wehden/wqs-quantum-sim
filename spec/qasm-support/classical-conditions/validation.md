# Aer implementation validation

Date: 2026-10-14. Source baseline: `2ed33601e9fdd5ab8ba04ef98c831486dbf9205c`. This record covers local implementation evidence. No deployment or live SkyPilot jobs were performed.

## Source and PR boundaries

- Foundation: PR #12 adds pinned worker dependencies, capability evidence and approved specifications. Existing engine pins are unchanged.
- Adapter/worker: PR #13 adds structural input validation, native Aer execution, final-memory mapping and isolated packaging. It is stacked on #12.
- API/docs/CI: the follow-on branch adds explicit Aer sampling admission, examples and required image coverage. It is stacked on #13.

The owner merges these PRs. Each PR needs green exact-head CI and completed automatic review before being called clear. Source API admission is implemented; production admission remains unchanged.

## Focused verification

- Foundation: 249 Python and three console tests; 39 focused native capability checks; baseline real worker-image tests passed.
- Import and native execution: 37 tests passed, covering pre-conversion rejection, budgets, final memory, latest writes, skipped measurements, X/Y/Z teleportation checks and full-width seed repeatability/discrimination.
- Worker/lifecycle/dispatch: 24 tests passed; no controller resource or worker-pool policy changes.
- API/identity/ownership: 74 tests passed, including generic/dedicated replay, engine conflicts, denied cross-owner reads and complete callback/artifact retrieval.
- Documentation examples: all 15 combined old/new example checks passed; six new Aer examples execute through API, isolated worker and protected download.
- Acceptance: four tests passed, including a 24-shot by 40,000-bit artifact with truncated JSON preview, safe accepted-job failure and explicit no-fallback behavior.
- CI gate tests: four passed. Standpunkt-first routing and the separate required hosted image job are preserved; labels now identify three engines.

Final local CI passed 310 Python tests and three console tests. The rebuilt worker image passed the complete startup/isolation/three-engine check. One pre-existing Starlette/httpx deprecation warning remains. GitHub exact-head receipts and automatic review are separate PR readiness gates.

## Actual isolated image evidence

A locally built worker image ran 2- and 26-qubit entangled QASM2 circuits with the production child entry point and restricted environment, network disabled. Both completed without stderr. The child retained its 3 GiB address-space bound. The 2-qubit case used 130,192 KiB peak RSS and 1.074 seconds wall time; the 26-qubit case used 1,158,764 KiB peak RSS and 6.071 seconds. These are single local observations, not a latency guarantee or proof that every 26-qubit workload fits. Aer uses one native thread in the unchanged allocation.

The packaged image check now includes QASM2 and QASM3 Aer conditions, a 63-bit seed, and all prior Clifft/qsim cases. It tests module isolation and rejects copying service/store authority into the worker image. The foundation footprint is recorded separately in implementation-decisions.md; image size is not process RSS or a registry transfer size.

## Legacy timing regression

Ten alternating baseline/candidate worker subprocess runs per engine used the same frozen environment and circuit. Alternating order reversed on successive iterations; no local image build or full test run overlapped this measurement. This includes native import/startup, not SkyPilot dispatch. The acceptance threshold was fixed at max(20% of baseline median, 0.1 seconds) added median time.

| Engine | Baseline median seconds | Candidate median seconds | Result |
| --- | --- | --- | --- |
| clifft | 0.050104 | 0.050478 | Passed |
| qsim | 0.655796 | 0.653228 | Passed |

Raw timing evidence:

```json
{
  "baseline_revision": "2ed33601e9fdd5ab8ba04ef98c831486dbf9205c",
  "circuit": "OPENQASM 2.0; include \"qelib1.inc\"; qreg q[2]; ry(0.3) q[0]; rx(0.7) q[1]; cx q[0],q[1];",
  "engines": {
    "clifft": {
      "samples": {
        "baseline": [
          0.052540682999961064,
          0.05072819199995138,
          0.04987932499989256,
          0.05081982599995172,
          0.04974842699994042,
          0.050024790999941615,
          0.04955153500009146,
          0.050184112000124514,
          0.050220282000054794,
          0.049938296999926024
        ],
        "candidate": [
          0.051046124000095006,
          0.050618734999943626,
          0.05042051000009451,
          0.05053535800016107,
          0.04911731300012434,
          0.050881752999885066,
          0.05011098399995717,
          0.048958923000100185,
          0.049592833000133396,
          0.051206768000156444
        ]
      },
      "medians": {
        "baseline": 0.050104451500033065,
        "candidate": 0.05047793400012779
      },
      "allowed_added_seconds": 0.1,
      "passed": true
    },
    "qsim": {
      "samples": {
        "baseline": [
          0.6559227239999927,
          0.656057889000067,
          0.6527505999999903,
          0.6755277739998746,
          0.6569328960001712,
          0.6492846890000692,
          0.6556690840000101,
          0.6443038239999623,
          0.6842885369999294,
          0.6507876680000209
        ],
        "candidate": [
          0.6426463640000293,
          0.6491670759999124,
          0.6740299290001985,
          0.6620218589998785,
          0.6360164929999428,
          0.6511447699999735,
          0.6549534470000253,
          0.6515025520000108,
          0.6669008399999257,
          0.6611362689998259
        ]
      },
      "medians": {
        "baseline": 0.6557959040000014,
        "candidate": 0.6532279995000181
      },
      "allowed_added_seconds": 0.1311591808000003,
      "passed": true
    }
  }
}
```

## Remaining gates

Automatic review and exact-head CI receipts remain required for the implementation stack. TASK-019 remains pending owner merges and a published approved image identity. Local Docker IDs are not deployable registry digests. The release/CD effort and original TASK-010 handoff remain separate; none is claimed complete here.

After separate deployment authorization, verify compatible workers before Aer admission, exercise live positive/negative smoke cases, and verify stored results. Backout must close new Aer admission and drain accepted Aer jobs on capable workers. No implementation validation justifies restarting or terminating existing work.

## Reconciliation, 2026-10-14

The owner merged #12–#14 and explicitly accepted the last documentation nits. PR-head CI receipts: [#12](https://github.com/Wehden/wqs-quantum-sim/actions/runs/38658274994), [#13](https://github.com/Wehden/wqs-quantum-sim/actions/runs/38658276361), [#14](https://github.com/Wehden/wqs-quantum-sim/actions/runs/38659232690). Each passed Standpunkt tests and hosted worker-image checks; hosted test fallback was skipped. These receipts supersede the pending PR gates above, not the release gate.

Important merge topology: #12 reached main as `30692108986d0067604dc15db6183eb41ab0e308`; #13 merged into the foundation branch as `ec4da0359dd835804c99d1fbc73a0f1bfdc0812d`; #14 merged into the adapter branch as `a0f7662beb9adc57696437635ea1e754cbceecbb`. Neither later merge was in main at reconciliation. Main `8626c94eb2ed932f6b3201306e1514e218654c06` has green [CI](https://github.com/Wehden/wqs-quantum-sim/actions/runs/38661020514), including #15/#16, but that does not establish integrated Aer coverage. A follow-up integration PR preserves those dependency updates and brings in the reviewed stack. TASK-019 remains incomplete; no registry digest or live acceptance is claimed.
