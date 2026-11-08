"""Bounded numeric physics results, independent of native solvers and job authority."""
import base64
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import io
import hashlib
import json
import math
import re
import zipfile

import numpy as np

__all__ = ("ArraySpec", "ResultPlan", "AllocationPlan", "PhysicsExecutionResult",
           "plan_result", "serialize_physics_result", "allocation_plan_digest",
           "validate_execution_context", "validate_physics_callback", "make_execution_context")


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



def allocation_plan_digest(plan: AllocationPlan) -> str:
    return hashlib.sha256(json.dumps(asdict(plan), sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def make_execution_context(plan: AllocationPlan, profile: Mapping) -> dict:
    return {'version':2,'worker_profile':dict(profile),'admission_policy_revision':plan.policy_revision,
            'allocation_plan_digest':allocation_plan_digest(plan),'algorithm':plan.algorithm,
            'result_plan':asdict(plan.result),'native_identity':{key:plan.coefficient_plan[key]
                for key in ('binary_sha256','source_commit','projection_revision') if key in plan.coefficient_plan}}


def _context_shape(context, *, callback=False):
    fields = {'version','worker_profile','admission_policy_revision','allocation_plan_digest','algorithm'}
    if type(context['version']) is not int or context['version'] not in (1,2):
        raise ValueError()
    if context['version']==2 or callback:
        fields.update(('result_plan','native_identity'))
    if set(context)!=fields or re.fullmatch(r'[0-9a-f]{64}',context['allocation_plan_digest']) is None:
        raise ValueError()
    if any(not isinstance(context[key],str) or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',context[key]) is None
           for key in ('admission_policy_revision','algorithm')):
        raise ValueError()
    profile = context['worker_profile']
    if (set(profile) != {'version','id','backend','policy_revision','algorithm_set_revision','resources','isolation','precision','image'}
            or type(profile['version']) is not int or profile['version']!=1
            or profile['id']!='physics-cpu-v1' or profile['backend']!='cpu'
            or profile['policy_revision']!='physics-placement-v1'
            or profile['algorithm_set_revision']!='physics-cpu-algorithms-v1'
            or profile['precision']!=['float64','complex128']
            or set(profile['resources'])!={'cpus','memory','infra'}
            or type(profile['resources']['cpus']) is not int or profile['resources']['cpus']!=2
            or type(profile['resources']['memory']) is not int or profile['resources']['memory']!=4
            or any(type(value) is not int for value in profile['isolation'].values())
            or profile['isolation']!={'address_space_bytes':3*1024**3,'file_bytes':12*1024**2,'native_threads':1,'uid':1000}
            or not isinstance(profile['image'],str) or profile['image'].startswith('docker:')
            or re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}',profile['image']) is None
            or re.fullmatch(r'[a-zA-Z0-9_./:-]{1,128}',profile['resources']['infra']) is None):
        raise ValueError()
    if 'native_identity' in context:
        identity = context['native_identity']
        if not isinstance(identity,dict) or set(identity)-{'binary_sha256','source_commit','projection_revision'}:
            raise ValueError()
        for key,value in identity.items():
            pattern = {'binary_sha256':r'[0-9a-f]{64}','source_commit':r'[0-9a-f]{40}',
                       'projection_revision':r'[A-Za-z0-9._:-]{1,128}'}[key]
            if not isinstance(value,str) or re.fullmatch(pattern,value) is None:
                raise ValueError()


def validate_execution_context(context: Mapping, plan: AllocationPlan, *, expected_profile: Mapping):
    """Verify admitted ownership and installed policy without importing authority."""
    try:
        _context_shape(context)
        if (context['admission_policy_revision'] != plan.policy_revision
                or context['algorithm'] != plan.algorithm
                or context['allocation_plan_digest'] != allocation_plan_digest(plan)):
            raise ValueError()
        if context['version'] == 2 and json.dumps(context['result_plan'],sort_keys=True,allow_nan=False) != json.dumps(asdict(plan.result),sort_keys=True):
            raise ValueError()
        if context['version']==2 and context['native_identity'] != make_execution_context(plan,context['worker_profile'])['native_identity']:
            raise ValueError()
        profile = context['worker_profile']
        expected = json.loads(json.dumps(expected_profile,allow_nan=False))
        image,infra = profile['image'],profile['resources']['infra']
        expected['image'] = image
        expected['resources']['infra'] = infra
        if json.dumps(profile,sort_keys=True,allow_nan=False) != json.dumps(expected,sort_keys=True):
            raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
        raise ValueError('execution_policy_mismatch: admitted context differs from installed plan') from None


def _stored_result_plan(context):
    data = context['result_plan']
    if set(data) != {'arrays','numeric_bytes','npz_upper_bytes','callback_upper_bytes'}:
        raise ValueError()
    if type(data['arrays']) is not list or len(data['arrays']) > 63:
        raise ValueError()
    specs = tuple(ArraySpec(item['name'],item['dtype'],tuple(item['shape']),item['units']) for item in data['arrays'])
    if any(set(item) != {'name','dtype','shape','units'} for item in data['arrays']):
        raise ValueError()
    reserved = data['npz_upper_bytes'] - data['numeric_bytes'] - 4352*(len(specs)+1) - 1024
    summary = data['callback_upper_bytes'] - 4*((data['npz_upper_bytes']+2)//3) - 256
    if reserved < 0 or reserved % 4:
        raise ValueError()
    plan = plan_result(specs,metadata_characters=reserved//4,summary_bytes=summary)
    if json.dumps(asdict(plan),sort_keys=True) != json.dumps(data,sort_keys=True,allow_nan=False):
        raise ValueError()
    return plan


def validate_physics_callback(raw: bytes, summary: Mapping, request: Mapping, context: Mapping):
    """Validate the stored result contract before loading bounded numeric members."""
    try:
        _context_shape(context,callback=True)
        plan = _stored_result_plan(context)
        if (type(raw) is not bytes or len(raw) > plan.npz_upper_bytes
                or type(summary) is not dict or len(json.dumps(summary,allow_nan=False).encode()) > 65536):
            raise ValueError()
        expected = [item.name+'.npy' for item in plan.arrays]+['metadata_json.npy']
        shapes = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if ([item.filename for item in members] != expected
                    or any(item.compress_type != zipfile.ZIP_STORED or item.flag_bits & 1 for item in members)
                    or sum(item.file_size for item in members) > plan.npz_upper_bytes
                    or len(raw)-sum(item.file_size for item in members) > 256*len(members)+1024):
                raise ValueError()
            for member in members:
                with archive.open(member) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version not in ((1,0),(2,0)):
                        raise ValueError()
                    reader = (np.lib.format.read_array_header_1_0 if version == (1,0)
                              else np.lib.format.read_array_header_2_0)
                    shape,_,dtype = reader(stream,max_header_size=4096)
                    if (dtype.hasobject or dtype.fields is not None or not dtype.isnative
                            or any(type(n) is not int or n < 0 for n in shape)
                            or stream.tell() > 4096 or math.prod(shape)*dtype.itemsize != member.file_size-stream.tell()):
                        raise ValueError()
                    if member.filename == 'metadata_json.npy':
                        if shape != () or dtype.kind != 'U' or dtype.itemsize > 4*65536:
                            raise ValueError()
                    else:
                        spec = plan.arrays[expected.index(member.filename)]
                        if dtype != np.dtype(spec.dtype):
                            raise ValueError()
                        if request['engine'] == 'josim':
                            if len(shape) != len(spec.shape) or not 0 < shape[0] <= spec.shape[0] or shape[1:] != spec.shape[1:]:
                                raise ValueError()
                        elif shape != spec.shape:
                            raise ValueError()
                        shapes[spec.name] = shape
        if request['engine'] == 'josim' and shapes['times'][0] != shapes['traces'][0]:
            raise ValueError()
        with np.load(io.BytesIO(raw),allow_pickle=False) as arrays:
            for spec in plan.arrays:
                if not np.isfinite(arrays[spec.name]).all():
                    raise ValueError()
            metadata = json.loads(str(arrays['metadata_json']))
        _validate_metadata(metadata)
        if any(metadata.get(key)!=value for key,value in context['native_identity'].items()):
            raise ValueError()
        precision = {'lindblad':'complex128','scqubits':'float64/complex128','josim':'float64'}[request['engine']]
        if metadata.get('backend') != context['worker_profile']['backend'] or metadata.get('precision') != precision:
            raise ValueError()
        if request['engine'] in ('lindblad','scqubits') and any(
                metadata.get(key) != context[field] for key,field in
                (('policy_revision','admission_policy_revision'),('algorithm','algorithm'))):
            raise ValueError()
        if any(key in metadata and metadata[key] != context[field] for key,field in
               (('policy_revision','admission_policy_revision'),('algorithm','algorithm'))):
            raise ValueError()
        if 'solver' in request and metadata.get('solver') != request['solver']:
            raise ValueError()
        if request['engine'] == 'josim':
            if set(context['native_identity']) != {'binary_sha256','source_commit','projection_revision'}:
                raise ValueError()
            traces = [line.split()[1:] for line in request['netlist'].splitlines() if line.startswith('.PRINT ')]
            names = [dict(DEVV='V',DEVI='I',PHASE='P')[kind]+'('+name+')' for kind,name in traces]
            units = [dict(DEVV='V',DEVI='A',PHASE='rad')[kind] for kind,_ in traces]
            if metadata.get('trace_names') != names or metadata.get('trace_units') != units or metadata.get('time_units') != 's':
                raise ValueError()
        if (metadata.get('engine') != request['engine'] or metadata.get('operation') != request['operation']
                or type(metadata.get('schema_version')) is not int
                or metadata['schema_version'] != request['schema_version']):
            raise ValueError()
        expected_summary = {'arrays': {spec.name: {'dtype': np.dtype(spec.dtype).name,
            'shape': list(shapes[spec.name]), 'units':spec.units} for spec in plan.arrays},'work':metadata['work']}
        for key in ('engine','operation','solver','schema_version','engine_version','precision','backend'):
            if key in metadata:
                expected_summary[key] = metadata[key]
        if json.dumps(summary,sort_keys=True,allow_nan=False) != json.dumps(expected_summary,sort_keys=True,allow_nan=False):
            raise ValueError()
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,zipfile.BadZipFile,EOFError,UnicodeError,RecursionError):
        raise ValueError('invalid_result: archive differs from admitted result contract') from None
