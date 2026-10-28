"""Native JoSIM acceptance; mocks exercise only hostile output boundaries."""
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
from qsim.physics_results import AllocationPlan, ArraySpec, plan_result, serialize_physics_result


@pytest.fixture
def request_model():
    return JoSIMRequest(netlist=Path('tests/fixtures/physics/josim/sfq.cir').read_text())


@pytest.fixture
def ceiling(request_model):
    _, info = validate_josim_netlist(request_model.netlist)
    rows, columns = info['predicted_rows'], len(info['trace_names'])
    result = plan_result((ArraySpec('times', 'float64', (rows,), 's'),
                          ArraySpec('traces', 'float64', (rows, columns), None)),
                         metadata_characters=65536, summary_bytes=65536)
    # Synthetic result reservation, not a native workspace certificate.
    return AllocationPlan('test', 'josim-transient', {}, (), 0, {}, result)


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
    monkeypatch.setattr(adapter, '_BINARY', binary)
    monkeypatch.setattr(adapter, '_BINARY_SHA256', adapter.hashlib.sha256(binary.read_bytes()).hexdigest())
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
            assert (root / 'input.cir').read_text() == request_model.netlist
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
