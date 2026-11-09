"""Required physics proof gates fail on missing, skipped or misidentified work."""
import copy
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml

from qsim.release_probe import release_cases

ROOT = Path(__file__).resolve().parents[1]
IMAGE_ID = 'sha256:'+'a'*64
REF = 'registry.example.invalid/worker@'+IMAGE_ID
CORE = (
    'test_joined_lindblad_operation_families',
    'test_joined_scqubits_native_spectrum_and_matrix_elements',
    'test_joined_scqubits_export_to_lindblad_is_a_client_sequence',
    'test_joined_josim_sfq_and_circuit_pool_separation',
    'test_joined_native_lindblad_scqubits_josim_results_restore_with_invitation',
)


def inline(marker):
    script = (ROOT/'tests/check_worker_image.sh').read_text()
    return script.split("<<'"+marker+"'\n",1)[1].split('\n'+marker,1)[0]


def receipt():
    root = ET.Element('testsuites')
    suite = ET.SubElement(root,'testsuite',tests='1',errors='0',failures='0',skipped='0')
    cases = [(name,'tests.test_physics_lifecycle') for name in CORE]
    cases += [('test_packaged_release_physics_cases['+name+']','tests.test_cd_acceptance')
              for name,body in release_cases().items()
              if body is None or body.get('engine') in ('lindblad','scqubits','josim')]
    cases += [('test_all_smoke_cases_pass_real_workers','tests.test_cd_acceptance')]
    cases += [('test_packaged_physics_examples','tests.test_physics_examples')]
    for name,module in cases:
        case = ET.SubElement(suite,'testcase',classname=module,name=name)
        properties=ET.SubElement(case,'properties')
        for key,value in [('joined_container_image_id',IMAGE_ID),('joined_synthetic_scheduler_ref',REF),
                          ('joined_placement','synthetic SkyPilot placement; real Docker native execution'),
                          ('joined_successful_native_processes','1')]:
            ET.SubElement(properties,'property',name=key,value=value)
    return root


def run_gate(tmp_path,root):
    path = tmp_path/'proof.xml'
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    return subprocess.run([sys.executable,'-',str(path)],
        input=inline('WQS_VERIFY_PHYSICS_JUNIT'),text=True,capture_output=True,
        cwd=ROOT,env={**os.environ,'WQS_JOINED_WORKER_REF':REF})


def test_complete_receipt_succeeds(tmp_path):
    result=run_gate(tmp_path,receipt())
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('mutation',['skip','xfail','failure','error','missing_case','duplicate_case',
    'missing_examples','missing_lifecycle','wrong_image','wrong_ref','missing_provenance','wrong_placement',
    'zero_native_processes','missing_native_processes','invalid_native_processes','empty'])
def test_incomplete_proof_receipt_is_fatal(tmp_path,mutation):
    root=receipt();suite=root[0];first=suite[0]
    if mutation in ('skip','xfail','failure','error'):
        ET.SubElement(first,'skipped' if mutation in ('skip','xfail') else mutation,
                      type='pytest.xfail' if mutation=='xfail' else mutation)
    elif mutation in ('missing_case','duplicate_case'):
        case=next(c for c in suite if c.get('name','').startswith('test_packaged_release_physics_cases['))
        if mutation=='missing_case':suite.remove(case)
        else:suite.append(copy.deepcopy(case))
    elif mutation=='missing_examples':
        suite.remove(next(c for c in suite if c.get('classname')=='tests.test_physics_examples'))
    elif mutation=='missing_lifecycle':
        for c in list(suite):
            if c.get('classname')=='tests.test_physics_lifecycle':suite.remove(c)
    elif mutation=='wrong_image':
        first.find("properties/property[@name='joined_container_image_id']").set('value','sha256:'+'b'*64)
    elif mutation=='wrong_ref':
        first.find("properties/property[@name='joined_synthetic_scheduler_ref']").set('value','registry.example.invalid/other@'+IMAGE_ID)
    elif mutation=='missing_provenance':first.remove(first.find('properties'))
    elif mutation=='wrong_placement':
        first.find("properties/property[@name='joined_placement']").set('value','live SkyPilot proof')
    elif mutation in ('zero_native_processes','missing_native_processes','invalid_native_processes'):
        property=first.find("properties/property[@name='joined_successful_native_processes']")
        if mutation=='missing_native_processes':first.find('properties').remove(property)
        else:property.set('value','0' if mutation=='zero_native_processes' else 'not-a-count')
    else:suite.clear()
    assert run_gate(tmp_path,root).returncode != 0


@pytest.mark.parametrize('mutation',['missing','zero_native_processes'])
def test_legacy_packaged_smoke_requires_native_execution(tmp_path,mutation):
    root=receipt();suite=root[0]
    case=next(c for c in suite if c.get('name')=='test_all_smoke_cases_pass_real_workers')
    if mutation=='missing':suite.remove(case)
    else:case.find("properties/property[@name='joined_successful_native_processes']").set('value','0')
    assert run_gate(tmp_path,root).returncode != 0


@pytest.mark.parametrize('case',[
    "class Proof(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\nunittest.main()\n",
    "class Proof(unittest.TestCase):\n @unittest.skip('missing native package')\n def test_skip(self): pass\nunittest.main()\n",
    "class Proof(unittest.TestCase):\n @unittest.expectedFailure\n def test_xfail(self): self.fail('missing certificate')\nunittest.main()\n",
    "class Proof(unittest.TestCase):\n def test_fail(self): self.fail('bad native result')\nunittest.main()\n",
    "unittest.main()\n",
    "# No main or tests at all.\n",
])
def test_image_unittest_guard_requires_executed_success(case):
    source=inline('WQS_REQUIRED_UNITTEST')+'\nimport unittest\n'+case+'\n'+inline('WQS_REQUIRED_UNITTEST_END')
    result=subprocess.run([sys.executable,'-'],input=source,text=True,capture_output=True)
    assert (result.returncode==0)==('test_ok' in case),result.stderr


def test_worker_image_gate_runs_whole_joined_release_and_example_modules():
    script=(ROOT/'tests/check_worker_image.sh').read_text()
    command=script.split('WQS_JOINED_WORKER_REF=',1)[1].split('WQS_VERIFY_PHYSICS_JUNIT',1)[0]
    for name in ('test_physics_lifecycle.py','test_cd_acceptance.py','test_physics_examples.py'):
        assert 'tests/'+name in command
    assert '-o xfail_strict=true' in command
    assert '-k ' not in command and '-m ' not in command and '--deselect' not in command
    assert script.count('required_unittest_source tests/')==3
    assert 'PhysicsCPUAdmission' in script and 'WQS_IMAGE_TEST=1' in script
    assert '--cpus 2 --memory 4g --memory-swap 4g' in script
    assert '--user 1000:1000' in script and '--cap-drop ALL' in script
    assert 'registry.example.invalid/worker@$image_id' in script
    assert 'docker image inspect "$image"' in script
    assert 'rm -f test-results/physics-joined.xml' in script


def test_runner_paths_and_required_receipt_artifact_remain_explicit():
    jobs=yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text())['jobs']
    assert jobs['local']['runs-on']==['self-hosted','Linux','X64','wqs-ci-local']
    for name in ('local','hosted'):
        assert any(step.get('run')=='bash tools/ci/check.sh' for step in jobs[name]['steps'])
    image=jobs['worker-image']
    assert image['runs-on']=='ubuntu-24.04'
    assert any(step.get('run')=='bash tools/ci/check-release.sh' for step in image['steps'])
    upload=next(step for step in image['steps'] if step.get('with',{}).get('path')=='test-results/physics-joined.xml')
    assert upload['if']=='always()' and upload['with']['if-no-files-found']=='error'
    summary=jobs['result']['steps'][0]['run']
    assert "os.environ['IMAGE_RESULT'] == 'success'" in summary
    assert 'native physics' in summary and 'synthetic' in summary
    normal=(ROOT/'tools/ci/check.sh').read_text()
    assert 'pytest "${pytest_args[@]}"' in normal and '--max-worker-restart=0' in normal
    assert 'uv pip check' in normal and 'node --test tests/test_admin_console.cjs' in normal


@pytest.mark.parametrize('content',[None,'not XML'])
def test_absent_or_unparseable_receipt_is_fatal(tmp_path,content):
    path=tmp_path/'missing.xml'
    if content is not None:path.write_text(content)
    result=subprocess.run([sys.executable,'-',str(path)],input=inline('WQS_VERIFY_PHYSICS_JUNIT'),
        cwd=ROOT,text=True,capture_output=True,env={**os.environ,'WQS_JOINED_WORKER_REF':REF})
    assert result.returncode != 0


@pytest.mark.parametrize('present',[False,True])
def test_image_source_pipeline_cannot_mask_missing_or_empty_proof(tmp_path,present):
    import shlex
    source=(ROOT/'tests/check_worker_image.sh').read_text()
    wrapper=source[source.index('required_unittest_source() {'):source.index('# -i feeds')]
    path=tmp_path/'proof.py'
    if present:path.write_text('')
    command=('set -euo pipefail\n'+wrapper+'\nrequired_unittest_source '+shlex.quote(str(path))+
             ' | '+shlex.quote(sys.executable)+' -\n')
    result=subprocess.run(['bash','-c',command],capture_output=True,text=True)
    assert result.returncode != 0
