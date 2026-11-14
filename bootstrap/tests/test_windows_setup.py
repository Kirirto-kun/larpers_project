import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'bootstrap/public/windows-setup.ps1'


def run_setup(tmp_path, *, present=False, answer='n', wsl=0, driver=0, docker=True):
    assert HELPER.exists(), 'Windows setup orchestrator must exist'
    trace = tmp_path / 'trace.json'
    script = tmp_path / 'setup.ps1'
    source = r'''
$ErrorActionPreference='Stop'
$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules')
. 'HELPER'
$script:trace = [Collections.Generic.List[string]]::new()
function Find-CampusDockerDesktop {
    DOCKER
}
function Get-CampusNVIDIADevices { DEVICES }
function Read-Host { $script:trace.Add('consent'); return 'ANSWER' }
function Ensure-CampusWSL { $script:trace.Add('wsl'); return WSL }
function Ensure-CampusNVIDIA { $script:trace.Add('nvidia'); return DRIVER }
try {
    $result = Initialize-CampusWindows -StateDir 'unused' -Architecture 'amd64'
    @{ result=$result; calls=@($script:trace.ToArray()) } | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath 'TRACE' -Encoding UTF8
} catch {
    @{ error=$_.Exception.Message; calls=@($script:trace.ToArray()) } | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath 'TRACE' -Encoding UTF8
    exit 1
}
'''
    source = source.replace('HELPER', str(HELPER).replace("'", "''")).replace('TRACE', str(trace).replace("'", "''"))
    source = source.replace('DOCKER', r"return [pscustomobject]@{ Bin='C:\Docker\resources\bin'; Desktop='C:\Docker\Docker Desktop.exe' }" if docker else "throw 'Docker Desktop must be installed manually'")
    source = source.replace('DEVICES', "[pscustomobject]@{ Name='NVIDIA RTX 5050 Laptop GPU' }" if present else 'return')
    source = source.replace('ANSWER', answer).replace('WSL }', str(wsl)+' }').replace('DRIVER }', str(driver)+' }')
    script.write_text(source, encoding='utf-8')
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-File', str(script)], capture_output=True, timeout=20)
    assert trace.exists(), result.stderr.decode(errors='replace')
    return result.returncode, json.loads(trace.read_text(encoding='utf-8-sig'))


@pytest.mark.skipif(os.name != 'nt', reason='native PowerShell boundary')
def test_cpu_setup_skips_gpu_prompt_and_driver(tmp_path):
    code, value = run_setup(tmp_path)
    assert code == 0 and value['calls'] == ['wsl']
    assert value['result']['GPU'] is False and value['result']['RestartRequired'] is False


@pytest.mark.skipif(os.name != 'nt', reason='native PowerShell boundary')
def test_gpu_consent_precedes_host_changes_and_is_forwarded(tmp_path):
    code, value = run_setup(tmp_path, present=True, answer='y')
    assert code == 0 and value['calls'] == ['consent', 'wsl', 'nvidia']
    assert value['result']['GPU'] is True


@pytest.mark.skipif(os.name != 'nt', reason='native PowerShell boundary')
def test_declining_gpu_skips_driver_install(tmp_path):
    code, value = run_setup(tmp_path, present=True)
    assert code == 0 and value['calls'] == ['consent', 'wsl']
    assert value['result']['GPU'] is False


@pytest.mark.skipif(os.name != 'nt', reason='native PowerShell boundary')
@pytest.mark.parametrize('component', ['wsl', 'driver'])
def test_restart_status_is_preserved_before_connection(tmp_path, component):
    code, value = run_setup(tmp_path, present=True, answer='y', **{component: 3010})
    assert code == 0 and value['result']['RestartRequired'] is True


@pytest.mark.skipif(os.name != 'nt', reason='native PowerShell boundary')
def test_missing_manual_docker_prevents_machine_changes(tmp_path):
    code, value = run_setup(tmp_path, docker=False)
    assert code == 1 and value['calls'] == []


def test_public_template_has_restart_fence_and_explicit_gpu_handoff():
    source = (ROOT / 'bootstrap/public/Connect.ps1.in').read_text(encoding='utf-8')
    assert '@WINDOWS_SETUP_HELPERS@' in source
    assert source.index('if ($setup.RestartRequired)') < source.index('& $runner @arguments')
    assert "'--gpu='" in source
