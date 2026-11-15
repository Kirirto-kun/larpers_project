"""Exercise the WSL bootstrap with fake OS/elevation boundaries, never host changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


HELPER = Path(__file__).resolve().parents[1] / "public/windows-wsl.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell bootstrap")


def run_wsl(tmp_path, *, version="2.6.3.0", feature=True, next_version="2.6.3.0",
            next_feature=True, code=0, cancel=False, virtualization=True,
            hypervisor=False, complete=True, host="powershell.exe", utf16=False, version_error=False):
    # The helper uses real native command invocation; only the executable locator,
    # CIM reads, and elevated process creation cross fake boundaries.
    tmp_path.mkdir(exist_ok=True)
    version_file = tmp_path / "version.txt"
    payload = "WSL version: " + version if version else ""
    version_file.write_bytes(payload.encode("utf-16-le" if utf16 else "ascii"))
    fake = tmp_path / "fake wsl.cmd"
    fake.write_text('@echo off\r\nif "%1"=="--version" (\r\n if "%WSL_TEST_VERSION_ERROR%"=="1" (\r\n  echo Invalid command line option: --version 1>&2\r\n  exit /b 1\r\n )\r\n type "%WSL_TEST_VERSION%"\r\n exit /b 0\r\n)\r\nexit /b 77\r\n', encoding="ascii")
    report = tmp_path / "result.json"
    script = tmp_path / "harness.ps1"
    script.write_text(r'''
$ErrorActionPreference='Stop'
[Environment]::SetEnvironmentVariable('PSModulePath', [IO.Path]::Combine($PSHOME,'Modules'), 'Process')
. $env:WSL_TEST_HELPER
$script:trace = [Collections.Generic.List[object]]::new()
$script:feature = $env:WSL_TEST_FEATURE -eq '1'
function Get-CampusWslNativePath { return $env:WSL_TEST_EXECUTABLE }
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    switch ($ClassName) {
        'Win32_OptionalFeature' {
            [pscustomobject]@{Name='VirtualMachinePlatform';InstallState=$(if($script:feature){1}else{2})}
            [pscustomobject]@{Name='Microsoft-Windows-Subsystem-Linux';InstallState=2}
        }
        'Win32_ComputerSystem' { [pscustomobject]@{HypervisorPresent=($env:WSL_TEST_HYPERVISOR -eq '1')} }
        'Win32_Processor' { [pscustomobject]@{VirtualizationFirmwareEnabled=($env:WSL_TEST_VIRTUALIZATION -eq '1')} }
        default { throw ('Unexpected CIM query: '+$ClassName) }
    }
}
function Start-Process {
    param($FilePath, $ArgumentList, $Verb, [switch]$PassThru, [switch]$Wait, $WindowStyle)
    $script:trace.Add([pscustomobject]@{path=$FilePath;arguments=@($ArgumentList);verb=$Verb;pass_thru=[bool]$PassThru})
    if($env:WSL_TEST_CANCEL -eq '1') { throw [ComponentModel.Win32Exception]::new(1223) }
    $script:feature = $env:WSL_TEST_NEXT_FEATURE -eq '1'
    $env:WSL_TEST_VERSION_ERROR='0'
    [IO.File]::WriteAllText($env:WSL_TEST_VERSION, 'WSL version: '+$env:WSL_TEST_NEXT_VERSION)
    $process=[pscustomobject]@{ExitCode=[int]$env:WSL_TEST_EXIT}
    $process | Add-Member ScriptMethod WaitForExit { param($milliseconds) return ($env:WSL_TEST_COMPLETE -eq '1') }
    return $process
}
try {
    $values=@(Ensure-CampusWSL -StateDir $env:WSL_TEST_STATE)
    $result=[pscustomobject]@{values=$values;trace=@($script:trace.ToArray());error=$null}
    [IO.File]::WriteAllText($env:WSL_TEST_REPORT,($result|ConvertTo-Json -Depth 8))
} catch {
    $result=[pscustomobject]@{values=@();trace=@($script:trace.ToArray());error=$_.Exception.Message}
    [IO.File]::WriteAllText($env:WSL_TEST_REPORT,($result|ConvertTo-Json -Depth 8))
    exit 17
}
''', encoding="utf-8-sig")
    env = {**os.environ, "WSL_TEST_HELPER": str(HELPER), "WSL_TEST_VERSION": str(version_file),
           "WSL_TEST_EXECUTABLE": str(fake), "WSL_TEST_REPORT": str(report),
           "WSL_TEST_STATE": str(tmp_path / "private cache 空间 & !"),
           "WSL_TEST_FEATURE": str(int(feature)), "WSL_TEST_NEXT_FEATURE": str(int(next_feature)),
           "WSL_TEST_NEXT_VERSION": next_version, "WSL_TEST_EXIT": str(code),
           "WSL_TEST_CANCEL": str(int(cancel)), "WSL_TEST_VIRTUALIZATION": str(int(virtualization)),
           "WSL_TEST_HYPERVISOR": str(int(hypervisor)), "WSL_TEST_COMPLETE": str(int(complete)),
           "WSL_TEST_VERSION_ERROR": str(int(version_error))}
    result = subprocess.run([host, "-NoLogo", "-NoProfile", "-File", str(script)],
                            env=env, capture_output=True, text=True, errors="replace", timeout=30)
    assert report.exists(), result.stdout + result.stderr
    return result, json.loads(report.read_text(encoding="utf-8-sig")), fake


def test_healthy_modern_wsl_is_read_only_even_without_legacy_wsl1_feature(tmp_path):
    process, result, _ = run_wsl(tmp_path)
    assert process.returncode == 0, result
    assert result["values"] == [0] and result["trace"] == []
    assert not (tmp_path / "private cache 空间 & !").exists()


def test_native_wsl_utf16_output_does_not_trigger_update_on_healthy_host(tmp_path):
    process, result, _ = run_wsl(tmp_path, utf16=True)
    assert process.returncode == 0, result
    assert result["values"] == [0] and result["trace"] == []


def test_legacy_wsl_rejecting_version_on_stderr_is_updated_instead_of_aborting(tmp_path):
    process, result, _ = run_wsl(tmp_path, version_error=True)
    assert process.returncode == 0, result
    assert result["values"] == [0]
    assert result["trace"][0]["arguments"] == ["--update", "--web-download"]


@pytest.mark.parametrize("version,feature,arguments", [
    ("", False, ["--install", "--no-distribution", "--web-download", "--no-launch"]),
    ("2.6.3.0", False, ["--install", "--no-distribution", "--web-download", "--no-launch"]),
    ("1.2.0.0", True, ["--update", "--web-download"]),
    ("", True, ["--update", "--web-download"]),
])
def test_missing_features_or_old_wsl_use_only_fixed_official_elevated_command(tmp_path, version, feature, arguments):
    process, result, fake = run_wsl(tmp_path, version=version, feature=feature)
    assert process.returncode == 0, result
    assert result["values"] == [0]
    assert result["trace"] == [{"path": str(fake), "arguments": arguments, "verb": "RunAs", "pass_thru": True}]


@pytest.mark.parametrize("code", [3010, 1641])
def test_installer_reboot_status_returns_3010_without_reboot_or_enrollment(tmp_path, code):
    process, result, _ = run_wsl(tmp_path, feature=False, code=code)
    assert process.returncode == 0, result
    assert result["values"] == [3010] and len(result["trace"]) == 1


def test_success_with_features_not_ready_requires_manual_restart(tmp_path):
    process, result, _ = run_wsl(tmp_path, feature=False, next_feature=False)
    assert process.returncode == 0, result
    assert result["values"] == [3010]


@pytest.mark.parametrize("case,keyword", [("cancel", "cancel"), ("failed", "23"), ("timeout", "30 minutes")])
def test_uac_cancellation_failed_install_and_timeout_stop_cleanly(tmp_path, case, keyword):
    process, result, _ = run_wsl(tmp_path, feature=False, cancel=case == "cancel",
                                 code=23 if case == "failed" else 0, complete=case != "timeout")
    assert process.returncode == 17
    assert keyword in result["error"].lower() and result["values"] == []
    assert len(result["trace"]) == 1


def test_disabled_firmware_virtualization_explains_bios_without_elevation(tmp_path):
    process, result, _ = run_wsl(tmp_path, feature=False, virtualization=False)
    assert process.returncode == 17
    assert "bios" in result["error"].lower() and result["trace"] == []


def test_running_hypervisor_is_ready_even_if_cpu_firmware_property_is_false(tmp_path):
    process, result, _ = run_wsl(tmp_path, virtualization=False, hypervisor=True)
    assert process.returncode == 0 and result["values"] == [0] and result["trace"] == []


def test_repeat_after_simulated_reboot_skips_elevation(tmp_path):
    _, first, _ = run_wsl(tmp_path, feature=False, next_feature=False, code=3010)
    assert first["values"] == [3010]
    _, second, _ = run_wsl(tmp_path)
    assert second["values"] == [0] and second["trace"] == []


def test_ps7_and_ps5_support_unicode_paths_and_same_scalar_contract(tmp_path):
    pwsh = shutil.which("pwsh.exe")
    if pwsh is None:
        candidates = [Path(p).parent / "pwsh.exe" for p in os.environ.get("PSModulePath", "").split(os.pathsep) if p]
        pwsh = next((str(p) for p in candidates if p.is_file()), None)
    if pwsh is None:
        pytest.skip("PowerShell 7 not available")
    process, result, _ = run_wsl(tmp_path / "空间 & !", feature=False, host=pwsh)
    assert process.returncode == 0 and result["values"] == [0], result
