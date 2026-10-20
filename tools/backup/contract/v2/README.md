# LaminarAuth recovery contract v2

Schema and portable fixtures are vendored byte-for-byte from
[LaminarAuth 1b87f5c](https://github.com/Wehden/LaminarAuth/tree/1b87f5c50b13120629f5d853579cfeae31154a27).
Exact paths and SHA-256 hashes are recorded in `provenance.json` and tested.
LaminarAuth's MIT attribution is retained in [../LICENSE](../LICENSE).

The v1 schema/provenance and historical fixtures remain unchanged in their
original locations. WQS v2 runtime semantics are in `tools/backup/v2.py`; actual
native decrypt/load tests are separate from portable format conformance.
A published source pin does not mean the upstream PR is merged or deployed.
