# Implementation tasks: classical conditions

Status: **approved by the owner on 2026-10-14; implementation authorized**. Design and requirements amendment approved on the same date. TASK-011 dependency/capability evidence passed: 249 Python and three console tests, plus baseline worker-image checks. Follow-on tasks are pending.

Prepared against the System2 1.1.1 task-planner contract. Parent scope: [design](design.md), [requirements delta](requirements-delta.md), and [existing tasks](../tasks.md). Research evidence remains separate from implementation acceptance.

## Task Graph Overview (short)

TASK-011 dependencies/contracts → TASK-012 import safety → TASK-013 native execution → TASK-014 isolated worker/image → TASK-015 API admission → TASK-016 CI/image and TASK-017 docs/examples in parallel → TASK-018 combined verification → TASK-019 release handoff.

The initial chain is deliberately sequential: safety and native execution share the new adapter, and API admission depends on a working compatible image. TASK-016 and TASK-017 have disjoint leases and can run concurrently once authorized. No other concurrent writers to a listed file. Independent read-only review can accompany implementation without expanding write authority.

## Tasks (the full list)

### TASK-011: Resolve dependency and worker contracts

- **Goal:** Resolve dependency and worker contracts.
- **Dependencies:** None beyond task-plan approval.
- **Recommended Mode:** executor.
- **Risk:** High: isolated research dependencies do not prove combined-image compatibility.
- **Files/areas:** `pyproject.toml`, `uv.lock`, `tests/test_aer_capabilities.py`, `spec/qasm-support/classical-conditions/implementation-decisions.md`. New Aer test/report paths are proposed.
- **Steps:** Add the approved pinned Qiskit family to the worker extra and resolve the combined frozen environment without upgrading existing engine pins. Port the research feasibility cases into executable capability tests. Inventory transitive licenses and image footprint. Inspect current CPU allocation, child environment and image-keyed reuse; record a bounded native-thread value that fits the existing allocation and the implementation transport. Do not change CPU/memory requests. Record which prerequisites are proven and which require TASK-014 image evidence. New resource/configuration contracts or incompatible pins return to design review.
- **Verification:** Run frozen dependency synchronization, uv pip check, focused tests/test_aer_capabilities.py and existing tests/test_qasm_capabilities.py. Require all three engine imports and the 12 researched cases to pass. Preserve exact existing Clifft/qsim/Cirq versions. Validate that package metadata imports used by API remain native-library-free.
- **Rollback / Backout:** Revert dependency changes and keep Aer admission absent.
- **Change budget:** max_files=4, max_new_symbols=0, interface_policy=none. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^pyproject\.toml$
^uv\.lock$
^tests/test_aer_capabilities\.py$
^spec/qasm\-support/classical\-conditions/implementation\-decisions\.md$
```

### TASK-012: Bound Aer imports and validate the conditional subset

- **Goal:** Bound Aer imports and validate the conditional subset.
- **Dependencies:** TASK-011.
- **Recommended Mode:** executor.
- **Risk:** High: hostile source can allocate before post-parse validation.
- **Files/areas:** `qsim/aer_adapter.py`, `tests/test_aer_import.py`, `tests/test_aer_security.py`. New Aer test/report paths are proposed.
- **Steps:** Create the one approved adapter module with lazy imports. Implement QASM2 lexer preflight and QASM3 AST validation before maintained conversion. Enforce exact includes, literal register bounds, numeric/source limits, flat equality conditions and approved standard gate allowlists. Reject nested controls, loops, custom gates, bindings, aliases, annotations and other excluded constructs. Account conservatively for both branches and all measurement events. Validate native circuits without evaluating branch decisions. Keep the module unreachable from public admission.
- **Verification:** Run tests/test_aer_import.py and tests/test_aer_security.py. Test exact/over limits, malformed and oversized numeric literals, include path attempts and out-of-range condition constants. Instrument importer entry/allocation boundaries so over-limit inputs demonstrably reject before conversion/allocation. Assert every unsupported construct fails safely; import success alone is insufficient.
- **Rollback / Backout:** Remove the unexposed adapter and tests; leave existing paths untouched.
- **Change budget:** max_files=3, max_new_symbols=24, interface_policy=extend-only. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^qsim/aer_adapter\.py$
^tests/test_aer_import\.py$
^tests/test_aer_security\.py$
```

### TASK-013: Execute native conditions and normalize final memory

- **Goal:** Execute native conditions and normalize final memory.
- **Dependencies:** TASK-012.
- **Recommended Mode:** executor.
- **Risk:** High: bit order, skipped writes and shot state must be correct.
- **Files/areas:** `qsim/aer_adapter.py`, `tests/test_aer_sampling.py`, `tests/test_aer_dynamic.py`. New Aer test/report paths are proposed.
- **Steps:** Implement the approved sample(request, *, cpu_threads) boundary using fixed CPU double-precision Aer statevector execution and maintained transpilation. Validate post-transpile instructions/budgets and stable classical mapping. Normalize raw memory via original register metadata and find_bit. Exercise both branches, partial/unwritten memory, latest writes, register broadcast, conditional reset and skipped measurement. Preserve complete shot memory and all seed bits; use native control only.
- **Verification:** Run tests/test_aer_sampling.py and tests/test_aer_dynamic.py plus TASK-012 tests. Prove deterministic asymmetric register mapping, independent shots, teleportation corrections in X/Y/Z bases, repeated-seed behavior at 0, 2**32 and 2**63-1, and high-bit discrimination. Use predeclared analytic criteria and the approved 0.05 distribution tolerance at 4,096 shots. No cross-engine equal-seed requirement.
- **Rollback / Backout:** Revert native execution while admission remains absent.
- **Change budget:** max_files=3, max_new_symbols=10, interface_policy=extend-only. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^qsim/aer_adapter\.py$
^tests/test_aer_sampling\.py$
^tests/test_aer_dynamic\.py$
```

### TASK-014: Integrate isolated workers and compatible packaging

- **Goal:** Integrate isolated workers and compatible packaging.
- **Dependencies:** TASK-013.
- **Recommended Mode:** executor.
- **Risk:** High: mixed images or native resource defaults could violate isolation.
- **Files/areas:** `qsim/worker.py`, `qsim/controller.py`, `deploy/Dockerfile`, `tests/test_worker_lifecycle.py`, `tests/test_dispatch.py`, `tests/test_aer_worker.py`. New Aer test/report paths are proposed.
- **Steps:** Package the adapter and frozen dependencies in the minimal worker target. Add explicit Aer dispatch and reuse the existing result/callback path. Pass the verified bounded native-thread setting through the restricted child environment or established internal arguments; no client-controlled options. Confirm existing image-keyed warm-worker selection, changing controller code only if required by the approved contract. Keep API admission absent. Measure startup/import/peak memory under the actual 3 GiB child limit; do not relax resource bounds to pass.
- **Verification:** Run tests/test_aer_worker.py, tests/test_worker_lifecycle.py and tests/test_dispatch.py. Build the real worker target and execute the credential-free child, proving API/controller credentials absent, fixed method/thread settings, callback/NPZ completeness, safe failures and incompatible-image exclusion. Test representative and boundary workloads against current resource limits; proposed lower Aer qubit admission or new resource contracts must return for review before TASK-015.
- **Rollback / Backout:** Revert worker integration before admission; retain previous image references. No deployed worker changes in this task.
- **Change budget:** max_files=6, max_new_symbols=8, interface_policy=extend-only. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^qsim/worker\.py$
^qsim/controller\.py$
^deploy/Dockerfile$
^tests/test_worker_lifecycle\.py$
^tests/test_dispatch\.py$
^tests/test_aer_worker\.py$
```

### TASK-015: Expose explicit Aer sampling and preserve durable identity

- **Goal:** Expose explicit Aer sampling and preserve durable identity.
- **Dependencies:** TASK-014, including resource/image evidence.
- **Recommended Mode:** executor.
- **Risk:** High: admission and canonicalization affect durable requests.
- **Files/areas:** `qsim/models.py`, `qsim/api.py`, `qsim/qasm_adapter.py`, `tests/test_service.py`, `tests/test_transactions.py`, `tests/test_aer_api.py`. New Aer test/report paths are proposed.
- **Steps:** Add aer to existing strict schemas and dependency-free capabilities, admitting only the approved QASM2/QASM3 sample combinations. Keep default Clifft, all old identities and old error contracts. Reject Aer Stim/probabilities, sampling-inapplicable fields and nonempty bindings at envelope validation where possible. Include engine in new identity, expose precise subset/version claims and retain incremental_samples=false. Native parsing stays in the child.
- **Verification:** Run tests/test_aer_api.py, tests/test_service.py and tests/test_transactions.py. Verify generic and dedicated routes, legacy canonical bytes, replay and changed-engine conflicts, owner isolation, complete callback/result lifecycle and safe syntax failures. Confirm importing API/capability metadata imports no Qiskit/Aer/native library. Admission source changes do not authorize production rollout.
- **Rollback / Backout:** Revert unshipped admission. If already deployed under a later authorization, close new Aer admission and drain accepted jobs on compatible workers before reverting.
- **Change budget:** max_files=6, max_new_symbols=6, interface_policy=extend-only. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^qsim/models\.py$
^qsim/api\.py$
^qsim/qasm_adapter\.py$
^tests/test_service\.py$
^tests/test_transactions\.py$
^tests/test_aer_api\.py$
```

### TASK-016: Extend required CI and packaged-image acceptance

- **Goal:** Extend required CI and packaged-image acceptance.
- **Dependencies:** TASK-015.
- **Recommended Mode:** test-engineer.
- **Risk:** Med: passing source tests can hide missing native image dependencies.
- **Files/areas:** `.github/workflows/ci.yml`, `tools/ci/check.sh`, `tools/ci/check-worker.sh`, `tests/check_worker_image.sh`, `tests/test_ci_route.py`, `docs/ci.md`. New Aer test/report paths are proposed.
- **Steps:** Ensure the existing full suite includes all Aer tests and reports all three engines. Extend real image checks with conditional Aer circuits and isolated imports while preserving Clifft/qsim cases. Preserve Standpunkt-first tests, hosted test fallback and the separately required hosted image check. Update runner-path descriptions and gate tests if labels/outputs change; do not add availability tokens or replace routing. Avoid workflow changes where existing discovery already covers the new tests.
- **Verification:** Run bash tools/ci/check.sh and bash tools/ci/check-worker.sh where Docker is available; run focused tests/test_ci_route.py after any workflow changes. Require actual three-engine execution and failure/cancelled/skipped image-gate cases. Record actual runner and skipped fallback paths in the PR. CI failure is reported to owner.
- **Rollback / Backout:** Revert only CI changes that are incorrect; do not call an untested Aer image release-ready.
- **Change budget:** max_files=5, max_new_symbols=4, interface_policy=none. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^\.github/workflows/ci\.yml$
^tools/ci/check\.sh$
^tools/ci/check\-worker\.sh$
^tests/check_worker_image\.sh$
^tests/test_ci_route\.py$
^docs/ci\.md$
```

### TASK-017: Document and exercise the new engine contract

- **Goal:** Document and exercise the new engine contract.
- **Dependencies:** TASK-015.
- **Recommended Mode:** executor.
- **Risk:** Med: public examples must match the bounded subset.
- **Files/areas:** `qsim/static/index.html`, `qsim/static/agents.md`, `README.md`, `tests/test_aer_examples.py`. New Aer test/report paths are proposed.
- **Steps:** Explain explicit Aer selection, its CPU execution and dependency provenance, condition syntax and retained exclusions. Include QASM2 register equality and QASM3 boolean/if-else examples, final-memory ordering and no-fallback behavior. Keep qsim/Clifft examples accurate; distinguish native sample completion from streaming. Add executable documentation examples through authenticated API, worker, callback and protected artifact retrieval.
- **Verification:** Run tests/test_aer_examples.py and existing tests/test_qasm_examples.py. Verify every new published example with the real isolated worker and assert its final memory. Check links and public files for internal hostnames, tokens or private deployment values. Do not publish website changes outside the authorized release process.
- **Rollback / Backout:** Revert incorrect examples/capability claims; retain existing engine documentation.
- **Change budget:** max_files=4, max_new_symbols=0, interface_policy=none. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^qsim/static/index\.html$
^qsim/static/agents\.md$
^README\.md$
^tests/test_aer_examples\.py$
```

### TASK-018: Close integration and security verification

- **Goal:** Close integration and security verification.
- **Dependencies:** TASK-016 and TASK-017.
- **Recommended Mode:** test-engineer.
- **Risk:** High: local feasibility is not service or runtime acceptance.
- **Files/areas:** `tests/test_aer_acceptance.py`, `spec/qasm-support/classical-conditions/validation.md`. New Aer test/report paths are proposed.
- **Steps:** Run the combined suite and actual image checks at the exact candidate revision. Add only missing cross-boundary assertions: hostile-input safety, no fallback, final-memory callback size/truncation, complete NPZ, idempotency and compatible workers. Record image/dependency provenance, memory/thread observations, CI runner path, known limitations and legacy timing comparison under the parent threshold. Production fixes return to the owning task with a renewed lease; this task does not patch arbitrary modules.
- **Verification:** Run focused tests/test_aer_acceptance.py, bash tools/ci/check.sh and bash tools/ci/check-worker.sh. Compare 10 alternating legacy subprocess timings per engine to a recorded pre-change baseline, accepting at most max(20% of baseline median, 0.1 seconds) added median. Record raw timings. Require automatic-review resolution and green exact-head CI for each PR; no live simulation submissions here.
- **Rollback / Backout:** Hold admission/release readiness and return failed evidence to the owning task; do not relax criteria after results.
- **Change budget:** max_files=2, max_new_symbols=0, interface_policy=none. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^tests/test_aer_acceptance\.py$
^spec/qasm\-support/classical\-conditions/validation\.md$
```

### TASK-019: Prepare the Aer rollout handoff

- **Goal:** Prepare the Aer rollout handoff.
- **Dependencies:** TASK-018 and owner-merged implementation PRs.
- **Recommended Mode:** executor.
- **Risk:** Med: release must preserve accepted jobs during upgrades/backout.
- **Files/areas:** `spec/qasm-support/classical-conditions/release.md`. New Aer test/report paths are proposed.
- **Steps:** Record approved merge revisions, verified image identity, capability subset and exact CI receipts. Integrate with the original TASK-010 handoff without claiming it completed. Specify worker-first availability, compatible warm-worker reuse, admission sequencing and drain-first rollback. Coordinate with the separately approved release/CD flow when available; do not create a second deployment system or expose private configuration.
- **Verification:** Check immutable commit/image references, successful CI and owner merge evidence. Review rollback against pending/running Aer jobs and result-reader compatibility. List the separately authorized live smoke acceptance needed after deployment. If a published digest is not yet available, mark handoff incomplete rather than inventing one.
- **Rollback / Backout:** Hold rollout; preserve existing service and any capable workers required by accepted jobs.
- **Change budget:** max_files=1, max_new_symbols=0, interface_policy=none. Production functions/classes/exports count; test functions do not.
- **write_lease:**

```text
^spec/qasm\-support/classical\-conditions/release\.md$
```

## Definition of Done Checklist

- [x] Owner approved the design and requirements amendment.
- [x] Owner approved this task plan before implementation.
- [ ] Combined frozen dependencies and licenses verified; current engine pins preserved.
- [ ] Input safety, condition semantics, classical mapping and seed behavior pass positive/negative checks.
- [ ] Actual isolated worker respects existing memory/output/thread budgets and compatible-image reuse.
- [ ] Legacy defaults, identities and result contracts remain unchanged.
- [ ] Authenticated API-to-worker examples and protected complete artifacts pass for Aer.
- [ ] All three engines and actual packaged image pass required CI; runner path reported.
- [ ] Automatic review resolved within the authorized review policy; owner merges PRs.
- [ ] Complete rollout handoff recorded, with deployed acceptance clearly outstanding until authorized and verified.

## Execution Notes (tooling, environment, checkpoints)

The owner approved this plan on 2026-10-14. TASK-011 is implemented in the foundation PR; its local verification passed. Preserve the existing release/CD draft. Production actions remain outside these implementation leases.

Before execution, inspect current repository instructions and main revision, preserve local spec work, and allocate one task lease per writer. Leases are conservative allowlists, not instructions to touch every file. Exceeding a file/symbol budget or discovering a new public contract requires an amended plan before edits. TASK-012/013 share one file and cannot run concurrently. TASK-016/017 can. Do not reuse older execution authorization for this amendment.

Verified entry points: `bash tools/ci/check.sh` for the frozen environment/full suite, and `bash tools/ci/check-worker.sh` for the real worker image. For focused tests after dependency resolution use `uv run --frozen --extra worker --extra controller pytest -q <listed test paths>`. New files must exist before these commands run. Docker is required for image checks; lack of Docker is not a successful image check. The separate hosted image job is authoritative when the local runner cannot build images.

Current source uses a 2-CPU/4-GB request, image-keyed worker reuse and a restricted child environment. These observations guide TASK-011/014; they are not new resource sizing decisions or live-cluster assertions. Do not change scheduling/resources, add active-job deadlines, impose global concurrency caps, or provision a worker for this plan.

All implementation changes use PRs; the owner merges. Preserve Standpunkt-first test routing and hosted fallback, with the separate required hosted image check. Report CI failures immediately. Trust automatic Codex review on ready/update events: no manual reviewer triggers and at most two remediation rounds before escalating further findings. Never hide a failed self-hosted test by treating hosted success as proof the original failure was harmless.

Checkpoints: dependency decisions after TASK-011; safety/execution after TASK-013; image/isolation before TASK-015; full verification after TASK-018. A failed discovery assumption returns to the design/task gate, not a substitute engine or relaxed semantic contract. Release/CD work and original TASK-010 remain separate; TASK-019 is a handoff only. Deployments, live workload submissions and infrastructure changes require their own authorization.

## Traceability (REQ IDs -> TASK IDs)

| Parent requirements | Tasks |
| --- | --- |
| REQ-001 integration selection | TASK-011, TASK-012, TASK-013 |
| REQ-002 API compatibility | TASK-015, TASK-018 |
| REQ-003/004 samples and classical results | TASK-013, TASK-014, TASK-017, TASK-018 |
| REQ-005/006 reset and classical conditions | TASK-011, TASK-012, TASK-013, TASK-018 |
| REQ-007/008/009 gates, bindings, QASM3 | TASK-012, TASK-015, TASK-017; preserve qsim support, explicitly defer Aer custom gates/bindings |
| REQ-010/011 capabilities and probability rejection | TASK-012, TASK-015, TASK-017 |
| REQ-012–014 durable lifecycle, isolation and identity | TASK-014, TASK-015, TASK-018 |
| REQ-015 streaming | TASK-015, TASK-017; incremental samples remain false |
| REQ-016/017 safe failures and compatibility | TASK-012, TASK-014, TASK-015, TASK-018 |
| REQ-018–021 safety and resource/lifecycle constraints | TASK-011, TASK-012, TASK-014, TASK-018 |
| REQ-022 provenance | TASK-013, TASK-014, TASK-017 |
| REQ-023/024 verification and release | TASK-016, TASK-017, TASK-018, TASK-019 |

Lease clarification (2026-10-14): TASK-016 includes `docs/ci.md` so its engine-coverage description stays consistent with the workflow. The five-file change budget is unchanged; no routing change is authorized.
