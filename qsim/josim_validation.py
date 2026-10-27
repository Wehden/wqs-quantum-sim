"""Pure validation of the self-contained, numeric JoSIM transient subset."""

import math
import re
from typing import Any, Mapping


__all__ = ('validate_josim_netlist',)


_NUMBER = re.compile(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:E[+-]?\d+)?)(MEG|[TGMKUNPF])?([VAFH])?')
_SCALE = {'': 1, 'T': 1e12, 'G': 1e9, 'MEG': 1e6, 'K': 1e3,
          'M': 1e-3, 'U': 1e-6, 'N': 1e-9, 'P': 1e-12, 'F': 1e-15}
_IDENTIFIER = r'[A-Z][A-Z0-9_]{0,31}'


def _numeric_token(token: str, unit: str = '', *, admission: bool = True) -> float:
    match = _NUMBER.fullmatch(token)
    if not match or (match[3] and match[3] != unit):
        raise ValueError('JoSIM requires a numeric literal with a compatible unit')
    base = float(match[1])
    value = base * _SCALE[match[2] or '']
    # Reject both float parsing and engineering-suffix underflow, without Decimal
    # allocation or expression evaluation for arbitrarily long numeric tokens.
    nonzero = any(c in '123456789' for c in re.split('E', match[1])[0])
    if not math.isfinite(value) or (admission and abs(value) > 1e12) or (nonzero and value == 0):
        raise ValueError('JoSIM numeric literal is outside the finite magnitude boundary')
    return 0.0 if value == 0 else value


def _normalize_lines(text: str, *, admission: bool) -> list[str]:
    if not isinstance(text, str) or not 1 <= len(text) <= 65536 or not text.isascii():
        raise ValueError('JoSIM netlist requires 1–65536 ASCII bytes')
    if any(not 32 <= ord(c) <= 126 and c not in '\r\n\t' for c in text):
        raise ValueError('JoSIM netlist contains unsupported control characters')
    lines = []
    for raw in text.splitlines():
        line = re.sub(r'[ \t]+', ' ', raw.strip()).upper()
        if not line or line.startswith(('*', '#')):
            continue
        if len(line) > 4096:
            raise ValueError('JoSIM logical line exceeds 4096 characters')
        if line.startswith('+'):
            if not lines:
                raise ValueError('JoSIM continuation requires a preceding statement')
            lines[-1] += ' ' + line[1:].strip()
            if len(lines[-1]) > 4096:
                raise ValueError('JoSIM logical line exceeds 4096 characters')
        else:
            lines.append(line)
            if admission and len(lines) > 256:
                raise ValueError('JoSIM accepts at most 256 logical statements')
    if not lines or lines[-1] != '.END' or '.END' in lines[:-1]:
        raise ValueError('JoSIM requires exactly one terminal .END statement')
    return lines


def _waveform(text: str, unit: str, *, admission: bool) -> str:
    if text.startswith('DC '):
        token = text[3:]
        _numeric_token(token, unit, admission=admission)
        return 'DC ' + token
    if not text.startswith('PWL(') or not text.endswith(')'):
        raise ValueError('JoSIM sources support numeric DC or PWL only')
    tokens = re.split(r'[ ,]+', text[4:-1].strip())
    if len(tokens) < 4 or (admission and len(tokens) > 128) or len(tokens) % 2:
        raise ValueError('JoSIM PWL requires 2–64 numeric time/value pairs')
    times = [_numeric_token(token, admission=admission) for token in tokens[::2]]
    for token in tokens[1::2]:
        _numeric_token(token, unit, admission=admission)
    if admission and (times[0] != 0 or any(b <= a for a, b in zip(times, times[1:])) or times[-1] > 1e-6):
        raise ValueError('JoSIM PWL requires increasing times from zero through at most one microsecond')
    return 'PWL(' + ' '.join(tokens) + ')'


def _validate_netlist(text: str, *, admission: bool) -> tuple[str, Mapping[str, Any]]:
    """Return exactly the text to execute and bounded SI trace/shape metadata.

    Statements outside this positive subset fail before any native execution.
    Error messages intentionally exclude input fragments and filesystem paths.
    """
    lines = _normalize_lines(text, admission=admission)
    devices, nodes, model_refs, models, traces, transient, normalized = {}, set(), [], {}, [], [], []
    for line in lines[:-1]:
        element = re.fullmatch(rf'([RLBIV][A-Z0-9_]{{1,31}}) (\d{{1,2}}) (\d{{1,2}}) (.+)', line)
        if element:
            name, a, b, tail = element.groups()
            kind = name[0]
            if name in devices or (admission and a == b):
                raise ValueError('JoSIM device names must be unique and nodes distinct')
            if kind in 'RL':
                value = _numeric_token(tail, '' if kind == 'R' else 'H', admission=admission)
                if admission and not 0 < value <= (1e6 if kind == 'R' else 1e-3):
                    raise ValueError('JoSIM passive value exceeds its positive physical bound')
            elif kind == 'B':
                junction = re.fullmatch(rf'({_IDENTIFIER}) AREA=(\S+)', tail)
                if not junction:
                    raise ValueError('JoSIM junction requires a model and numeric AREA')
                area = _numeric_token(junction[2], admission=admission)
                if admission and not 0 < area <= 100:
                    raise ValueError('JoSIM junction requires AREA in (0,100]')
                model_refs.append(junction[1])
            else:
                tail = _waveform(tail, 'A' if kind == 'I' else 'V', admission=admission)
            devices[name] = kind
            nodes.update((a, b))
            if admission and (len(devices) > 32 or len(nodes) > 16):
                raise ValueError('JoSIM accepts at most 32 devices and 16 nodes')
            normalized.append(f'{name} {a} {b} {tail}')
            continue
        model = re.fullmatch(rf'\.MODEL ({_IDENTIFIER}) JJ\((.*)\)', line)
        if model:
            if models:
                raise ValueError('JoSIM requires exactly one junction model')
            fields = {}
            for field in re.split(r'[ ,]+', model[2]):
                pair = field.split('=')
                if len(pair) != 2 or pair[0] in fields:
                    raise ValueError('JoSIM model fields must be unique numeric assignments')
                fields[pair[0]] = pair[1]
            if set(fields) != {'RTYPE', 'VG', 'CAP', 'R0', 'RN', 'ICRIT'}:
                raise ValueError('JoSIM junction model has unsupported or missing fields')
            values = {key: _numeric_token(token, {'VG': 'V', 'CAP': 'F', 'R0': '', 'RN': '', 'ICRIT': 'A'}.get(key, ''), admission=admission)
                      for key, token in fields.items()}
            if admission and (values['RTYPE'] != 1 or any(value <= 0 for value in values.values())):
                raise ValueError('JoSIM junction model requires RTYPE=1 and positive numeric fields')
            models[model[1]] = values
            normalized.append(f'.MODEL {model[1]} JJ(' + ' '.join(f'{key}={fields[key]}' for key in sorted(fields)) + ')')
            continue
        analysis = re.fullmatch(r'\.TRAN (\S+) (\S+)', line)
        if analysis:
            step, stop = (_numeric_token(token, admission=admission) for token in analysis.groups())
            if admission and not 0 < step <= stop <= 1e-6:
                raise ValueError('JoSIM transient requires 0 < step <= stop <= one microsecond')
            if transient:
                raise ValueError('JoSIM requires exactly one transient')
            rows = None
            if admission:
                # Exact float ratios provide a conservative output-row ceiling.
                step_n, step_d = step.as_integer_ratio()
                stop_n, stop_d = stop.as_integer_ratio()
                quotient, remainder = divmod(stop_n * step_d, stop_d * step_n)
                rows = quotient + bool(remainder) + 1
                if rows > 4096:
                    raise ValueError('JoSIM accepts at most 4096 predicted rows')
            transient.append({'step_seconds': step, 'stop_seconds': stop, 'predicted_rows': rows})
            normalized.append('.TRAN ' + ' '.join(analysis.groups()))
            continue
        trace = re.fullmatch(rf'\.PRINT (DEVV|DEVI|PHASE) ({_IDENTIFIER})', line)
        if trace:
            if trace.groups() in traces or (admission and len(traces) >= 8):
                raise ValueError('JoSIM requires at most eight unique selected traces')
            traces.append(trace.groups())
            normalized.append('.PRINT ' + ' '.join(trace.groups()))
            continue
        raise ValueError('JoSIM statement is outside the supported positive subset')
    if not devices or len(models) != 1 or (admission and any(name not in models for name in model_refs)):
        raise ValueError('JoSIM requires devices and one model with valid references')
    if not transient or not traces:
        raise ValueError('JoSIM requires one transient and explicit selected traces')
    for kind, name in traces:
        if admission and (name not in devices or (kind == 'PHASE' and devices[name] != 'B')):
            raise ValueError('JoSIM selected trace has an invalid device or phase reference')
    normalized.append('.END')
    result = '\n'.join(normalized) + '\n'
    if len(result) > 65536:
        raise ValueError('Normalized JoSIM netlist exceeds its byte boundary')
    return result, {
        **transient[0], 'device_count': len(devices), 'node_count': len(nodes),
        'trace_names': [f'{dict(DEVV="V", DEVI="I", PHASE="P")[kind]}({name})' for kind, name in traces],
        'trace_units': [dict(DEVV='V', DEVI='A', PHASE='rad')[kind] for kind, _ in traces],
    }


def _normalize_josim_netlist(text: str) -> str:
    """Normalize frozen syntax/finite values before an idempotency lookup."""
    return _validate_netlist(text, admission=False)[0]


def validate_josim_netlist(text: str) -> tuple[str, Mapping[str, Any]]:
    """Validate all physical, reference and resource bounds before execution."""
    return _validate_netlist(text, admission=True)
