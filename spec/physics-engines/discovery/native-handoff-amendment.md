# G1 amendment: native observables and JoSIM execution projection

Status: approved by the owner on 2026-11-03. Both Design A and Design B and
their stated interface and write-lease amendments are approved for implementation.
Implementation and allocation proof remain pending. Physics activation, backup/release gates, credentials
and unrelated workloads stay unchanged.

## Why this amendment is needed

TASK-013's native model has six frozen fields: Hamiltonian, collapse operators,
initial state, tensor order, coefficient segments and allocation plan. It does
not carry the requested observable operators. The existing operator constructor
is private to the builder. Importing that helper into the adapter would violate
the frozen boundary; duplicating it would create two implementations of matrix,
product and tensor-order semantics.

TASK-020's bounded-history certificate binds the patched JoSIM executable, while
the old TASK-021 adapter still verifies the original executable. Public admission
therefore remains closed. Separately, native DEVV output for independent current
sources omits the requested column despite a zero native exit. Refusing that
previously approved trace would reduce supported semantics. Upstream NODEV output
provides the same physical voltage through its existing bounded native path.

## Requirements amendment

- NH-001: Keep `run_lindblad` and the public request/result schemas unchanged.
  Supply native observable operators in validated request order without a
  production import of a builder-private symbol or a duplicated constructor.
- NH-002: Existing six-positional native-model construction remains valid.
  Observable count, native operator shape/tensor dimensions and finiteness
  mismatch fail before result allocation; there is no fallback that silently
  omits expectations. Zero observables remain valid.
- NH-003: Charge retained observable matrices, construction copies and expectation
  intermediates before final native admission. No hidden history or weaker
  final-only retention is introduced.
- NH-004: Preserve the canonical JoSIM client request, idempotency identity,
  public trace labels/order and SI units. Keep all existing non-current-source
  voltage/current/phase behavior unchanged.
- NH-005: Allow only the server-generated DEVV-independent-current-source to
  NODEV projection described below. Clients do not gain arbitrary directives,
  expressions, files, native options or a general projection language.
- NH-006: Verify the exact patched executable against the allocation certificate
  before native execution. Original, wrong and tampered binaries must be
  rejected. Do not enable public admission while plan/runtime identity can
  disagree or while required production packaging remains incomplete.
- NH-007: Keep the original public input cap at 65,536 ASCII bytes. Bound generated
  native text, projection owners and copies separately, within the existing
  2-GiB total plan and approved CPU/file/address-space profile.
- NH-008: Preserve active jobs and their lifetime semantics. Add no simulation
  deadline, internal-step cap, partial-result success or direct GPU/provider
  execution. All GPU work remains scheduled through SkyPilot.

## Design A: observable handoff

Append `observables: tuple = ()` after `allocation_plan` in
`NativeLindbladModel`. This is the optional seventh field; existing positional
construction remains compatible. Populate it inside `_build_model`, under its
existing native settings context, by reusing `_operator` in that same module.
Operators follow `request.observables` order; names remain in the validated
request and are not copied into the model.

The adapter consumes this tuple and checks its length against the request, native
operator status, square shape, tensor dimensions and finiteness before allocating
output arrays. A missing tuple with requested observables is an error. The builder
is authoritative for operator construction/order; the adapter does not reconstruct
operators to compare their numerical contents.
The draft private-helper import is removed. Existing public export names remain
unchanged; the frozen constructor signature gains the defaulted field.

Persistent dense observable storage is bounded by `16 * O * D**2` bytes for
complex128 operators, with O <= 32. This formula alone is not an allocation
certificate: native construction temporaries, tensor intermediates, working state,
expectation evaluation and parent/child overlap must still be charged and proven
by the final certificate. No public admission bypass follows from adding the
field.

## Design B: bounded JoSIM native projection and identity reconciliation

For each validated `DEVV Ixx`, obtain the source's validated node pair `(a,b)`.
Generate one fixed `.PRINT NODEV a b` statement when `a != 0`. When `a == 0`,
generate `.PRINT NODEV b 0` and multiply that column by -1 during result
projection. This avoids the pinned native first-node-ground handling bug.
For rewritten current-source voltage columns only, normalize exact zero to
positive zero, matching the native output accumulator. Apply sign and zero
projection only to those rewritten columns. Unaffected columns, including their
zero signs, remain bitwise unchanged. Use no epsilon threshold or lossy correction.

The native text is private execution material. The client canonical envelope and
hash stay unchanged. Recompute all projection metadata from validated input;
never accept it from the client. Parse columns positionally against the complete
ordered native header, including duplicate NODEV labels. Relabel projected
columns with their original public names, retaining existing shape, ordering,
finiteness, row/time and byte checks. No dictionary keyed solely by the native
header may collapse duplicate columns.

Private metadata is bounded to:

- `native_netlist`: at most 65,568 ASCII bytes, including at most four added bytes
  for each of eight rewritten trace statements.
- `native_trace_names`: at most eight strings, each at most 35 characters.
- `trace_projection`: at most eight fixed entries with bounded public/native
  labels, a scale restricted to -1 or +1 and a `rewritten` boolean derived from
  the validated trace kind. Reserve 2,048 serialized bytes for that table; no
  caller-supplied transformation is interpreted.

The native matrix dimension is unchanged. Each rewritten trace selects at most
two native variables; the existing selected-variable cap of 24 remains
conservative. Output stays at most eight traces and 4,096 predicted rows.

Use a conservative additional projection envelope of 1 MiB, including the
4,608-byte native token/temporary increase. Source-derived preliminary Python
owners include generated ASCII text/fragments (145,826 bytes), at most eight
fixed projection dictionaries/list slots, the 2,048-byte serialized table and its
Unicode/UTF-8 copies, bounded headers and at most one additional full projected
float64 trace matrix (8 * 4,096 * 8 = 262,144 bytes). The existing result arrays,
CSV parser and callback owners remain separately charged. The implementation
must enumerate actual owners/copies and prove they fit this envelope; a generic
reserve or RSS sample alone is not that proof. It may not create uncharged
additional full histories or matrix copies.

The existing largest private plan of 621,726,476 bytes plus this envelope is
622,775,052 bytes, below 2 GiB. The native subtotal becomes 570,713,600 bytes;
its 4,608-byte increase is included once in the 1-MiB envelope. Final line items
must prevent double counting and retain the same process/address-space/file
profile. If actual owners cannot be bounded inside this envelope, return for
review before enabling execution.

Update the private algorithm/certificate identity for projection semantics. In
one reviewed implementation change, reconcile TASK-021's fixed native hash and
runtime checks with the private certificate for isolated fixture execution.
Public `admission_plan()` remains closed during this work. Lifting that gate
belongs to separately accepted packaging/worker integration and the existing
G2/G3/G4 activation process; this amendment does not authorize it. Preserve the
fixed executable path and argument vector, private scratch, positive grammar,
original-binary refusal and complete error/result validation. No production
activation is approved by this amendment.

## Alternatives considered

- Import the private Lindblad operator helper: fewer changes, but contradicts
  the frozen module boundary. Not recommended.
- Add a second public operator-builder entry point: possible, but unnecessary
  when the already-created native model can own its observables.
- Refuse DEVV I traces: simple but reduces approved semantics. Requires explicit
  owner selection; not the recommended path.
- Patch native JoSIM RelevantTrace handling: changes the audited native source,
  build, hashes and native patch lease. The existing NODEV path avoids that
  additional native implementation change.

## Approved lease/interface amendment

| Owner | Existing lease | Proposed addition |
| --- | --- | --- |
| TASK-013 executor | `qsim/lindblad_adapter.py`, `tests/test_lindblad_evolve.py` | `qsim/lindblad_model.py`; max_files becomes 3, production-symbol budget stays 8 |
| TASK-020 executor, atomic TASK-021 reconciliation | `qsim/josim_models.py`, `qsim/josim_validation.py`, `tests/test_josim_validation.py`, `tests/test_josim_security.py` | `qsim/josim_adapter.py`, `tests/test_josim_execution.py`; max_files becomes 6, production-symbol budget stays 14 |
| Coordinator | Shared spec/status only | Update `interfaces.json` native-model constructor signature and approved design/tasks/decision records |

The two runtime leases remain disjoint. No dependency, deployment, native patch,
controller/store, credential or worker activation file is added to either lease.

## Evidence and acceptance

The uncommitted TASK-013 draft passes 26 new real-native evolve tests and 426
focused tests overall. It remains unpublishable until its private-helper import
is replaced through an approved handoff. Required added tests cover the defaulted
constructor, zero-observable success, ordered product/matrix observables,
count/shape/tensor-dimension/finiteness mismatch, request normalization and
unchanged none/final/all state retention. Repeat the shared native builder's
`test_lindblad_operators.py`, `test_lindblad_drives.py` and
`test_lindblad_models.py` suites alongside evolve tests, including native settings
restoration after observable-construction failure.

The safe TASK-020 private planning stage is committed locally at `f3971f2` and
keeps public admission closed. Validation/security tests pass 148 cases; combined
validation/security/history/execution tests pass 189 without skips using the
actual original/candidate/instrumented binaries. Existing seven fixture and seven
capacity comparisons still pass in the constrained pinned build image.

Read-only projection discovery, not production code, passed nine DC/PWL/retry
fixtures across node orientations `(1,2)`, `(1,0)`, `(0,1)`. Original, candidate and
instrumented native output matched exactly; sign/+0 projection matched reference
doubles bitwise. A maximum 32-device, 31-source/64-PWL-pair, 4,095-row, eight-trace
mixed fixture preserved order and distinct public labels despite duplicate native
NODEV headers. Instrumentation remained bounded. Exact binary/source/build
identities remain those of the approved TASK-036 proof. Private discovery receipts
stay outside the repository; acceptance must be retained in repeatable source tests
under the approved leases.

Before completion, repeat those cases using the final implementation, explicitly
verify unaffected columns and their zero signs remain bitwise unchanged, add
malformed projection/certificate and original/wrong-binary refusal tests, and prove
the largest admitted shapes and all actual text/table/copy owners under the
approved profile. Rejected requests must not launch native execution. Require
current-head CI and automatic review within the owner's review-round limit;
the owner merges. A failed proof or material contract deviation returns for review.

## Owner decision

The owner approved Design A and Design B with the stated bounded accounting and
lease extensions on 2026-11-03, preserving all activation/release gates. Approval authorizes scoped
implementation and corresponding frozen-contract updates only. It does not
authorize deployment, physics activation, credential rotation or changes to
unrelated workloads.
