"""The physics result boundary admits complete, pickle-free callback payloads."""
import ast
import base64
from dataclasses import replace
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

from qsim.physics_results import (
    AllocationPlan, ArraySpec, PhysicsExecutionResult, ResultPlan,
    plan_result, serialize_physics_result,
)


def spec(name="values", dtype="float64", shape=(3,), units=None):
    return ArraySpec(name, dtype, shape, units)


def plan(arrays=None, metadata_characters=65536, summary_bytes=65536):
    return plan_result(tuple([spec()] if arrays is None else arrays),
                       metadata_characters=metadata_characters, summary_bytes=summary_bytes)


def result(arrays=None, metadata=None, work=None):
    return PhysicsExecutionResult(
        {"values": np.asarray([1., 2., 3.])} if arrays is None else arrays,
        {"engine": "lindblad", "operation": "evolve"} if metadata is None else metadata,
        {"set_state": "1", "rhs_evaluations_upper_bound": "9007199254740993"} if work is None else work,
    )


def unpack(value, expected_plan):
    summary, artifact = serialize_physics_result(value, expected_plan)
    raw = base64.b64decode(artifact, validate=True)
    assert len(raw) <= expected_plan.npz_upper_bytes
    assert len(artifact) == 4 * ((len(raw) + 2) // 3)
    payload = {"status": "SUCCEEDED", "summary": summary, "artifact": artifact}
    assert len(json.dumps(payload).encode()) <= expected_plan.callback_upper_bytes
    assert len(json.dumps(summary).encode()) <= 65536
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert all(member.compress_type == zipfile.ZIP_STORED for member in archive.infolist())
    with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    metadata = json.loads(arrays.pop("metadata_json").item())
    return summary, metadata, arrays, raw


def test_plan_arithmetic_and_allocation_contract():
    planned = plan([spec(shape=(2, 3), dtype="complex128")], 80, 160)
    assert planned == ResultPlan((spec(shape=(2, 3), dtype="complex128"),), 96,
                                96 + 4*80 + 4352*2 + 1024,
                                4*((96 + 4*80 + 4352*2 + 1024 + 2)//3) + 160 + 256)
    allocation = AllocationPlan("cpu-v1", "vern7", {"D": 3}, (), 1024, {}, planned)
    assert allocation.result == planned


def test_complete_complex_result_and_work_round_trip():
    values = np.asarray([1+2j, -3+4j, 5-6j], dtype=np.complex128)
    _, metadata, arrays, _ = unpack(result({"values": values}), plan([spec(dtype="complex128")]))
    np.testing.assert_array_equal(arrays["values"], values)
    assert metadata["work"]["rhs_evaluations_upper_bound"] == "9007199254740993"


def test_metadata_only_and_zero_length_arrays_are_safe():
    _, metadata, arrays, _ = unpack(result({}, {"operation": "steadystate"}, {}), plan([]))
    assert arrays == {} and metadata["work"] == {}
    _, _, arrays, _ = unpack(result({"values": np.empty((0, 3))}), plan([spec(shape=(0, 3))]))
    assert arrays["values"].shape == (0, 3)


@pytest.mark.parametrize("dtype", ["int8", "uint8", "int64", "uint64", "float64", "complex128"])
def test_supported_numeric_dtypes(dtype):
    _, _, arrays, _ = unpack(result({"values": np.arange(3).astype(dtype)}), plan([spec(dtype=dtype)]))
    assert arrays["values"].dtype == np.dtype(dtype)


def test_noncontiguous_array_and_file_member_name():
    array = np.arange(12., dtype=np.float64).reshape(3, 4)[:, ::2]
    _, _, arrays, _ = unpack(result({"file": array}), plan([spec("file", shape=(3, 2))]))
    np.testing.assert_array_equal(arrays["file"], array)


@pytest.mark.parametrize("name", ["", "metadata_json", "../values", "a/b", "v.npy", "x"*65, "é", "x\x00"])
def test_invalid_member_names_refused_before_array_allocation(name, monkeypatch):
    monkeypatch.setattr(np, "empty", lambda *a, **k: pytest.fail("unexpected allocation"))
    with pytest.raises(ValueError):
        plan([spec(name)])


@pytest.mark.parametrize("shape", [(-1,), (True,), (1.5,), (1,)*5, (10**100,), (0, 10**100)])
def test_invalid_or_huge_dimensions_refused(shape):
    with pytest.raises(ValueError):
        plan([spec(shape=shape)])


@pytest.mark.parametrize("dtype", ["object", "float32", "complex64", "bool", "U3", "S3", "V8", ">f8"])
def test_unsafe_or_noncanonical_dtype_refused(dtype):
    with pytest.raises(ValueError):
        plan([spec(dtype=dtype)])


def test_exact_payload_limit_with_incompressible_bytes():
    values = np.random.default_rng(42).integers(0, 256, size=6*1024**2, dtype=np.uint8)
    planned = plan([spec(dtype="uint8", shape=values.shape)])
    _, _, _, raw = unpack(result({"values": values}), planned)
    assert len(raw) > values.nbytes
    with pytest.raises(ValueError):
        plan([spec(dtype="uint8", shape=(values.size+1,))])


def test_member_limit_names_headers_and_archive_overhead():
    specs = [spec("x"*62+str(i), shape=(0,)) for i in range(10)]
    specs += [spec("member_"+str(i), shape=(0,)) for i in range(53)]
    planned = plan(specs)
    arrays = {item.name: np.empty(item.shape) for item in specs}
    _, _, _, raw = unpack(result(arrays), planned)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert len(archive.infolist()) == 64
        for member in archive.infolist():
            with archive.open(member) as stream:
                version = np.lib.format.read_magic(stream)
                reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                          else np.lib.format.read_array_header_2_0)
                shape, _, dtype = reader(stream)
                assert stream.tell() <= 4096
                assert stream.tell() + np.prod(shape, dtype=object)*dtype.itemsize == member.file_size
        assert len(raw) - sum(item.file_size for item in archive.infolist()) <= 256*64+1024
    with pytest.raises(ValueError):
        plan(specs+[spec("too_many", shape=(0,))])
    with pytest.raises(ValueError):
        plan([spec("same"), spec("same")])


@pytest.mark.parametrize("arrays", [
    {}, {"extra": np.arange(3.)}, {"values": np.arange(4.)},
    {"values": np.arange(3, dtype=np.float32)},
    {"values": np.asarray([1, object(), 3], dtype=object)},
    {"values": np.asarray([1., np.nan, 3.])},
    {"values": np.asarray([1., np.inf, 3.])},
    {"values": [1., 2., 3.]},
])
def test_actual_arrays_must_match_safe_finite_plan(arrays):
    with pytest.raises(ValueError):
        serialize_physics_result(result(arrays), plan())


def test_complex_nonfinite_rejected():
    with pytest.raises(ValueError):
        serialize_physics_result(result({"values": np.asarray([1j, complex(2, float("nan")), 3j])}),
                                 plan([spec(dtype="complex128")]))


@pytest.mark.parametrize("field,value", [
    ("metadata", {"bad": float("nan")}), ("work", {"bad": float("inf")}),
    ("metadata", {1: "key"}), ("metadata", {"bad": object()}),
    ("work", {"bad": np.int64(2)}), ("metadata", {"bad": (1, 2)}),
    ("metadata", {"work": {}}),
])
def test_metadata_and_work_are_finite_json_only(field, value):
    value_result = replace(result(), **{field: value})
    with pytest.raises(ValueError):
        serialize_physics_result(value_result, plan())


def test_cyclic_metadata_is_bounded_failure():
    cycle = []; cycle.append(cycle)
    with pytest.raises(ValueError):
        serialize_physics_result(result(metadata={"cycle": cycle}), plan())


def test_unicode_metadata_counts_characters_and_utf8_separately():
    metadata = {"label": "🙂é\"\n\\"*800}
    _, decoded, _, _ = unpack(result(metadata=metadata), plan())
    assert decoded["label"] == metadata["label"]
    with pytest.raises(ValueError):
        serialize_physics_result(result(metadata={"label": "🙂"*20000}), plan())
    with pytest.raises(ValueError):
        serialize_physics_result(result(metadata={"label": "\ud800"}), plan())


def test_exact_metadata_limit_and_beyond():
    # Metadata includes the serializer-owned empty work object.
    overhead = len(json.dumps({"label": "", "work": {}}, ensure_ascii=False, separators=(",", ":")))
    metadata = {"label": "x"*(65536-overhead)}
    _, decoded, _, _ = unpack(result(metadata=metadata, work={}), plan())
    assert len(json.dumps(decoded, ensure_ascii=False, separators=(",", ":")).encode()) == 65536
    with pytest.raises(ValueError):
        serialize_physics_result(result(metadata={"label": metadata["label"]+"x"}, work={}), plan())


def test_actual_metadata_and_summary_must_fit_reserved_plan():
    with pytest.raises(ValueError):
        serialize_physics_result(result(), plan(metadata_characters=1))
    with pytest.raises(ValueError):
        serialize_physics_result(result(), plan(summary_bytes=1))


@pytest.mark.parametrize("field,value", [("metadata_characters", -1), ("metadata_characters", 65537),
                                        ("summary_bytes", True), ("summary_bytes", 65537)])
def test_invalid_reservations(field, value):
    with pytest.raises(ValueError):
        plan(**{field: value})


@pytest.mark.parametrize("field", ["numeric_bytes", "npz_upper_bytes", "callback_upper_bytes"])
def test_forged_or_understated_plan_cannot_emit_success(field):
    planned = plan()
    with pytest.raises(ValueError):
        serialize_physics_result(result(), replace(planned, **{field: 1}))


def test_callback_summary_uses_actual_escaped_bytes():
    # ASCII escaping in the existing worker can exceed UTF-8 size considerably.
    with pytest.raises(ValueError):
        serialize_physics_result(result(work={"note": "🙂"*5500}), plan())


def test_summary_exact_limit_and_plan_reservation_boundary():
    summary, _ = serialize_physics_result(result(metadata={}, work={}), plan())
    size = len(json.dumps(summary).encode())
    serialize_physics_result(result(metadata={}, work={}), plan(summary_bytes=size))
    with pytest.raises(ValueError):
        serialize_physics_result(result(metadata={}, work={}), plan(summary_bytes=size-1))
    with_units = [spec(units="")]
    summary, _ = serialize_physics_result(result(metadata={}, work={}), plan(with_units))
    padding = 65536-len(json.dumps(summary).encode())
    exact = [spec(units="x"*padding)]
    summary, _, _, _ = unpack(result(metadata={}, work={}), plan(exact))
    assert len(json.dumps(summary).encode()) == 65536
    with pytest.raises(ValueError):
        serialize_physics_result(result(metadata={}, work={}), plan([spec(units="x"*(padding+1))]))


def test_absolute_declared_envelope_maxima():
    arrays = [spec("large", "uint8", (6*1024**2,))]
    arrays += [spec("empty_"+str(i), shape=(0,)) for i in range(62)]
    planned = plan(arrays)
    assert planned.numeric_bytes == 6291456
    assert planned.npz_upper_bytes == 6833152
    assert planned.callback_upper_bytes == 9176664


@pytest.mark.parametrize("padding", [1, 20000, 400000])
def test_encoder_cannot_silently_violate_npz_or_member_plan(monkeypatch, padding):
    write = np.lib.format.write_array
    def malformed(stream, array, **options):
        write(stream, array, **options)
        stream.write(b"x"*padding)
    monkeypatch.setattr(np.lib.format, "write_array", malformed)
    with pytest.raises(ValueError):
        serialize_physics_result(result(), plan())


def test_oversize_metadata_is_refused_before_npz_encoding(monkeypatch):
    monkeypatch.setattr(np.lib.format, "write_array", lambda *a, **k: pytest.fail("unexpected encoder call"))
    for metadata in ({"large": "x"*65537}, {"items": ["x"*50000]*2}):
        with pytest.raises(ValueError):
            serialize_physics_result(result(metadata=metadata), plan())


@pytest.mark.parametrize("arrays", [
    [spec("times", shape=(2,), units="ns"), spec("expectations", "complex128", (2, 1)),
     spec("state_times", shape=(1,)), spec("states", "complex128", (1, 2))],
    [spec("state_times", shape=(2,)), spec("states", "complex128", (2, 2, 2))],
    [spec("expectations", "complex128", (2,)), spec("steady_state", "complex128", (2, 2))],
    [spec("propagators", "complex128", (2, 4, 4))],
    [spec("eigenvalues", shape=(3,)), spec("n_operator", "complex128", (3, 3))],
    [spec("times", shape=(3,), units="s"), spec("traces", shape=(3, 2))],
])
def test_engine_shape_plans_round_trip_without_reinterpretation(arrays):
    values = {item.name: np.zeros(item.shape, dtype=item.dtype) for item in arrays}
    _, _, actual, _ = unpack(result(values), plan(arrays))
    assert {name: value.shape for name, value in actual.items()} == {name: value.shape for name, value in values.items()}


def test_api_safe_imports_and_six_public_exports():
    source = Path(__file__).resolve().parents[1]/"qsim"/"physics_results.py"
    tree = ast.parse(source.read_text())
    imports = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not imports & {"qutip", "scqubits", "scipy", "cupy", "jax", "sky", "subprocess"}
    import qsim.physics_results as module
    assert set(module.__all__) == {"ArraySpec", "ResultPlan", "AllocationPlan", "PhysicsExecutionResult",
                                   "plan_result", "serialize_physics_result"}
