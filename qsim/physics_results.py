"""Bounded numeric physics results, independent of native solvers and job authority."""
import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import io
import json
import math
import re
import zipfile

import numpy as np

__all__ = ("ArraySpec", "ResultPlan", "AllocationPlan", "PhysicsExecutionResult",
           "plan_result", "serialize_physics_result")


@dataclass(frozen=True)
class ArraySpec:
    name: str
    dtype: str
    shape: tuple[int, ...]
    units: str | None


@dataclass(frozen=True)
class ResultPlan:
    arrays: tuple[ArraySpec, ...]
    numeric_bytes: int
    npz_upper_bytes: int
    callback_upper_bytes: int


@dataclass(frozen=True)
class AllocationPlan:
    policy_revision: str
    algorithm: str
    dimensions: dict
    allocation_items: tuple
    peak_bytes: int
    coefficient_plan: dict
    result: ResultPlan


@dataclass(frozen=True)
class PhysicsExecutionResult:
    arrays: Mapping[str, np.ndarray]
    metadata: Mapping[str, object]
    work: Mapping[str, object]


def plan_result(arrays: Sequence[ArraySpec], *, metadata_characters: int,
                summary_bytes: int) -> ResultPlan:
    """Predict the complete uncompressed result envelope without allocating arrays."""
    if (not isinstance(arrays, Sequence) or len(arrays) > 63
            or type(metadata_characters) is not int or not 0 <= metadata_characters <= 65536
            or type(summary_bytes) is not int or not 0 <= summary_bytes <= 65536):
        raise ValueError("resource_limit: invalid result reservation")
    names = set()
    numeric_bytes = 0
    for item in arrays:
        if (not isinstance(item, ArraySpec) or type(item.name) is not str
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", item.name) is None
                or item.name == "metadata_json" or item.name in names
                or type(item.dtype) is not str
                or type(item.shape) is not tuple or len(item.shape) > 4
                or any(type(n) is not int or not 0 <= n <= np.iinfo(np.intp).max for n in item.shape)
                or (item.units is not None and type(item.units) is not str)):
            raise ValueError("invalid_result: invalid array specification")
        names.add(item.name)
        try:
            dtype = np.dtype(item.dtype)
        except (TypeError, ValueError):
            raise ValueError("invalid_result: unsupported dtype") from None
        if (not dtype.isnative or dtype.hasobject or dtype.fields is not None
                or not (dtype == np.dtype("float64") or dtype == np.dtype("complex128")
                        or (dtype.kind in "iu" and dtype.itemsize in (1, 2, 4, 8)))):
            raise ValueError("invalid_result: unsupported dtype")
        numeric_bytes += math.prod(item.shape) * dtype.itemsize
        if numeric_bytes > 6 * 1024**2:
            raise ValueError("resource_limit: numeric result payload")
    npz_upper = numeric_bytes + 4*metadata_characters + 4352*(len(arrays)+1) + 1024
    callback_upper = 4*((npz_upper+2)//3) + summary_bytes + 256
    if npz_upper > 9*1024**2 or callback_upper > 12*1024**2:
        raise ValueError("resource_limit: result transport envelope")
    return ResultPlan(tuple(arrays), numeric_bytes, npz_upper, callback_upper)


def _validate_array(array, spec):
    if (type(array) is not np.ndarray or array.dtype.hasobject
            or array.shape != spec.shape or array.dtype != np.dtype(spec.dtype)
            or not np.isfinite(array).all()):
        raise ValueError("invalid_result: array differs from finite numeric plan")


def _validate_metadata(value):
    """Preflight finite JSON and its UTF-8 bytes without encoding a large tree."""
    if (value is None or type(value) in (str, bool, int)
            or (type(value) is float and math.isfinite(value))):
        if type(value) is str and len(value) > 65536:
            raise ValueError("resource_limit: result metadata")
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
    elif type(value) is list:
        size = 2 + max(0, len(value)-1)
        for child in value:
            size += _validate_metadata(child)
            if size > 65536:
                raise ValueError("resource_limit: result metadata")
    elif type(value) is dict and all(type(key) is str for key in value):
        size = 2 + max(0, len(value)-1) + len(value)
        for key, child in value.items():
            size += _validate_metadata(key) + _validate_metadata(child)
            if size > 65536:
                raise ValueError("resource_limit: result metadata")
    else:
        raise ValueError("invalid_result: metadata must contain finite JSON values")
    if size > 65536:
        raise ValueError("resource_limit: result metadata")
    return size


def serialize_physics_result(result: PhysicsExecutionResult,
                             plan: ResultPlan) -> tuple[dict, str]:
    """Return the summary and base64 artifact for the existing success callback.

    The plan reserves metadata characters and summary bytes in its two envelope
    bounds. Recover and verify those reservations rather than trusting a manually
    assembled ResultPlan. Numerical adapters remain responsible for physical
    validation; this boundary verifies exact array and transport contracts.
    """
    if (not isinstance(plan, ResultPlan)
            or any(type(value) is not int for value in
                   (plan.numeric_bytes, plan.npz_upper_bytes, plan.callback_upper_bytes))
            or type(plan.arrays) is not tuple):
        raise ValueError("invalid_result: invalid result plan")
    metadata_reserve = plan.npz_upper_bytes - plan.numeric_bytes - 4352*(len(plan.arrays)+1) - 1024
    summary_reserve = plan.callback_upper_bytes - 4*((plan.npz_upper_bytes+2)//3) - 256
    if metadata_reserve < 0 or metadata_reserve % 4:
        raise ValueError("invalid_result: inconsistent metadata reservation")
    metadata_characters = metadata_reserve//4
    verified = plan_result(plan.arrays, metadata_characters=metadata_characters,
                           summary_bytes=summary_reserve)
    if verified != plan:
        raise ValueError("invalid_result: inconsistent result plan")
    if (not isinstance(result, PhysicsExecutionResult) or not isinstance(result.arrays, Mapping)
            or not isinstance(result.metadata, Mapping) or not isinstance(result.work, Mapping)
            or list(result.arrays) != [item.name for item in plan.arrays]):
        raise ValueError("invalid_result: result differs from planned members")
    for item in plan.arrays:
        _validate_array(result.arrays[item.name], item)
    metadata, work = dict(result.metadata), dict(result.work)
    if "work" in metadata:
        raise ValueError("invalid_result: work metadata is serializer-owned")
    metadata["work"] = work
    try:
        _validate_metadata(metadata)
        metadata_json = json.dumps(metadata, ensure_ascii=False, allow_nan=False,
                                   separators=(",", ":"), sort_keys=True)
        if (len(metadata_json) > metadata_characters
                or len(metadata_json.encode("utf-8")) > 65536):
            raise ValueError("resource_limit: result metadata")
        summary = {"arrays": {
            item.name: {"dtype": np.dtype(item.dtype).name, "shape": list(item.shape), "units": item.units}
            for item in plan.arrays}, "work": work}
        for key in ("engine", "operation", "solver", "schema_version", "engine_version", "precision", "backend"):
            if key in metadata:
                summary[key] = metadata[key]
        summary_encoded = json.dumps(summary, allow_nan=False).encode("utf-8")
    except (TypeError, UnicodeError, RecursionError):
        raise ValueError("invalid_result: metadata is not bounded JSON") from None
    if len(summary_encoded) > summary_reserve or len(summary_encoded) > 65536:
        raise ValueError("resource_limit: result summary")

    members = dict(result.arrays)
    members["metadata_json"] = np.asarray(metadata_json)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in members.items():
            with archive.open(name+".npy", "w") as stream:
                np.lib.format.write_array(stream, array, allow_pickle=False)
    raw = buffer.getvalue()
    if len(raw) > plan.npz_upper_bytes:
        raise ValueError("resource_limit: encoded NPZ exceeds plan")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if (len(infos) != len(members)
                or len(raw) - sum(info.file_size for info in infos) > 256*len(infos)+1024):
            raise ValueError("resource_limit: ZIP framing exceeds plan")
        for info, (name, array) in zip(infos, members.items()):
            with archive.open(info) as stream:
                version = np.lib.format.read_magic(stream)
                if version not in ((1, 0), (2, 0)):
                    raise ValueError("invalid_result: unsupported NPY header")
                reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                          else np.lib.format.read_array_header_2_0)
                shape, _, dtype = reader(stream)
                if (info.filename != name+".npy" or info.compress_type != zipfile.ZIP_STORED
                        or stream.tell() > 4096 or shape != array.shape or dtype != array.dtype
                        or info.file_size != stream.tell()+array.nbytes):
                    raise ValueError("invalid_result: NPY member differs from plan")
    artifact = base64.b64encode(raw).decode("ascii")
    callback_bytes = len(json.dumps({"status": "SUCCEEDED", "summary": summary,
                                    "artifact": artifact}, allow_nan=False).encode("utf-8"))
    if (len(artifact) > 12*1024**2 or callback_bytes > plan.callback_upper_bytes
            or callback_bytes > 12*1024**2 or callback_bytes > 16*1024**2):
        raise ValueError("resource_limit: complete result callback exceeds plan")
    return summary, artifact
