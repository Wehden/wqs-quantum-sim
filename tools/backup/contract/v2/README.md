# LaminarAuth recovery contract v2

Schema and portable fixtures are vendored byte-for-byte from
[LaminarAuth fdec5b8](https://github.com/Wehden/LaminarAuth/tree/fdec5b863579fd775a776eb93f7cc1ed910cf015).
Exact paths and SHA-256 hashes are recorded in `provenance.json` and tested.
LaminarAuth's MIT attribution is retained in [../LICENSE](../LICENSE).

The v1 schema/provenance and historical fixtures remain unchanged in their
original locations. WQS v2 runtime semantics are in `tools/backup/v2.py`; actual
native decrypt/load tests are separate from portable format conformance.
A published source pin does not mean the upstream PR is merged or deployed.
