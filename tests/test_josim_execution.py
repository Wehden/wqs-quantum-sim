"""Native JoSIM acceptance; mocks exercise only hostile output boundaries.

Set WQS_TEST_JOSIM_PROFILE=physics-cpu-v1 for the constrained pinned-image run.
The maximum fixture then checks live CPU/cgroup/AS/file/nonroot limits. Tests use
WQS_TEST_JOSIM_{ORIGINAL,CANDIDATE,INSTRUMENTED,SOURCE}; BINARY is the candidate.
A separate executable compiler tmpfs is needed for the existing header test;
native scratch remains on the noexec /tmp mount.
"""
import base64
import io
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from qsim import josim_adapter as adapter
from qsim.josim_models import JoSIMRequest
from qsim.josim_validation import validate_josim_netlist
from qsim.physics_results import ArraySpec, plan_result, serialize_physics_result


@pytest.fixture
def request_model():
    return JoSIMRequest(netlist=Path('tests/fixtures/physics/josim/sfq.cir').read_text())


@pytest.fixture
def ceiling(request_model):
    return request_model._certified_allocation_plan()


def test_public_entry_remains_closed(request_model, ceiling, monkeypatch):
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('native launched'))
    with pytest.raises(ValueError, match='certificate is unavailable'):
        adapter.run_josim(request_model, ceiling, execution_context={})


def test_real_sfq_and_exact_serialization(request_model, ceiling, monkeypatch):
    binary = os.environ.get('WQS_TEST_JOSIM_BINARY')
    if not binary:
        pytest.skip('Pinned JoSIM executable unavailable; native acceptance is a separate gate')
    monkeypatch.setattr(adapter, '_BINARY', Path(binary))
    result = adapter._execute_josim(request_model, ceiling)
    times, traces = result.arrays['times'], result.arrays['traces']
    assert traces.shape == (len(times), 5)
    assert len(times) == 999 < ceiling.result.arrays[0].shape[0]
    assert result.metadata['trace_names'] == ['V(B01)', 'P(B01)', 'V(B02)', 'P(B02)', 'I(ROUT)']
    assert result.metadata['trace_units'] == ['V', 'rad', 'V', 'rad', 'A']
    assert 'seed' not in result.metadata
    window = (times >= 50e-12) & (times <= 200e-12)
    for column in (0, 2):
        integral = np.trapezoid(traces[window, column], times[window])
        assert abs(integral / 2.0678338484619295e-15 - 1) < .02
    exact = plan_result((ArraySpec('times', 'float64', times.shape, 's'),
                         ArraySpec('traces', 'float64', traces.shape, None)),
                        metadata_characters=65536, summary_bytes=65536)
    assert exact.numeric_bytes <= ceiling.result.numeric_bytes
    assert exact.npz_upper_bytes <= ceiling.result.npz_upper_bytes
    assert exact.callback_upper_bytes <= ceiling.result.callback_upper_bytes
    summary, artifact = serialize_physics_result(result, exact)
    assert summary['arrays']['times']['shape'] == [999]
    with np.load(io.BytesIO(base64.b64decode(artifact)), allow_pickle=False) as stored:
        np.testing.assert_array_equal(stored['traces'], traces)


@pytest.fixture
def fake_native(monkeypatch, tmp_path, request_model):
    binary = tmp_path / 'trusted'
    binary.write_bytes(b'test binary')
    real_sha256 = adapter.hashlib.sha256

    def digest(data=None):
        if data is not None:
            return real_sha256(data)
        return SimpleNamespace(update=lambda block: None,
                               hexdigest=lambda: ceiling_digest)

    ceiling_digest = adapter._BINARY_SHA256
    monkeypatch.setattr(adapter.hashlib, 'sha256', digest)
    monkeypatch.setattr(adapter, '_BINARY', binary)
    _, info = validate_josim_netlist(request_model.netlist)
    header = 'time,' + ','.join('"'+name+'"' for name in info['trace_names']) + '\n'
    calls = []

    def install(payload=None, status=0, extra=None):
        def execute(argv, **kwargs):
            root = kwargs['cwd']
            calls.append(root)
            assert root.stat().st_mode & 0o777 == 0o700
            assert argv == [str(binary), '-m', '-o', 'output.csv', 'input.cir']
            assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'OMP_NUM_THREADS': '1'}
            assert kwargs['stdin'] == adapter.subprocess.DEVNULL
            assert kwargs['stderr'] == adapter.subprocess.DEVNULL
            assert 'shell' not in kwargs and 'timeout' not in kwargs
            assert (root / 'input.cir').read_text() == info['native_netlist']
            if payload is not None:
                (root / 'output.csv').write_bytes(payload)
            if extra:
                extra(root)
            return SimpleNamespace(returncode=status)
        monkeypatch.setattr(adapter.subprocess, 'run', execute)
    return install, header, calls


@pytest.mark.parametrize('body', [b'', b'0,0,0\n', b'0,0,0,0,0,nan\n',
                                  b'0,0,0,0,0,0\n0,1,1,1,1,1\n',
                                  b'-1,0,0,0,0,0\n', b'1,0,0,0,0,0\n',
                                  b'0,0,0,0,0,inf\n', b'\xff\n',
                                  b'0,' + b'1'*4100 + b'\n'])
def test_corrupt_output_rejected_and_scratch_cleaned(body, fake_native, request_model, ceiling):
    install, header, calls = fake_native
    install(header.encode() + body)
    with pytest.raises(ValueError, match='invalid_result'):
        adapter._execute_josim(request_model, ceiling)
    assert calls and all(not path.exists() for path in calls)


@pytest.mark.parametrize('variant', ['header', 'oversize', 'rows', 'exit', 'missing', 'extra', 'symlink'])
def test_native_boundary_failures(variant, fake_native, request_model, ceiling, tmp_path):
    install, header, calls = fake_native
    payload = header.encode() + b'0,0,0,0,0,0\n'
    extra, status = None, 0
    if variant == 'header':
        payload = payload.replace(b'V(B01)', b'V(B99)')
    elif variant == 'oversize':
        payload = b'X'*(6*1024**2 + 1)
    elif variant == 'rows':
        payload = header.encode() + b''.join(f'{i*1e-15},0,0,0,0,0\n'.encode() for i in range(4097))
    elif variant == 'exit':
        status = 1
    elif variant == 'missing':
        payload = None
    elif variant == 'extra':
        extra = lambda root: (root / 'unexpected').write_text('bad')
    elif variant == 'symlink':
        payload = None
        outside = tmp_path / 'outside.csv'
        outside.write_text(header + '0,0,0,0,0,0\n')
        extra = lambda root: (root / 'output.csv').symlink_to(outside)
    install(payload, status, extra)
    with pytest.raises(ValueError, match='(invalid_result|native_failure|resource_limit)') as error:
        adapter._execute_josim(request_model, ceiling)
    assert str(tmp_path) not in str(error.value)
    assert all(not path.exists() for path in calls)


def test_binary_identity_failure_prevents_launch(fake_native, request_model, ceiling, monkeypatch):
    monkeypatch.setattr(adapter, '_BINARY_SHA256', '0'*64)
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('native launched'))
    with pytest.raises(ValueError, match='binary identity'):
        adapter._execute_josim(request_model, ceiling)


def test_interruption_cleans_scratch(fake_native, request_model, ceiling, monkeypatch):
    _, _, calls = fake_native
    def interrupt(argv, **kwargs):
        calls.append(kwargs['cwd'])
        raise KeyboardInterrupt
    monkeypatch.setattr(adapter.subprocess, 'run', interrupt)
    with pytest.raises(KeyboardInterrupt):
        adapter._execute_josim(request_model, ceiling)
    assert calls and all(not path.exists() for path in calls)


def test_invalid_reservation_prevents_launch(request_model, ceiling, monkeypatch):
    from dataclasses import replace
    wrong = replace(ceiling, result=plan_result((ArraySpec('times', 'float64', (1,), 's'),),
                                               metadata_characters=65536, summary_bytes=65536))
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('native launched'))
    with pytest.raises(ValueError, match='reservation mismatch'):
        adapter._execute_josim(request_model, wrong)


def test_launch_os_error_is_sanitized_and_cleans_scratch(fake_native, request_model, ceiling, monkeypatch):
    _, _, calls = fake_native
    def failed_launch(argv, **kwargs):
        calls.append(kwargs['cwd'])
        raise OSError('sensitive /private/credential/path')
    monkeypatch.setattr(adapter.subprocess, 'run', failed_launch)
    with pytest.raises(ValueError, match='native_failure') as error:
        adapter._execute_josim(request_model, ceiling)
    assert 'credential' not in str(error.value)
    assert calls and all(not path.exists() for path in calls)


@pytest.mark.parametrize('nodes,scale', [('0 1', -1), ('1 0', 1)])
def test_projected_columns_only_normalize_zero_and_native_headers_are_positional(nodes, scale):
    text = (f'I1 {nodes} DC 1u\nI2 {nodes} DC 2u\nR1 1 0 1\n'
            '.MODEL J JJ(RTYPE=1 VG=2mV CAP=1pF R0=100 RN=10 ICRIT=1mA)\n'
            '.TRAN 1p 5p\n.PRINT DEVV I1\n.PRINT DEVV R1\n.PRINT DEVV I2\n.END\n')
    _, info = validate_josim_netlist(text)
    rows = adapter._parse_output(b'time,"V(1,0)","V(R1)","V(1,0)"\n0,-0,-0,2\n', info)
    assert rows[0] == [0, 0, 0, scale*2]
    assert not np.signbit(rows[0][1])
    assert np.signbit(rows[0][2])


@pytest.mark.parametrize('change', ['algorithm', 'policy', 'peak', 'workspace', 'certificate', 'projection', 'type'])
def test_full_private_certificate_mismatch_prevents_launch(change, request_model, ceiling, monkeypatch):
    from dataclasses import replace
    if change == 'algorithm':
        wrong = replace(ceiling, algorithm='legacy-original')
    elif change == 'policy':
        wrong = replace(ceiling, policy_revision='other-profile')
    elif change == 'peak':
        wrong = replace(ceiling, peak_bytes=ceiling.peak_bytes-1)
    elif change == 'workspace':
        wrong = replace(ceiling, allocation_items=())
    else:
        wrong = request_model._certified_allocation_plan()
        if change == 'certificate':
            wrong.coefficient_plan['binary_sha256'] = '0'*64
        elif change == 'projection':
            wrong.coefficient_plan['trace_projection'][0]['native_name'] = 'V(I99)'
        else:
            wrong.coefficient_plan['trace_projection'][0]['scale'] = True
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('native launched'))
    with pytest.raises(ValueError, match='certificate mismatch'):
        adapter._execute_josim(request_model, wrong)


@pytest.mark.parametrize('change', ['missing', 'extra', 'scale', 'bool_scale', 'rewritten',
                                   'native_name', 'public_name', 'count', 'text', 'header'])
def test_malformed_projection_fails_before_binary_or_native(change, request_model, ceiling, monkeypatch):
    normalized, info = validate_josim_netlist(request_model.netlist)
    if change == 'missing':
        del info['trace_projection'][0]['scale']
    elif change == 'extra':
        info['trace_projection'][0]['expression'] = 'private'
    elif change == 'scale':
        info['trace_projection'][0]['scale'] = 2
    elif change == 'bool_scale':
        info['trace_projection'][0]['scale'] = True
    elif change == 'rewritten':
        info['trace_projection'][0]['rewritten'] = True
    elif change == 'native_name':
        info['trace_projection'][0]['native_name'] = 'V(1,2)'
    elif change == 'public_name':
        info['trace_projection'][0]['public_name'] = 'private'
    elif change == 'count':
        info['trace_projection'].pop()
    elif change == 'text':
        info['native_netlist'] += '.FILE private\n'
    else:
        info['native_trace_names'][0] = 'V(1,2)'
    monkeypatch.setattr(adapter, 'validate_josim_netlist', lambda text: (normalized, info))
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('native launched'))
    with pytest.raises(ValueError, match='invalid JoSIM native projection') as error:
        adapter._execute_josim(request_model, ceiling)
    assert 'private' not in str(error.value)


@pytest.mark.parametrize('identity', ['original', 'wrong', 'tampered_candidate'])
def test_real_original_wrong_and_tampered_binary_never_launch(identity, request_model, ceiling, monkeypatch, tmp_path):
    if identity == 'wrong':
        binary = tmp_path / 'wrong'
        binary.write_bytes(b'not the certified executable')
    else:
        variable = 'WQS_TEST_JOSIM_ORIGINAL' if identity == 'original' else 'WQS_TEST_JOSIM_CANDIDATE'
        path = os.environ.get(variable)
        if not path:
            pytest.skip('Real pinned executable required for identity acceptance')
        binary = Path(path)
        if identity == 'tampered_candidate':
            copied = tmp_path / 'tampered'
            copied.write_bytes(binary.read_bytes()+b'changed')
            binary = copied
    monkeypatch.setattr(adapter, '_BINARY', binary)
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('native launched'))
    with pytest.raises(ValueError, match='binary identity'):
        adapter._execute_josim(request_model, ceiling)


@pytest.mark.parametrize('nodes', [('1', '2'), ('1', '0'), ('0', '1')])
@pytest.mark.parametrize('mode', ['dc', 'pwl', 'retry'])
def test_real_current_source_voltage_projection_is_native_bitwise_exact(nodes, mode, monkeypatch):
    import runpy
    probe = runpy.run_path('spec/physics-engines/probe_josim_history.py')
    candidate, original = (os.environ.get(name) for name in
                           ('WQS_TEST_JOSIM_CANDIDATE', 'WQS_TEST_JOSIM_ORIGINAL'))
    if not candidate or not original:
        pytest.skip('Distinct real executables required for numerical projection acceptance')
    assert adapter.hashlib.sha256(Path(original).read_bytes()).hexdigest() == probe['ORIGINAL_SHA256']
    assert adapter.hashlib.sha256(Path(candidate).read_bytes()).hexdigest() == probe['BUILD_PROVENANCE']['candidate_binary_sha256']
    a, b = nodes
    waveform = 'DC 1u' if mode == 'dc' else 'PWL(0 0 1.375p 1u 2.625p -1u 5.125p 0)'
    extra = 'R02 2 0 5\n' if b == '2' else ''
    transient = '.TRAN 0.25p 20.125p'
    if mode == 'retry':
        extra += f'V01 {a} {b} DC 0.1\nB01 {a} {b} MOD AREA=1\n'
        transient = '.TRAN 1p 1n'
    request = JoSIMRequest(netlist=(f'I01 {a} {b} {waveform}\nR01 {a} {b} 2\n'+extra+
        probe['MODEL']+transient+'\n.PRINT DEVV R01\n.PRINT DEVV I01\n.PRINT DEVI I01\n.END\n'))
    canonical = request.canonical_envelope()
    _, info = validate_josim_netlist(request.netlist)
    reference = probe['_run'](Path(original), info['native_netlist'], True, 30)
    candidate_raw = probe['_run'](Path(candidate), info['native_netlist'], True, 30)
    probe['_compare'](reference, candidate_raw)
    monkeypatch.setattr(adapter, '_BINARY', Path(candidate))
    result = adapter._execute_josim(request, request._certified_allocation_plan())
    raw = np.asarray(candidate_raw[2][1], dtype=np.float64)
    assert result.arrays['times'].tobytes() == raw[:, 0].copy().tobytes()
    assert result.arrays['traces'][:, 0].copy().tobytes() == raw[:, 1].copy().tobytes()
    assert result.arrays['traces'][:, 2].copy().tobytes() == raw[:, 3].copy().tobytes()
    assert result.arrays['traces'][:, 1].copy().tobytes() == raw[:, 1].copy().tobytes()
    assert result.metadata['trace_names'] == ['V(R01)', 'V(I01)', 'I(I01)']
    assert request.canonical_envelope() == canonical


def test_real_maximum_pwl_mixed_projection_keeps_duplicate_native_columns(monkeypatch, tmp_path):
    import runpy
    probe = runpy.run_path('spec/physics-engines/probe_josim_history.py')
    candidate, original = (os.environ.get(name) for name in
                           ('WQS_TEST_JOSIM_CANDIDATE', 'WQS_TEST_JOSIM_ORIGINAL'))
    if not candidate or not original:
        pytest.skip('Distinct real executables required for maximum projection acceptance')
    if os.environ.get('WQS_TEST_JOSIM_PROFILE') == 'physics-cpu-v1':
        import resource
        assert os.getuid() == 1001
        assert resource.getrlimit(resource.RLIMIT_AS) == (3*1024**3,)*2
        assert resource.getrlimit(resource.RLIMIT_FSIZE) == (12*1024**2,)*2
        assert int(Path('/sys/fs/cgroup/memory.max').read_text()) == 4*1024**3
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        assert int(quota) == 2*int(period)
    text = probe['_capacity_cases']()['maximum_source_storage']
    traces = ['DEVV I01', 'DEVV R32', 'DEVI I02', 'DEVV I02',
              'DEVV I03', 'DEVI R32', 'DEVV I04', 'DEVV I05']
    for index, trace in enumerate(traces, 1):
        text = text.replace(f'.PRINT DEVI I{index:02d}', f'.PRINT {trace}')
    request = JoSIMRequest(netlist=text)
    plan = request._certified_allocation_plan()
    _, info = validate_josim_netlist(request.netlist)
    assert len(info['native_netlist']) <= 65568
    assert info['native_trace_names'].count('V(1,0)') == 5
    import csv
    import json
    import subprocess
    instrumented = os.environ.get('WQS_TEST_JOSIM_INSTRUMENTED')
    if not instrumented:
        pytest.skip('Source-matched instrumented binary required for capacity acceptance')
    receipts = probe['BUILD_PROVENANCE']
    outputs = []
    for index, (binary, expected_digest) in enumerate((
            (original, receipts['original_binary_sha256']),
            (candidate, receipts['candidate_binary_sha256']),
            (instrumented, receipts['instrumented_binary_sha256']))):
        assert adapter.hashlib.sha256(Path(binary).read_bytes()).hexdigest() == expected_digest
        root = tmp_path / str(index)
        root.mkdir()
        (root / 'input.cir').write_text(info['native_netlist'])
        completed = subprocess.run([binary, '-m', '-o', 'output.csv', 'input.cir'],
            cwd=root, env={'PATH': '/usr/bin:/bin', 'OMP_NUM_THREADS': '1'},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            preexec_fn=probe['_limits'], timeout=30, check=True)
        outputs.append((root / 'output.csv').read_bytes())
        if index == 2:
            record = [json.loads(line.removeprefix(b'WQS_HISTORY '))
                      for line in completed.stderr.splitlines() if line.startswith(b'WQS_HISTORY ')][0]
            probe['_verify_capacity'](record)
            assert record['rows'] == 4095 and record['selected_variables'] == 2
    assert outputs[0] == outputs[1] == outputs[2]
    parsed = list(csv.reader(io.StringIO(outputs[0].decode('ascii'))))
    assert parsed[0] == ['time', *info['native_trace_names']]
    raw = np.asarray([[float(cell) for cell in row] for row in parsed[1:]], dtype=np.float64)
    monkeypatch.setattr(adapter, '_BINARY', Path(candidate))
    result = adapter._execute_josim(request, plan)
    assert result.arrays['traces'].shape == (4095, 8)
    for index, entry in enumerate(info['trace_projection']):
        expected = raw[:, index+1].copy()
        if entry['rewritten']:
            expected *= entry['scale']
            expected[expected == 0] = 0.0
        assert result.arrays['traces'][:, index].copy().tobytes() == expected.tobytes()
    assert result.metadata['trace_names'] == info['trace_names']
    assert plan.peak_bytes == 622775052
    assert result.metadata['trace_projection'] == info['trace_projection']
