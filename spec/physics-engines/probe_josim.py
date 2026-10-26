#!/usr/bin/env python3
"""Discovery-only positive JoSIM subset and native SFQ probe; not an API parser.

Run: python3 spec/physics-engines/probe_josim.py --binary /absolute/josim-cli
The executable must be a separately built, trusted JoSIM v2.7 binary.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import subprocess
import tempfile
import time

PHI0 = 2.0678338484619295e-15  # h/(2e), V s (SI exact defining constants)
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:E[+-]?\d+)?(?:MEG|[TGMKUNPF])?(?:[VAFH])?"
IDENT = r"[A-Z][A-Z0-9_]{0,31}"
SCALE = {"": 1, "T": 1e12, "G": 1e9, "MEG": 1e6, "K": 1e3,
         "M": 1e-3, "U": 1e-6, "N": 1e-9, "P": 1e-12, "F": 1e-15}


def numeric(token):
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:E[+-]?\d+)?)(MEG|[TGMKUNPF])?([VAFH])?", token)
    if not match:
        raise ValueError("numeric literals only")
    value = float(match[1]) * SCALE[match[2] or ""]
    if not math.isfinite(value) or abs(value) > 1e12:
        raise ValueError("numeric bound")
    return value


def validate(text):
    """Normalize then full-match every statement, including continuation payloads.

    The deliberately narrow prototype accepts R/L, B junctions, PWL/DC sources,
    one JJ model, one transient, explicit device traces, and terminal .END.
    Native parsing/electrical semantics remain JoSIM's responsibility.
    """
    if len(text.encode()) > 65536 or not text.isascii() or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
        raise ValueError("text boundary")
    lines = []
    for raw in text.splitlines():
        line = raw.strip().upper()
        if not line or line.startswith(('*', '#')):
            continue
        if len(line) > 4096:
            raise ValueError("line bound")
        if line.startswith('+'):
            if not lines:
                raise ValueError("orphan continuation")
            lines[-1] += ' ' + line[1:].strip()
            if len(lines[-1]) > 4096:
                raise ValueError("logical line bound")
        else:
            lines.append(line)
    if len(lines) > 256 or not lines or lines[-1] != '.END' or '.END' in lines[:-1]:
        raise ValueError("statement/end boundary")
    devices, nodes, junction_models, models, traces, transients = {}, set(), [], set(), [], []
    for line in lines[:-1]:
        match = re.fullmatch(rf"([RL][A-Z0-9_]{{1,31}})\s+(\d{{1,2}})\s+(\d{{1,2}})\s+({NUMBER})", line)
        if match:
            name, a, b, value = match.groups()
            if not 0 < numeric(value) <= (1e6 if name[0] == 'R' else 1e-3):
                raise ValueError("passive value bound")
        else:
            match = re.fullmatch(rf"(B[A-Z0-9_]{{1,31}})\s+(\d{{1,2}})\s+(\d{{1,2}})\s+({IDENT})\s+AREA=({NUMBER})", line)
            if match:
                name, a, b, model, area = match.groups()
                junction_models.append(model)
                if not 0 < numeric(area) <= 100:
                    raise ValueError("area bound")
            else:
                match = re.fullmatch(rf"([IV][A-Z0-9_]{{1,31}})\s+(\d{{1,2}})\s+(\d{{1,2}})\s+(.*)", line)
                if match:
                    name, a, b, waveform = match.groups()
                    if re.fullmatch(rf"DC\s+{NUMBER}", waveform):
                        numeric(waveform.split()[1])
                    elif re.fullmatch(rf"PWL\(\s*{NUMBER}(?:[ ,]+{NUMBER}){{3,127}}\s*\)", waveform):
                        values = [numeric(x) for x in re.split(r'[ ,]+', waveform[4:-1].strip())]
                        times = values[::2]
                        if len(values) % 2 or times[0] != 0 or any(y <= x for x, y in zip(times, times[1:])) or times[-1] > 1e-6:
                            raise ValueError("PWL time boundary")
                    else:
                        raise ValueError("waveform outside subset")
                else:
                    model = re.fullmatch(rf"\.MODEL\s+({IDENT})\s+JJ\((.*)\)", line)
                    if model:
                        fields = model[2].replace(',', ' ').split()
                        values = {}
                        for field in fields:
                            pair = field.split('=')
                            if len(pair) != 2 or pair[0] in values:
                                raise ValueError("model field")
                            values[pair[0]] = numeric(pair[1])
                        if set(values) != {'RTYPE', 'VG', 'CAP', 'R0', 'RN', 'ICRIT'} or values['RTYPE'] != 1 or any(v <= 0 for v in values.values()):
                            raise ValueError("model allowlist")
                        if model[1] in models:
                            raise ValueError("duplicate model")
                        models.add(model[1])
                        continue
                    tran = re.fullmatch(rf"\.TRAN\s+({NUMBER})\s+({NUMBER})", line)
                    if tran:
                        step, stop = map(numeric, tran.groups())
                        if not 0 < step <= stop <= 1e-6 or math.ceil(stop / step) + 1 > 4096:
                            raise ValueError("transient/output boundary")
                        transients.append((step, stop))
                        continue
                    trace = re.fullmatch(rf"\.PRINT\s+(DEVV|DEVI|PHASE)\s+({IDENT})", line)
                    if trace:
                        traces.append(trace.groups())
                        continue
                    raise ValueError("statement outside positive subset")
        if name in devices or a == b:
            raise ValueError("device identity/node boundary")
        devices[name] = name[0]
        nodes.update((a, b))
    if not 1 <= len(devices) <= 32 or len(nodes) > 16 or len(models) != 1 or any(m not in models for m in junction_models):
        raise ValueError("device/model count or reference")
    if len(transients) != 1 or not 1 <= len(traces) <= 8 or len(set(traces)) != len(traces):
        raise ValueError("analysis/trace count")
    for kind, device in traces:
        if device not in devices or (kind == 'PHASE' and devices[device] != 'B'):
            raise ValueError("trace reference")
    return '\n'.join(lines) + '\n'


def child_limits():
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2,) * 2)


def native(binary, text):
    with tempfile.TemporaryDirectory(prefix='wqs-josim-') as directory:
        root = Path(directory)
        root.chmod(0o700)
        (root / 'input.cir').write_text(text)
        started = time.perf_counter()
        subprocess.run([str(binary), '-m', '-o', 'output.csv', 'input.cir'], cwd=root,
                       env={'PATH': '/usr/bin:/bin', 'OMP_NUM_THREADS': '1'},
                       preexec_fn=child_limits, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
        payload = (root / 'output.csv').read_bytes()
        if len(payload) > 6 * 1024**2:
            raise AssertionError('native output budget')
        rows = list(csv.reader(payload.decode().splitlines()))
        data = [[float(cell) for cell in row] for row in rows[1:] if row]
        if not 1 <= len(data) <= 4096 or any(not math.isfinite(v) for row in data for v in row):
            raise AssertionError('empty or nonfinite native result')
        if any(b[0] <= a[0] for a, b in zip(data, data[1:])):
            raise AssertionError('non-increasing SI times')
        if set(path.name for path in root.iterdir()) != {'input.cir', 'output.csv'}:
            raise AssertionError('unexpected native file')
        return rows[0], data, payload, time.perf_counter() - started


def adversarial_checks(text):
    statements = ['.include secret', 'InClUdE secret', '.file escaped.csv', 'FILE escaped.csv',
                  '.control\ninclude secret\n.endc', '.param A=1', '.subckt S 1 0\n.ends',
                  'X1 1 0 S', '.iv JMITLL 1u stolen.csv', '.save all', '.option seed=42',
                  '.temp 4.2', '.neb 1e12', '.plot V(1)', '.model M JJ(RTYPE=1)',
                  'VEXT 1 0 CUS(secret.dat)', 'VEXT 1 0 PWL(0 0 1p {A})',
                  'VEXT 1 0 PWL(0 0 1p 1)\n+ .file escaped.csv',
                  '+ .include secret', '.tran 1e-30 1', '.print DEVV ABSENT',
                  '.print PHASE ROUT', '.end\n.file escaped.csv']
    for statement in statements:
        candidate = text.replace('.end', statement + '\n.end')
        try:
            validate(candidate)
        except ValueError:
            continue
        raise AssertionError('unsafe/unsupported variant admitted: ' + statement)
    # Case folding, comment removal and safe continuation preserve the subset.
    validate(text.lower())
    validate(text.replace('100p 0', '100p\n+ 0'))
    return len(statements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--fixture', type=Path)
    args = parser.parse_args()
    if args.fixture is None:
        args.fixture = Path(__file__).resolve().parents[2] / 'tests/fixtures/physics/josim/sfq.cir'
    binary = args.binary.resolve(strict=True)
    text = args.fixture.read_text()
    normalized = validate(text)
    rejected = adversarial_checks(text)
    header, rows, payload, elapsed = native(binary, normalized)
    if header != ['time', 'V(B01)', 'P(B01)', 'V(B02)', 'P(B02)', 'I(ROUT)'] or any(len(row) != 6 for row in rows):
        raise AssertionError('selected native trace schema changed')
    integrals = {}
    pulse_rows = [row for row in rows if 50e-12 <= row[0] <= 200e-12]
    for device in ('B01', 'B02'):
        column = next(i for i, name in enumerate(header) if device in name and name.startswith('V('))
        area = sum((a[column] + b[column]) * (b[0] - a[0]) / 2 for a, b in zip(pulse_rows, pulse_rows[1:]))
        error = abs(area / PHI0 - 1)
        if error >= .02:
            raise AssertionError(f'{device} SFQ integral {area} error={error}')
        integrals[device] = {'volt_seconds': area, 'relative_error': error, 'window_seconds': [50e-12, 200e-12]}
    # Native seed/temperature discovery only, deliberately outside v1 safe subset.
    noisy = normalized.replace('.END', '.TEMP 4.2\n.NEB 1e12\n.OPTION SEED=42\n.END')
    first = native(binary, noisy)[2]
    second = native(binary, noisy)[2]
    other = native(binary, noisy.replace('SEED=42', 'SEED=43'))[2]
    if first != second or first == other or first == payload:
        raise AssertionError('native noise seed did not reproduce/change as expected')
    print(json.dumps({'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                     'fixture_sha256': hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
                     'header': header, 'units': ['s', 'V', 'rad', 'V', 'rad', 'A'], 'rows': len(rows), 'output_bytes': len(payload),
                     'elapsed_seconds': elapsed, 'peak_child_rss_kib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                     'rejected_adversarial_variants': rejected, 'sfq': integrals,
                     'noise_seed_42_equal': first == second, 'noise_seed_43_differs': first != other,
                     'rlimit_as_bytes': 3 * 1024**3, 'rlimit_fsize_bytes': 12 * 1024**2}, indent=2))


if __name__ == '__main__':
    main()
