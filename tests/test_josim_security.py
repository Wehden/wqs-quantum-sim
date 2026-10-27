import ast
from pathlib import Path

import pytest

from qsim.josim_validation import validate_josim_netlist


BASE = ('I1 0 1 DC 1u\nR1 1 0 5\nB1 1 0 J AREA=1\n'
        '.model J JJ(RTYPE=1 VG=2mV CAP=1pF R0=100 RN=10 ICRIT=1mA)\n'
        '.tran 1p 5p\n.print DEVV B1\n.end\n')


@pytest.mark.parametrize('statement', [
    '.include private', 'InClUdE private', '.file escaped.csv', 'FILE escaped.csv',
    '.control\ninclude private\n.endc', '.CONTROL\nfile escaped.csv\n.ENDC',
    '.param A=1', '.subckt S 1 0\n.ends', 'X1 1 0 S',
    '.iv J 1u escaped.csv', '.save all', '.plot V(1)',
    '.option seed=42', '.temp 4.2', '.neb 1e12',
    'V1 1 0 CUS(private.dat)', 'V1 1 0 NOISE(1 0 1p)',
    'V1 1 0 PWL(0 0 1p {A})', 'V1 1 0 DC (1+1)',
    'R2 1 0 5\n+ .include private', 'R2 1 0 5\n+ FILE escaped.csv',
    'R2 1 0 5 ; .include private', 'R2 1 0 5 $ .file escaped.csv',
    '.print DEVV B1 > escaped.csv', '.print "DEVV B1"', '.print V(1)',
    'I2 1 0 DC 1\n+\n+ .file private', 'R/../escape 1 0 5',
    '.include "private"', 'include /private', '.end\n.file escaped.csv',
])
def test_file_control_expression_and_path_constructs_are_rejected(statement):
    with pytest.raises(ValueError) as error:
        validate_josim_netlist(BASE.replace('.end', statement + '\n.end'))
    assert 'private' not in str(error.value)
    assert 'escaped.csv' not in str(error.value)


@pytest.mark.parametrize('prefix', ['+ .include private\n', '\x00', '\x1b', '\v', '\f', '\x7f', 'Ω', '\ud800'])
def test_non_ascii_control_or_orphan_continuations(prefix):
    with pytest.raises(ValueError):
        validate_josim_netlist(prefix + BASE)


def test_normalized_output_cannot_reintroduce_ignored_tail():
    with pytest.raises(ValueError):
        validate_josim_netlist(BASE + '.file escaped.csv\n')
    normalized, _ = validate_josim_netlist(BASE + '* .file comment only\n')
    assert '.FILE' not in normalized


def test_validator_module_has_no_native_or_process_imports():
    path = Path(__file__).parents[1] / 'qsim/josim_validation.py'
    tree = ast.parse(path.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert set(imports) <= {'math', 're', 'typing'}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {'eval', 'exec', 'compile', 'open', '__import__'}
                   for node in ast.walk(tree))


def test_request_rejection_and_missing_certificate_never_invoke_native_process(monkeypatch):
    import subprocess
    from qsim.josim_models import JoSIMRequest

    def forbidden(*args, **kwargs):
        raise AssertionError('validation invoked a native process')

    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError):
        JoSIMRequest(netlist=BASE.replace('.end', '.file private\n.end'))
    request = JoSIMRequest(netlist=BASE)
    assert request.canonical_envelope()['engine'] == 'josim'
    with pytest.raises(ValueError, match='resource_limit'):
        request.admission_plan()


def test_request_model_has_only_api_safe_imports():
    path = Path(__file__).parents[1] / 'qsim/josim_models.py'
    tree = ast.parse(path.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert set(imports) <= {'typing', 'pydantic', 'josim_validation', 'physics_results'}
