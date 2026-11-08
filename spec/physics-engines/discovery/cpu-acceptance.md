# CPU physics integration evidence

Status: Tranche A implementation evidence on main baseline `410f062`, pending PR
review, canonical CI and owner merge. Production physics activation remains off.
This is not a deployed-release, live-SkyPilot or GPU acceptance receipt.

## Preserved execution contract

All three engines retain the approved CPU profile: 2 CPUs, 4 GiB container
memory, nonroot uid 1000, 3-GiB child address space, 12-MiB files and serial
native threads. Charged allocation is at most 2 GiB, including the 512-MiB native
import reserve, overlapping model/workspace/result owners and transport copies.
No solver algorithm, numerical tolerance, supported statement subset, global
queue cap, simulation timeout or total-work quota is added or relaxed.

## Certificate evidence

| Backend | Executed evidence | Largest reported charge / measured peak |
| --- | --- | --- |
| Lindblad | 15 normal-admission/native/serialization boundary cases, analytic checks, all complex HEEVD dimensions 1-256 in both vector modes, values-only GESDD dimensions 1-1024, and raw-input expansion refusal. | 1,684,075,944 charged bytes; 1,286,056 KiB peak RSS in the executor repeat. Final coordinator image repeat is recorded separately below. |
| scqubits export | 32 cases covering all four maximum raw bases, every allowed operator-count boundary and every individual operator at its largest accepted return size. Existing spectra/matrix-element certificates remain unchanged. | 759,900,716 charged bytes; 311,948 KiB peak RSS. |
| JoSIM | Seven exact frozen cases and seven capacity cases under the accepted Bookworm source/binary certificate. The longest retry case covers 264,765,429 attempted native steps. | Bounded history 229,728 bytes for that long case; this is a history measurement, not total process RAM. |

These bounds are source/algorithm-derived, not fitted RSS multipliers. The
Lindblad ledger includes per-segment QobjEvo copies, dense and DIA representations,
CSR add capacity up to twice the dense nonzero count, DIA add diagonal capacity,
Liouville/dissipator construction, retained feedback-cycle workspaces, explicit
RK stage/state owners and full output/serialization overlap. The stress inventory
includes 16 distinct nonconstant dense ME generators with 65,536 coefficient and
output knots, rather than relying on a zero/constant compressed case.

Frozen workspace bounds are tied to 77 installed native/source file digests.
HEEVD bounds cover complex work n²+33n+1, real work 2n²+5n+1 and integer work
5n+3. Values-only GESDD complex work is bounded by 66n, with wrapper-owned real
7n/integer 8n. Source references include SciPy commit
`c2df8ace0d9859f090e79058b2bd2ffc584f19e6`,
`scipy/linalg/flapack_gen.pyf.src` (SHA-256
`680631ba0dad285300d01b90bc49e08e834c28753dab141a9e4f44ae618f9fef`)
and `scipy/linalg/src/_matfuncs_expm.c` (SHA-256
`accbf76ec32c090a0ce30ed0853b35f9bd83bdd672ac6b3bffcc11ef54c912f9`).
The latter's 6n²+4n complex scratch plus pivots/input/output is charged for
matrix-exponential state construction. Model comments identify the matching
QuTiP retained-copy and sparse-capacity source mechanisms.

The raw-input preflight uses a lower bound of the existing charged-owner term:
each normalized complex cell has 1,328 canonical-owner bytes, charged four times.
It rejects only when this lower bound alone already exceeds 2 GiB. A roughly
4-MiB JSON payload containing 32 maximum matrices is refused before complex-leaf
or native-array construction. This does not assert physical heap usage equals
5,312 bytes per cell and does not impose a separate cell/language cap.

Final model source SHA-256 identities:

- Lindblad: `ee33f1448303fe0fa992864c42dcf6849ca9602a280ce36abcb0ce1cbcb196c4`.
- scqubits: `7ceaa98580f02a07b4187f5f92b2864079f31503eb80d857649c47c3a445ab71`.
- JoSIM: `fe790d45ae7345a0e99b0e117521fa67e027082bbcb9e6a14b9e98390dff48f9`.

JoSIM executable SHA-256 remains
`0bca2802d5e152a1b4325cc4a9f4e8e5b01331d705d45ea757c72e0cb1afe676`.
Its source/patch/frozen traces are unchanged. Export's additional certificate is
`04bb1ae263260aed485690d89509c4c00846e5af7d55149e4b0ded344d98540a`.

## Joined service contract

New version-2 dispatch context retains the admitted result contract and native
binary/source/projection identities in addition to the allocation-plan digest,
algorithm, policy and full worker profile. Version-1 records are not rewritten;
bridging requires an exactly reproduced original plan. Public canonical requests
and owner-scoped idempotency identities are unchanged.

The authenticated physics input response and private child envelope carry the
request and stored context separately. The outer worker compares stored and
launched context before RUNNING; the child recomputes admission and verifies the
installed source manifest and frozen native files before adapter execution.
No callback URL/token or controller environment enters the numerical child.
An image cannot embed its own final digest: local source/native manifests are
not presented as registry or live-SkyPilot attestation.

Physics callbacks enforce stored shape/dtype/member/byte bounds before loading
NPZ arrays with `allow_pickle=False`. Object arrays, duplicate/traversal members,
compression, nonfinite values, incompatible backend/precision/policy/algorithm,
unknown/corrupt context and JoSIM trace/unit/native-identity mismatches are
refused. Completed callback replay preserves the existing terminal outcome.
Circuit callbacks retain their prior compatibility path.

The controller persists acknowledged native job identity and asynchronous queue
observation identity. Read failures preserve ownership and explicitly report
unverified completion; API-request expiration does not erase an acknowledged
native job. Confirmed terminal completion without a committed result becomes a
bounded failure, never a duplicate execution. Setup/driver failures are not warm
reused. Confirmed absent clusters can be cleaned without inventing native success.

## Repeatable verification

`bash tools/ci/check-worker.sh` is the mandatory built-image entry point. It runs
existing startup/circuit checks, actual packaged physics children, the seven
frozen/seven capacity cases, offline JoSIM source rebuild/ABI checks, all CPU
admission/workspace/preflight cases and the joined HTTP/native harness. Test code
and diagnostic executables are not packaged into the production worker.

The joined harness executes real uvicorn HTTP/auth, production admission,
controller task construction, packaged outer worker, native children, HTTP
callbacks, polling/SSE, result download and encrypted native backup/restore.
Only placement and explicit transport-fault injection are synthetic. Its local
Docker image ID and synthetic immutable scheduler reference are recorded as
separate JUnit properties. No cluster/GPU allocation is performed.

The 23 joined cases include all Lindblad operation families and seeded repeat,
all four scqubits spectra/matrix families and four numeric export-to-Lindblad
sequences, JoSIM SFQ, circuit/physics pool separation, context alterations,
agreeing-but-forged stored/launch digests, and real final HTTP callback loss.
The lost-callback relay returns five immediate 503 responses under the existing
worker retry behavior; native failure then reconciles to cleaned FAILED with no
rerun or result. Test harness deadlines are not product simulation timeouts.
Actual complex native states with nonzero imaginary components and Unicode
metadata are encrypted/restored with contexts, reservations and unchanged
synthetic pending-invitation material to a separate destination.

Host numerical unit tests may replace only the installed-provenance loader in
a clearly labeled source-checkout fixture. They do not replace admission plans
or qualify installed-image acceptance. Required image/native tests execute the
real manifest/certificates without those mocks. Ordinary host skips do not count
as image, hardware or release acceptance.

## Remaining gates

Canonical CI must pass on the actual selected Standpunkt or existing hosted
fallback runner and on the mandatory hosted image lane. The broad local host
suite reports 1,947 passes, 62 skips, 36 failures and four import errors; its 40
failing identifiers match the independently reproduced main baseline, including
OpenMP static-TLS exhaustion in Aer/qsim. That host run is not reported green and
no dependency/TLS workaround or weakened assertion is introduced.

Tranche B owns versioned physics-aware release acceptance, tested public docs,
the explicit empty production GPU matrix and combined security/evidence audit.
The final live rollout still requires exact current-main artifacts, real fresh
backup gates, restricted smoke identity, actual SkyPilot/native/image evidence
and cleanup before general admission. Existing credentials, release settings,
backup gates and unrelated workloads remain unchanged.


## Final coordinator image repeat

The complete mandatory image script exited 0. Tested local Docker image ID:
`sha256:ab72787f156c56c1436000142e72be002b59cf473d24af6c892f668eed9f4ffb`.
Its 23 joined tests have 0 failures, 0 errors and 0 skips in JUnit. The three CPU proof
methods include all 15 normal native cases, full workspace queries and the large
raw-input preflight; maximum charged allocation is 1,684,075,944 bytes and measured
peak RSS 1,285,020 KiB. Existing native source rebuild, 7 frozen/7 capacity cases,
all packaged physics families and legacy circuit/startup checks pass. The
synthetic scheduler reference is explicitly labeled in the same JUnit receipt;
real scheduling/image/CD attestation remains a later live gate.

Private input transport explicitly negotiates execution-context protocol 2 with
`X-WQSIM-Execution-Context`. Previously accepted version-1 jobs retain the flat
input expected by their pinned older worker when that header is absent. New
workers receive the sealed envelope, including for version-1 jobs, and still
verify its original admission digest and profile. Unknown protocol versions
are rejected. Public request bodies and idempotency hashes are unchanged.
