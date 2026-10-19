"""LaminarAuth v1 metadata semantics. Structural checks never establish trust.

Adapted from LaminarAuth conformance reference, MIT license in contract/LICENSE.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath

CONTRACT_COMMIT = "6f4674e2ae02a6e9fe8ced33117ac5a17b77fc78"
SCHEMA = json.loads((Path(__file__).parent / "contract/schema.json").read_text())
MAX_INT = 9007199254740991

def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    """The restricted backup-json-v1 metadata encoding, not general JCS."""
    def check(item):
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            require(0 <= item <= MAX_INT, "unsafe integer")
        elif type(item) is str:
            require(all(32 <= ord(char) <= 126 for char in item), "non-ASCII metadata")
        elif type(item) is list:
            for child in item:
                check(child)
        elif type(item) is dict:
            for key, child in item.items():
                require(type(key) is str, "non-string key")
                check(key)
                check(child)
        else:
            raise ValueError("unsupported value")
    check(value)
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_metadata(raw):
    require(type(raw) in (str, bytes), "metadata input must be text or UTF-8 bytes")
    if type(raw) is bytes:
        raw = raw.decode("utf-8", errors="strict")
    require(not raw.startswith("\ufeff"), "metadata BOM is forbidden")

    def integer(token):
        require(not token.startswith("-"), "negative integer token")
        number = int(token)
        require(number <= MAX_INT, "unsafe integer")
        return number

    def unsupported(token):
        raise ValueError("unsupported number " + token)

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate key")
            result[key] = value
        return result

    value = json.loads(raw, parse_int=integer, parse_float=unsupported,
                       parse_constant=unsupported, object_pairs_hook=pairs)
    canonical(value)
    return value


def digest(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def binding(record):
    return {key: record[key] for key in
            ("record_id", "evidence_type", "schema_id", "contract_version")} | digest(canonical(record))


def timestamp(value):
    # JSON Schema format alone may accept offsets/fractions; the contract does not.
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    require(parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value, "noncanonical UTC")
    return parsed


def validate_record(record):
    from jsonschema import Draft202012Validator, FormatChecker
    validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
    canonical(record)
    errors = list(validator.iter_errors(record))
    require(not errors, "schema failure")
    if record["evidence_type"] != "final_result":
        timestamp(record["created_at"])
        timestamp(record["context"]["boundary"]["source_at"])
        if record["evidence_type"] == "full_restore":
            for proof in record["route_proofs"]:
                timestamp(proof["created_at"])
        for field in ("expires_at", "accepted_at"):
            if field in record:
                timestamp(record[field])


def safe_path(path):
    require(path and not path.startswith("/") and "\\" not in path, "unsafe path")
    require(all(part not in ("", ".", "..") for part in path.split("/")), "unsafe path")
    require(str(PurePosixPath(path)) == path, "noncanonical path")


def verify_manifest(manifest):
    context = manifest["context"]
    dependencies = manifest["dependencies"]
    require(dependencies["kind"] == context["set_type"], "dependency owner mismatch")
    files = manifest["inventory"]
    inventory = {item["path"]: item for item in files}
    require(len(inventory) == len(files), "duplicate inventory path")
    for item in files:
        safe_path(item["path"])
        require(item["kind"] == "regular", "nonregular file")
    references = []

    def needed(path, role):
        safe_path(path)
        require(path in inventory and inventory[path]["role"] == role, "missing dependency/role")
        references.append(path)

    kind = dependencies["kind"]
    if kind == "wqs":
        for role in ("database", "invitation_registry", "pending_delivery_key", "runner_ledger",
                     "image_inventory", "registry_access"):
            needed(dependencies[role], role)
        for role in ("results", "release_receipts"):
            for path in dependencies[role]:
                needed(path, role)
        require(dependencies["pvc_identity"] == context["source"]["storage"], "wrong current PVC")
    elif kind == "laminarauth":
        for role in ("rdb", "aof_manifest"):
            needed(dependencies[role], role)
        for role in ("aof_files", "log_segments"):
            for path in dependencies[role]:
                needed(path, role)
        require(dependencies["log_end"] == context["boundary"]["cursor"], "incomplete log boundary")
        projection = dependencies["expected_projection"]
        needed(projection["path"], "expected_projection")
        require(projection["boundary"] == dependencies["log_end"], "wrong reference boundary")
        require(projection["source_workload"] == context["source"]["workload"], "wrong reference source")
        require(all(projection[key] == inventory[projection["path"]][key]
                    for key in ("sha256", "size")), "projection content mismatch")
    else:
        for role in ("datastore", "server_token", "runtime_inventory"):
            needed(dependencies[role], role)
        encryption = dependencies["encryption"]
        if encryption["state"] == "enabled":
            needed(encryption["config"], "encryption_config")
            for path in encryption["key_material"]:
                needed(path, "encryption_key_material")
    require(len(references) == len(set(references)), "aliased/duplicate required dependency")
    require(set(references) == set(inventory), "unclassified inventory")


