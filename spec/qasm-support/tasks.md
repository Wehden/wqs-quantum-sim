# Tasks: broader circuit support

Status: task plan approved by the owner on 2026-10-13, with parallel System2 execution authorized within disjoint write leases. Requirements and design were approved on the same date. TASK-001 through TASK-009 implemented and locally verified; PR review/CI gates remain. TASK-010 awaits owner merges. Approval preserves the design's evidence gates; it does not establish untested engine capabilities.

Prepared using System2 1.1.1 task-planner. Paths are scoped to this existing feature specification directory.

## Task Graph Overview (short)

TASK-001 capability probes → TASK-002 contract decisions → TASK-003 legacy identity and TASK-004 isolated adapter/package → TASK-005 sampling → independently supported TASK-006 dynamic circuits and TASK-007 languages/bindings → TASK-008 security/lifecycle → TASK-009 documentation/full verification → TASK-010 release handoff.

TASK-003 and TASK-004 have separate writes and can proceed independently after contract decisions. TASK-006 and TASK-007 share adapter files and must execute sequentially. Each implementation increment is a small PR with its own applicable checks; the final validation tasks do not postpone PR-level checks. Unsupported features are deferred with evidence, not represented as implemented.

## Tasks (the full list)

### TASK-001: Capability evidence

- **Goal:** Capability evidence.
- **Dependencies:** None.
- **Recommended Mode:** test-engineer.
- **Risk:** High: parser support can exceed engine execution support.
- **Files/areas:** `tests/test_qasm_capabilities.py`, `spec/qasm-support/capabilities.md`. New test filenames are proposed.
- **Steps:** Probe the pinned native/importer paths for terminal and mid-circuit measurement, reset, conditions, repeated writes, custom gates and QASM3. Record exact versions, licenses, inputs and positive/negative outcomes per engine. Use analytic fixtures first; request a design amendment before adding a dependency family. Record unsupported cells explicitly.
- **Verification:** Run the new capability test module with the repository pytest command below. Every advertised candidate cell needs executable evidence; unsupported cells need a reproducible reason. No production submissions.
- **Rollback / Backout:** Remove discovery-only fixtures/report; existing runtime stays unchanged.
- **Change budget:** max_files=2, max_new_symbols=12, interface_policy=none. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^tests/test_qasm_capabilities\.py$
^spec/qasm\-support/capabilities\.md$
```

### TASK-002: Resolve contracts for the first supported subset

- **Goal:** Resolve contracts for the first supported subset.
- **Dependencies:** TASK-001.
- **Recommended Mode:** executor.
- **Risk:** High: result ordering and numeric identity become durable contracts.
- **Files/areas:** `spec/qasm-support/requirements.md`, `spec/qasm-support/design.md`, `spec/qasm-support/interfaces.json`, `spec/qasm-support/module-boundaries.json`, `spec/qasm-support/decisions.md`. New test filenames are proposed.
- **Steps:** Record OR-001 through OR-004 and OR-006/007 decisions for the first subset: integration, supported syntax, numeric canonicalization, final-memory schema, measured expansion bounds, statistical tolerances and benchmark threshold. Keep OR-005 incremental samples deferred. Attach probe evidence and flag changes to the approved design for owner review. Unsupported features stay deferred without blocking independent verified features.
- **Verification:** Review each decision against capability evidence and all 24 requirements. Owner accepts material contract changes before dependent code tasks. Validate JSON and Markdown links; git diff --check.
- **Rollback / Backout:** Keep prior approved contract; dependent tasks remain blocked.
- **Change budget:** max_files=5, max_new_symbols=0, interface_policy=none. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^spec/qasm\-support/requirements\.md$
^spec/qasm\-support/design\.md$
^spec/qasm\-support/interfaces\.json$
^spec/qasm\-support/module\-boundaries\.json$
^spec/qasm\-support/decisions\.md$
```

### TASK-003: Preserve legacy request identity

- **Goal:** Preserve legacy request identity.
- **Dependencies:** TASK-002.
- **Recommended Mode:** executor.
- **Risk:** High: changed normalization could break durable retries.
- **Files/areas:** `qsim/api.py`, `qsim/models.py`, `tests/test_service.py`, `tests/test_transactions.py`. New test filenames are proposed.
- **Steps:** Capture historical normalized requests and hashes from synthetic fixtures using current code. Separate legacy canonicalization from new-operation identity before adding defaults. Preserve explicit/omitted engine behavior, generic-route optional keys, strict fields and owner-scoped conflicts.
- **Verification:** Run tests/test_service.py and tests/test_transactions.py. Replaying every legacy fixture must produce identical canonical bytes/hash and job identity; changed semantic inputs must conflict.
- **Rollback / Backout:** Revert before admission of new schemas; never rewrite old hashes.
- **Change budget:** max_files=4, max_new_symbols=4, interface_policy=none. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^qsim/api\.py$
^qsim/models\.py$
^tests/test_service\.py$
^tests/test_transactions\.py$
```

### TASK-004: Integrate verified importer and worker packaging

- **Goal:** Integrate verified importer and worker packaging.
- **Dependencies:** TASK-002.
- **Recommended Mode:** executor.
- **Risk:** High: untrusted parsing and standalone worker packaging.
- **Files/areas:** `qsim/qasm_adapter.py`, `qsim/worker.py`, `qsim/controller.py`, `deploy/Dockerfile`, `tests/test_qasm_adapter.py`, `tests/check_worker_image.sh`. New test filenames are proposed.
- **Steps:** Add the single approved adapter module with dependency-free capability metadata and lazy native imports. Implement approved packaged-include and expansion checks inside isolated execution. Package only the minimal worker modules and update parent/child module entry points plus controller invocation. Leave new capability admission disabled.
- **Verification:** Run tests/test_qasm_adapter.py and tests/test_worker_lifecycle.py. Build the worker target per deploy/Dockerfile and extend/run tests/check_worker_image.sh against it: verify package imports, isolated child execution and both engines. Metadata imports must not load native libraries.
- **Rollback / Backout:** Revert adapter/entry points together while new admission is disabled; retain prior worker image.
- **Change budget:** max_files=6, max_new_symbols=8, interface_policy=extend-only. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^qsim/qasm_adapter\.py$
^qsim/worker\.py$
^qsim/controller\.py$
^deploy/Dockerfile$
^tests/test_qasm_adapter\.py$
^tests/check_worker_image\.sh$
```

### TASK-005: Implement QASM sampling and final memory

- **Goal:** Implement QASM sampling and final memory.
- **Dependencies:** TASK-003, TASK-004.
- **Recommended Mode:** executor.
- **Risk:** High: measurement ordering and repeated writes affect correctness.
- **Files/areas:** `qsim/models.py`, `qsim/api.py`, `qsim/worker.py`, `qsim/qasm_adapter.py`, `tests/test_qasm_sampling.py`, `tests/test_service.py`, `tests/test_qasm_adapter.py`. New test filenames are proposed.
- **Steps:** Add explicit QASM sample operation only for verified combinations. Preserve legacy probability defaults and reject nonunitary probability requests. Emit qasm-samples/1 with declaration-order register metadata, zero-initialized final classical memory, complete NPZ, top 256 counts with lexicographic ties and first 20 shots. Validate artifacts before completion; bound classical output cells.
- **Verification:** Run tests/test_qasm_sampling.py, tests/test_service.py and tests/test_qsim_engine.py. Verify asymmetric deterministic circuits, unused bits, reordered writes, count truncation and JSON/NPZ reconstruction. Unsupported/default-engine combinations reject without fallback.
- **Rollback / Backout:** Disable new admission; drain accepted jobs on capable images before reverting; retain result readers.
- **Change budget:** max_files=7, max_new_symbols=8, interface_policy=extend-only. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^qsim/models\.py$
^qsim/api\.py$
^qsim/worker\.py$
^qsim/qasm_adapter\.py$
^tests/test_qasm_sampling\.py$
^tests/test_service\.py$
^tests/test_qasm_adapter\.py$
```

### TASK-006: Enable verified reset and classical control

- **Goal:** Enable verified reset and classical control.
- **Dependencies:** TASK-005.
- **Recommended Mode:** executor.
- **Risk:** High: collapse and branch semantics may lack engine support.
- **Files/areas:** `qsim/qasm_adapter.py`, `qsim/worker.py`, `tests/test_qasm_dynamic.py`. New test filenames are proposed.
- **Steps:** Enable only TASK-001 proven constructs. Test entangled reset, both conditional branches, language register significance, mid-circuit collapse, repeated writes and independent shots. Unsupported constructs receive safe failures. If no maintained integration supports a construct, document it as deferred; do not build an interpreter.
- **Verification:** Run tests/test_qasm_dynamic.py and tests/test_qasm_sampling.py using predeclared criteria. Assert unsupported controls cannot be silently removed or simulated by a different engine.
- **Rollback / Backout:** Disable affected combinations, drain accepted work, then revert implementation.
- **Change budget:** max_files=3, max_new_symbols=6, interface_policy=none. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^qsim/qasm_adapter\.py$
^qsim/worker\.py$
^tests/test_qasm_dynamic\.py$
```

### TASK-007: Enable verified custom gates and QASM3 bindings

- **Goal:** Enable verified custom gates and QASM3 bindings.
- **Dependencies:** TASK-004, TASK-005; TASK-006 only for dynamic features exposed here.
- **Recommended Mode:** executor.
- **Risk:** High: expansion and bindings cross parser and identity boundaries.
- **Files/areas:** `qsim/models.py`, `qsim/api.py`, `qsim/qasm_adapter.py`, `qsim/worker.py`, `tests/test_qasm_languages.py`, `tests/test_transactions.py`, `tests/test_qasm_adapter.py`, `tests/test_qasm_sampling.py`, `tests/test_service.py`. New test filenames are proposed.
- **Steps:** Use importer-native custom-gate expansion within approved bounds. Add at most /api/jobs/qasm3 with explicit operation for the proven subset, and finite numeric input bindings using native resolvers. Reject missing/extra/invalid bindings and unsafe includes. Preserve QASM2 numeric/gate-local syntax without a placeholder dialect. Enable each independent feature only when its own evidence and decisions are complete.
- **Verification:** Run tests/test_qasm_languages.py and tests/test_transactions.py. Test binding hash equivalence/conflicts, gate operand mapping, recursion/expansion rejection and unsupported QASM3 features; use reference fixtures from TASK-001.
- **Rollback / Backout:** Disable new combinations and drain dependent jobs; revert new route after retaining access to existing results.
- **Change budget:** max_files=9, max_new_symbols=8, interface_policy=extend-only. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^qsim/models\.py$
^qsim/api\.py$
^qsim/qasm_adapter\.py$
^qsim/worker\.py$
^tests/test_qasm_languages\.py$
^tests/test_transactions\.py$
^tests/test_qasm_adapter\.py$
^tests/test_qasm_sampling\.py$
^tests/test_service\.py$
```

### TASK-008: Verify lifecycle, security and failure contracts

- **Goal:** Verify lifecycle, security and failure contracts.
- **Dependencies:** TASK-005 plus each selected TASK-006/007 increment.
- **Recommended Mode:** security-sentinel.
- **Risk:** High: isolation must hold for all new parsers and results.
- **Files/areas:** `tests/test_qasm_security.py`, `tests/test_transactions.py`, `tests/test_worker_lifecycle.py`, `tests/test_dispatch.py`, `tests/test_observability.py`, `spec/qasm-support/validation.md`. New test filenames are proposed.
- **Steps:** Verify cross-owner access, safe error categories in existing Completion.error, include confinement, child credentials, incomplete/conflicting callbacks and replay. Assert no new active deadline/concurrency cap, correct idle cleanup, capacity queuing and disconnect independence. Record unsupported incremental samples. Route production fixes back to the relevant executor task with a revised lease.
- **Verification:** Run the listed Python test modules; use focused lifecycle fixtures, not a production load test. Capture results and scan error/log assertions for raw circuits, bindings and credentials.
- **Rollback / Backout:** Tests may remain; block admission/release on failures. Do not weaken checks to obtain green CI.
- **Change budget:** max_files=6, max_new_symbols=10, interface_policy=none. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^tests/test_qasm_security\.py$
^tests/test_transactions\.py$
^tests/test_worker_lifecycle\.py$
^tests/test_dispatch\.py$
^tests/test_observability\.py$
^spec/qasm\-support/validation\.md$
```

### TASK-009: Update documentation and run release verification

- **Goal:** Update documentation and run release verification.
- **Dependencies:** TASK-008.
- **Recommended Mode:** test-engineer.
- **Risk:** Med: capability claims and both-engine regressions must match shipped images.
- **Files/areas:** `qsim/static/index.html`, `qsim/static/agents.md`, `README.md`, `tests/test_qasm_capabilities.py`, `tests/test_qasm_examples.py`, `spec/qasm-support/validation.md`, `tests/check_worker_image.sh`. New test filenames are proposed.
- **Steps:** Publish only proven support and retained exclusions; provide executable sampling/probability examples and ordering/binding guidance. Reconcile capabilities with image contents and fixtures. Record unchanged-unitary benchmark comparison against agreed threshold. Execute full repository CI and worker image checks.
- **Verification:** Run bash tools/ci/check.sh and the extended worker-image check. Run tests/test_qasm_examples.py. Require both-engine regressions and recorded Standpunkt-first/GitHub-fallback path. Report CI failure to owner immediately; missing CI is not green.
- **Rollback / Backout:** Revert incorrect documentation/admission claims; preserve stored artifact compatibility.
- **Change budget:** max_files=7, max_new_symbols=6, interface_policy=none. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^qsim/static/index\.html$
^qsim/static/agents\.md$
^README\.md$
^tests/test_qasm_capabilities\.py$
^tests/test_qasm_examples\.py$
^spec/qasm\-support/validation\.md$
^tests/check_worker_image\.sh$
```

### TASK-010: Prepare rollout handoff

- **Goal:** Prepare rollout handoff.
- **Dependencies:** TASK-009 and owner-merged implementation PRs.
- **Recommended Mode:** executor.
- **Risk:** Med: mixed worker versions can strand accepted requests.
- **Files/areas:** `spec/qasm-support/release.md`. New test filenames are proposed.
- **Steps:** Record reviewed commits, exact worker image and verified capability matrix in a release handoff. Describe worker-first rollout, compatible warm-worker selection, admission enablement and drain-first backout. Keep private deployment values in the private baseline. Actual deployment is a separate authorized action outside this task write lease.
- **Verification:** Check PR checks and automatic review status, maximum two remediation rounds, owner merge evidence and complete validation record. Identify the private deployment follow-up without copying private values into public docs. No rollout is claimed by preparing this handoff.
- **Rollback / Backout:** Hold release/admission; retain capable workers for previously accepted work.
- **Change budget:** max_files=1, max_new_symbols=0, interface_policy=none. Count production functions/classes/exports; fixture/test functions do not consume the production-symbol budget. Exceeding a budget requires a revised plan before edits.
- **write_lease:**

```text
^spec/qasm\-support/release\.md$
```

## Definition of Done Checklist

- [x] Owner approves this task plan before execution.
- [ ] Selected capabilities have versioned positive and negative engine evidence; deferred cells are explicit.
- [ ] Affected open decisions are resolved and material design changes accepted before dependent implementation.
- [ ] Legacy identity, results and engine defaults remain compatible.
- [ ] New samples preserve classical semantics and complete artifacts within approved bounds.
- [ ] Both engines, actual worker packaging, security and lifecycle checks pass.
- [ ] Public and agent documentation matches capabilities actually shipped.
- [ ] Every implementation PR reports CI runner path and passes checks; automatic review has no unresolved findings after at most two remediation rounds.
- [ ] Owner merges; deployment handoff includes admission/drain/backout sequencing and no private values.

## Execution Notes (tooling, environment, checkpoints)

Use the canonical repository checkout. Read current instructions and rebase the implementation branch on current main before code changes; preserve local spec edits. No project CLAUDE.md or .claude rules were present during planning. Review repository changes since the documented baseline before relying on it.

Verified repository verification entry point: `bash tools/ci/check.sh`. It runs frozen uv synchronization, dependency checks, pytest and the Node console tests. For focused Python verification, use `uv run --frozen --extra worker --extra controller pytest -q <listed test paths>` after environment synchronization. Proposed new test files must exist before their commands run. `tests/check_worker_image.sh <image>` exists, but currently tests startup followers only; TASK-004 must extend it before claiming adapter/image coverage. Inspect Dockerfile target and available Docker/build environment at execution time; no invented image tag or runtime command is certified by this plan.

Standpunkt is preferred, with the established GitHub-hosted fallback. Report CI failures immediately. Do not add availability tokens or change routing as part of this feature. Use automatic Codex review without manual triggers and stop after two remediation rounds if findings remain. The owner merges implementation PRs. Use REST PATCH instead of the broken gh pr edit command when an edit is needed.

Every task should begin with its dependencies, write lease and budget. Checkpoint after discovery/contract decisions and after each supported increment. Native parser acceptance is not execution evidence. Where probes invalidate an assumption, revise the affected design/task before proceeding. No custom interpreter, silent engine fallback, hardware execution, production replay, active-job deadline or concurrency cap is authorized by this plan.

Document checks for this planning gate are JSON structure/link/traceability checks and `git diff --check`; they are not runtime CI. No tests, dependency installs or capability experiments have run as part of writing this plan.

## Traceability (REQ IDs -> TASK IDs)

| Requirement | Tasks |
| --- | --- |
| REQ-001 | TASK-001, TASK-002, TASK-004 |
| REQ-002 | TASK-003, TASK-005, TASK-009 |
| REQ-003 | TASK-005 |
| REQ-004 | TASK-002, TASK-005, TASK-006 |
| REQ-005 | TASK-001, TASK-006 |
| REQ-006 | TASK-001, TASK-006 |
| REQ-007 | TASK-001, TASK-002, TASK-007 |
| REQ-008 | TASK-002, TASK-007 |
| REQ-009 | TASK-001, TASK-002, TASK-007 |
| REQ-010 | TASK-001, TASK-004, TASK-005, TASK-009 |
| REQ-011 | TASK-005, TASK-008 |
| REQ-012 | TASK-003, TASK-007, TASK-008 |
| REQ-013 | TASK-005, TASK-008, TASK-009 |
| REQ-014 | TASK-005, TASK-006, TASK-008 |
| REQ-015 | TASK-008, TASK-009 |
| REQ-016 | TASK-004, TASK-005, TASK-008 |
| REQ-017 | TASK-008 |
| REQ-018 | TASK-008 |
| REQ-019 | TASK-002, TASK-004, TASK-005, TASK-008 |
| REQ-020 | TASK-004, TASK-007, TASK-008 |
| REQ-021 | TASK-004, TASK-008 |
| REQ-022 | TASK-004, TASK-005, TASK-009 |
| REQ-023 | TASK-001, TASK-009, TASK-010 |
| REQ-024 | TASK-009, TASK-010 |

## Execution record

2026-10-13: owner approved the plan and parallel execution. Current upstream main remains the documented baseline 0e850ec; the specification branch is based on that commit. TASK-001 is split into non-overlapping leases: capability_tests owns tests/test_qasm_capabilities.py; capability_report owns spec/qasm-support/capabilities.md. A third executor performs read-only compatibility/packaging preparation until TASK-002 is resolved. The coordinator owns planning status files. Production write leases are not issued before dependencies are satisfied.

TASK-001 completed: 24 new capability tests and five existing engine tests passed. TASK-002 choices are recorded in [decisions.md](decisions.md); source safety findings are in [capabilities.md](capabilities.md). TASK-003 compatibility and TASK-004 adapter/packaging receive disjoint production write leases. Classical conditions remain deferred because native execution fails; no unverified feature will be admitted.

Execution lease clarification: TASK-005 includes tests/test_qasm_adapter.py (max_files 7) solely to update the existing disabled-admission assertion when sampling is enabled. This adjacent test change is required by the approved admission transition; coordinator owns that file during split execution.

TASK-005 passed combined integration checks. TASK-006 is validation-only in tests/test_qasm_dynamic.py because the shared sampling path already executes reset and measurement; no runtime lease is issued. TASK-007 can proceed with separate API and adapter leases while those tests run; any discovered runtime fix serializes the overlapping work. TASK-007 includes qsim/worker.py (max_files 6) for QASM3 probability dispatch, required by the approved new route. Existing QASM2 probability behavior remains on its compatible path.

TASK-007 adjacent test adjustment: coordinator owns prior custom-gate rejection assertions in tests/test_qasm_adapter.py and tests/test_qasm_sampling.py as support is deliberately enabled. max_files becomes 8; no other lease expansion. TASK-006 finished with six passing isolated-worker tests and no production edits. TASK-008 security tests for the completed sampling increment run in tests/test_qasm_security.py while the language increment is developed.

TASK-007 also updates the prior custom_gates=false capability assertion in tests/test_service.py (coordinator lease, max_files 9). New operation-specific capability metadata must continue to report false for unchanged QASM2 probability custom gates.

TASK-009 includes tests/check_worker_image.sh (coordinator lease, max_files 7) so the existing package-image check permanently covers new QASM2/QASM3 worker entry points and wide seeds, beyond the earlier standalone adapter probe.

Execution checkpoint: TASK-003 through TASK-009 are implemented and locally verified; see [validation.md](validation.md). Full local CI: 220 Python + 3 console tests passed, including the unpushed PR #8 corrective fixture. Published feature suite count is 219 until that correction is pushed. PR #9 foundation CI passed on Standpunkt with no automated findings. TASK-010 remains pending owner merges. No deployment occurred.
