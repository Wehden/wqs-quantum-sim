# Read-only welcome WAL verification

Implementation pin: cb84dd6d5ef4aa0187033c5c483ee247d6f43f94. Documentation-only review receipts may follow without changing these runtime bytes.

## Executor checks

`python -m pytest -q tests/test_welcome_wal_lifecycle.py tests/test_management_provider.py tests/test_welcome_provider.py tests/test_welcome_evidence.py tests/test_approvals.py`:86 passed. Six lifecycle cases cover query-only enforcement, no held transaction, writer/checkpoint progress, fresh second-process reads and normal/exception native-admin shutdown with notifications enabled/disabled.

A network-disabled test used the actual packaged native-admin candidate at UID1000 and a separate immutable reviewed welcome image on a genuine read-only bind mount. The reader succeeded, then observed an independent committed insert after TRUNCATE checkpoint, and native shutdown released sidecars. Production files were not mounted. This is stronger evidence than URI mode=ro on a writable mount.

Image readback compared all810 `/app` files against the deployed native-admin base: only qsim/adminweb.py and qsim/store.py changed, both matching the frozen source.808 other files remained byte-identical. Packaged imports passed at the existing image UID. The candidate was not published or deployed during these checks.

## Independent reviews

Root's independent test review reported150 targeted tests passing, plus read-only container and cancellation checks. Simplification, security, docs and final correctness reviews all passed with no findings. No token policy, credential material, provider mount permissions, public endpoint, release/bootstrap flag or physics files changed.

## Pending live acceptance

Required PR CI and human source merge remain pending. The exact private native-admin image-only promotion still needs concrete ship approval. After promotion, require native readiness, unchanged existing registry/token fields and actual welcome read-only PVC/WAL readiness, TLS/SAN/auth denials, capabilities and existing eligible recipient reads before central activation. Existing token validation is separately tracked. No production mail or key creation is part of these checks.
