# Proposed implementation tasks

Status: task plan ready for owner review. Requirements and design were approved on 2026-10-25; task-plan and implementation approval are not yet recorded. Scope includes all three backends and GPU discovery.

| Task | Work | Requirements | Evidence before completion |
| --- | --- | --- | --- |
| PHY-T01 | Constrained QuTiP discovery, coefficient-grid/shape conventions, density validation, result and memory budgets | 005–013 | Analytic/convergence fixtures; actual 2 CPU/4 GiB image measurements; worst-case callback accounting; resolved design decisions |
| PHY-T02 | Strict request models, shared semantic validation, schema route, general/explicit submission integration | 001–004, 014 | JSON schema/validator agreement, 422 pointers, zero queued invalid jobs, unchanged legacy canonicalization and errors |
| PHY-T03 | Native model builder, operators, states, static terms, collapse operators and bounded numeric drives | 005–009 | Hermiticity, positivity, units, tensor-order, complex-HC and pulse-between-output-times tests |
| PHY-T04 | Evolution, propagator, spectrum and steady-state adapters; NPZ serialization and child dispatch | 009–013 | Analytic Rabi/T1/ringdown/thermal/dispersive/propagator checks; statistical MC comparison; safe callback overflow behavior |
| PHY-T05 | Worker dependency/image lock, CI, lifecycle and backup regressions | 001–002, 011–015 | Existing and new engine CI, real isolated-child execution, idempotency/conflict/SSE resume/cancel/delete and native backup validation |
| PHY-T06 | Capabilities, developer/agent guides and release-acceptance contract | 014–015 | Tested examples, accurate pinned versions/limits, old clients unchanged, extended acceptance evidence tests |
| PHY-T07 | Owner merge and existing guarded deployment | 015 | Exact merged-main CI images, fresh backup, current circuit acceptance plus Lindblad physics/result check; no credential changes |
| PHY-T08 | Implement scqubits schemas, native adapter and numeric Lindblad export | 016–017 | Class/operator allowlists, version/license review, export round-trip spectrum equality and resource measurements |
| PHY-T09 | Implement JoSIM schema, positive netlist subset and native transient adapter | 016, 018 | Positive safe subset/parser decision, native executable provenance, SFQ analytic fixture, filesystem escape and output-bound tests |
| PHY-T10 | Assess CPU/GPU native backend compatibility and benchmark representative workloads | 011, 019 | Exact backend versions, supported-operation matrix, precision agreement, VRAM/host-memory measurements, cold/warm end-to-end timings |
| PHY-T11 | Implement useful GPU placement through the existing launcher, or record evidence for retaining CPU | 019–020 | Server-only placement policy, image/resource pool compatibility, GPU availability/fallback tests, truthful capability and result metadata |
| PHY-T12 | Complete all-three-engine acceptance, docs and guarded release integration | 015–020 | All prior circuit cases plus Lindblad analytic evolution, scqubits spectrum/export and JoSIM SFQ traces; CPU/GPU cases where advertised |

All three backends are required by the owner's scope confirmation. PHY-T07 can validate an incremental release but does not complete this flow before PHY-T08/09/12. Task order may change to perform GPU discovery before choosing worker image variants.

Sweeps require a later explicit amendment. They must not appear as an advertised capability in these increments.
