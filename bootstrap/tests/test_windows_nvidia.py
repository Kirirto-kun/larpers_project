"""Run the real helper with mocked machine, network and installer boundaries.

No test enumerates the host GPU, contacts NVIDIA, or executes an installer.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "bootstrap/public/windows-nvidia.ps1"
STARTER = ROOT / "bootstrap/public/Connect.ps1.in"
HOSTS = [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell boundary tests")


def gpu(device="2D98", *, version=None, code=28, name="Microsoft Basic Display Adapter", **extra):
    return {"PNPDeviceID": f"PCI\\VEN_10DE&DEV_{device}&SUBSYS_12341043\\4&123&0&0009",
            "DeviceID": f"PCI\\VEN_10DE&DEV_{device}&SUBSYS_12341043\\4&123&0&0009",
            "Name": name, "PNPClass": "Display", "Present": True,
            "ClassGuid": "{4d36e968-e325-11ce-bfc1-08002be10318}",
            "CompatibleID": ["PCI\\CC_030000", "PCI\\CC_0300"],
            "ConfigManagerErrorCode": code, "DriverVersion": version, **extra}


def scenario(tmp_path, host, *, entities=None, videos=None, smi=None, after=None,
             after_smi=None, signature="Valid", subject="CN=NVIDIA Corporation, O=NVIDIA Corporation, C=US",
             install_exit=0, download_exit=0, http="200", arch="amd64", build="22631", action="ensure",
             tamper=None, repeat=False, cancel=False):
    settings = {"entities": entities or [], "videos": videos or [], "smi": smi,
                "after": after, "after_smi": after_smi, "signature": signature,
                "subject": subject, "install_exit": install_exit, "download_exit": download_exit,
                "http": http, "arch": arch, "build": build, "action": action,
                "tamper": tamper, "repeat": repeat, "cancel": cancel}
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(settings), encoding="utf-8")
    wrapper = tmp_path / "boundary.ps1"
    wrapper.write_text(r'''
$ErrorActionPreference = 'Stop'
[Environment]::SetEnvironmentVariable('PSModulePath', (Join-Path $PSHOME 'Modules'), 'Process')
$fixture = Get-Content -LiteralPath $env:NVIDIA_FIXTURE -Raw | ConvertFrom-Json
$script:installed = 0
$script:downloads = @()
$script:launches = @()
$script:signatures = 0
# Use the starter's actual private-directory and no-reparse-point implementation.
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:NVIDIA_STARTER, [ref]$tokens, [ref]$parseErrors)
foreach ($name in @('Assert-NoLinks', 'Protect-Directory')) {
    $definition = $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]}, $true) | Where-Object Name -eq $name | Select-Object -First 1
    . ([scriptblock]::Create($definition.Extent.Text))
}
. $env:NVIDIA_HELPER
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    switch ($ClassName) {
        'Win32_PnPEntity' { if ($script:installed -and $null -ne $fixture.after) { $fixture.after } else { $fixture.entities } }
        'Win32_VideoController' { if ($script:installed -and $null -ne $fixture.after) { $fixture.after } else { $fixture.videos } }
        'Win32_OperatingSystem' { [pscustomobject]@{BuildNumber=$fixture.build; ProductType=1; OSArchitecture='64-bit'} }
        default { throw "Unmocked CIM class $ClassName" }
    }
}
function Invoke-CampusNvidiaSmi {
    if ($script:installed -and $null -ne $fixture.after_smi) { $fixture.after_smi } else { $fixture.smi }
}
function Get-AuthenticodeSignature {
    param($LiteralPath, $ErrorAction)
    $script:signatures++
    [pscustomobject]@{Status=$fixture.signature; SignatureType='Authenticode'; SignerCertificate=[pscustomobject]@{Subject=$fixture.subject}}
}
$nativeCurl = Join-Path ([Environment]::GetFolderPath('Windows')) 'System32\curl.exe'
Set-Item -LiteralPath ('Function:global:' + $nativeCurl) -Value {
    param([Parameter(ValueFromRemainingArguments=$true)]$Rest)
    $script:downloads += ,@($Rest)
    $index = [Array]::IndexOf($Rest, '--output')
    if ($index -lt 0) { throw 'Download must name output file' }
    [IO.File]::WriteAllBytes($Rest[$index + 1], [Text.Encoding]::UTF8.GetBytes('fake signed vendor package, never executable'))
    $global:LASTEXITCODE = [int]$fixture.download_exit
    $fixture.http
}
function Start-Process {
    param($FilePath, $Verb, [switch]$Wait, [switch]$PassThru, $ArgumentList, $ErrorAction, $WindowStyle)
    $script:launches += [pscustomobject]@{path=$FilePath; verb=$Verb; wait=[bool]$Wait; pass_thru=[bool]$PassThru; arguments=$ArgumentList; window=$WindowStyle}
    if ($fixture.cancel) { throw [ComponentModel.Win32Exception]::new(1223) }
    $script:installed++
    [pscustomobject]@{ExitCode=[int]$fixture.install_exit}
}
$state = Join-Path $env:NVIDIA_TESTROOT 'private state'
$values = @(); $caught = $null; $devices = @()
try {
    if ($fixture.action -eq 'devices') { $devices = @(Get-CampusNVIDIADevices) }
    else {
        $values = @(Ensure-CampusNVIDIA -StateDir $state -Architecture $fixture.arch)
        if ($fixture.tamper) {
            $file = Get-ChildItem -LiteralPath $state -Filter '*.exe' -Recurse | Select-Object -First 1
            if ($fixture.tamper -eq 'acl') {
                $acl = New-Object Security.AccessControl.FileSecurity
                $acl.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User)
                $acl.SetAccessRuleProtection($true, $false)
                $world = New-Object Security.Principal.SecurityIdentifier('S-1-1-0')
                $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($world, 'FullControl', 'Allow')))
                if ($PSVersionTable.PSEdition -eq 'Desktop') { [IO.File]::SetAccessControl($file.FullName, $acl) }
                else { [IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($file.FullName), $acl) }
            } else { [IO.File]::WriteAllText($file.FullName, 'tampered payload') }
        }
        if ($fixture.repeat -or $fixture.tamper) {
            $script:installed = 0
            $values += @(Ensure-CampusNVIDIA -StateDir $state -Architecture $fixture.arch)
        }
    }
} catch { $caught = $_.Exception.Message }
$receipts = @(Get-ChildItem -LiteralPath $state -Filter '*.json' -Recurse -ErrorAction SilentlyContinue | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json })
Write-Output ('RESULT:' + (ConvertTo-Json -Depth 12 -Compress -InputObject @{values=@($values); error=$caught; devices=@($devices); downloads=@($script:downloads); launches=@($script:launches); signatures=$script:signatures; receipts=@($receipts); state_exists=[IO.Directory]::Exists($state)}))
''', encoding="utf-8")
    result = subprocess.run([shutil.which(host), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
                            env={**os.environ, "NVIDIA_FIXTURE": str(fixture), "NVIDIA_HELPER": str(HELPER),
                                 "NVIDIA_STARTER": str(STARTER), "NVIDIA_TESTROOT": str(tmp_path)},
                            capture_output=True, text=True, errors="replace", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    output = [line[7:] for line in result.stdout.splitlines() if line.startswith("RESULT:")]
    assert len(output) == 1, result.stdout + result.stderr
    return json.loads(output[0])


@pytest.mark.parametrize("host", HOSTS)
def test_no_nvidia_hardware_leaves_host_and_cache_untouched(tmp_path, host):
    amd = gpu("150E", name="AMD Radeon Graphics")
    amd["PNPDeviceID"] = amd["DeviceID"] = amd["DeviceID"].replace("VEN_10DE", "VEN_1002")
    result = scenario(tmp_path, host, entities=[amd], videos=[amd])
    assert result["error"] is None
    assert result["values"] == [0]
    assert not result["state_exists"] and not result["downloads"] and not result["launches"]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("device", ["2D98", "2DD8"])
def test_pci_display_detects_5050_without_nvidia_driver_or_smi(tmp_path, host, device):
    raw = gpu(device, PNPClass=None, ClassGuid=None)
    result = scenario(tmp_path, host, entities=[raw], action="devices")
    assert result["error"] is None and len(result["devices"]) == 1
    assert f"DEV_{device}" in result["devices"][0]["PNPDeviceID"]
    assert not result["downloads"] and not result["launches"]


@pytest.mark.parametrize("host", HOSTS)
def test_nvidia_audio_and_absent_phantom_display_are_not_gpus(tmp_path, host):
    audio = gpu("22BB", PNPClass="MEDIA", ClassGuid="{audio}", CompatibleID=["PCI\\CC_040300"])
    phantom = gpu(Present=False)
    result = scenario(tmp_path, host, entities=[audio, phantom], action="devices")
    assert result["devices"] == []


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("version,smi_version", [("32.0.15.7688", "576.88"), ("32.0.16.2124", "621.24")])
def test_working_compatible_driver_never_downloads_or_reinstalls(tmp_path, host, version, smi_version):
    card = gpu(version=version, code=0, name="NVIDIA GeForce RTX 5050 Laptop GPU")
    result = scenario(tmp_path, host, entities=[card], videos=[card], smi=f"NVIDIA GeForce RTX 5050 Laptop GPU, {smi_version}, 0x2D9810DE")
    assert result["error"] is None and result["values"] == [0]
    assert not result["downloads"] and not result["launches"] and not result["state_exists"]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("basic_version", [None, "10.0.26108.1"])
def test_missing_driver_downloads_only_official_notebook_package_and_waits_for_uac(tmp_path, host, basic_version):
    fixed = gpu(version="32.0.16.2114", code=0, name="NVIDIA GeForce RTX 5050 Laptop GPU")
    basic = gpu(version=basic_version)
    result = scenario(tmp_path, host, entities=[basic], videos=[basic], after=[fixed], after_smi="NVIDIA GeForce RTX 5050 Laptop GPU, 621.14, 0x2D9810DE")
    assert result["error"] is None and result["values"] == [0]
    args = result["downloads"][0]
    assert args[-1] == "https://us.download.nvidia.com/Windows/621.14/621.14-notebook-win10-win11-64bit-international-dch-whql.exe"
    assert args[args.index("--max-time") + 1] == "1800"
    assert int(args[args.index("--max-filesize") + 1]) == 3 * 1024 ** 3
    assert args[args.index("--max-redirs") + 1] == "0"
    assert "--cacert" not in args and "--insecure" not in args and "--ssl-revoke-best-effort" not in args
    launch = result["launches"][0]
    assert launch["verb"] == "RunAs" and launch["wait"] and launch["pass_thru"]
    assert not launch["arguments"], "do not invent SFX installer flags"
    assert result["signatures"] >= 2, "verify download and again immediately before execution"
    assert len(result["receipts"]) == 1 and len(result["receipts"][0]["sha256"]) == 64


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("device,arch,build", [("2D83", "amd64", "22631"), ("FFFF", "amd64", "22631"), ("2D98", "arm64", "22631"), ("2D98", "amd64", "19045")])
def test_no_guessed_package_for_desktop_unknown_model_arm_or_windows10(tmp_path, host, device, arch, build):
    result = scenario(tmp_path, host, entities=[gpu(device)], arch=arch, build=build)
    assert result["error"] and "nvidia.com" in result["error"]
    assert not result["downloads"] and not result["launches"]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("status,subject", [("HashMismatch", "CN=NVIDIA Corporation, O=NVIDIA Corporation, C=US"), ("Valid", "CN=Another Vendor, O=Another Vendor, C=US"), ("Valid", "CN=NVIDIA Corporation, O=NVIDIA Corporation Evil, C=US")])
def test_unsigned_tampered_or_wrong_publisher_package_never_elevates(tmp_path, host, status, subject):
    result = scenario(tmp_path, host, entities=[gpu()], signature=status, subject=subject)
    assert result["error"] and "signature" in result["error"].lower()
    assert not result["launches"]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("exit_code", [1, 3010, 1641])
def test_vendor_reboot_exit_is_normalized_without_restarting_windows(tmp_path, host, exit_code):
    result = scenario(tmp_path, host, entities=[gpu()], install_exit=exit_code)
    assert result["error"] is None and result["values"] == [3010]
    assert len(result["launches"]) == 1


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("exit_code", [7, 1602])
def test_vendor_failure_stops_before_any_readiness_result(tmp_path, host, exit_code):
    result = scenario(tmp_path, host, entities=[gpu()], install_exit=exit_code)
    assert result["error"] and str(exit_code) in result["error"]
    assert result["values"] == []


@pytest.mark.parametrize("host", HOSTS)
def test_uac_cancellation_has_clear_error(tmp_path, host):
    result = scenario(tmp_path, host, entities=[gpu()], cancel=True)
    assert result["error"] and "cancel" in result["error"].lower()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("exit_code,http", [(22, "403"), (0, "302"), (63, "200")])
def test_failed_or_redirected_download_never_elevates(tmp_path, host, exit_code, http):
    result = scenario(tmp_path, host, entities=[gpu()], download_exit=exit_code, http=http)
    assert result["error"] and not result["launches"]


@pytest.mark.parametrize("host", HOSTS)
def test_cached_package_is_sha_and_signature_verified_before_every_use(tmp_path, host):
    result = scenario(tmp_path, host, entities=[gpu()], install_exit=3010, repeat=True)
    assert result["error"] is None and result["values"] == [3010, 3010]
    assert len(result["downloads"]) == 1 and len(result["launches"]) == 2
    assert result["signatures"] >= 3


@pytest.mark.parametrize("host", HOSTS)
def test_modified_cached_package_cannot_elevate_again(tmp_path, host):
    result = scenario(tmp_path, host, entities=[gpu()], install_exit=3010, tamper="payload")
    assert result["error"] and len(result["launches"]) == 1


@pytest.mark.parametrize("host", HOSTS)
def test_shared_writable_cached_package_cannot_elevate_again(tmp_path, host):
    result = scenario(tmp_path, host, entities=[gpu()], install_exit=3010, tamper="acl")
    assert result["error"] and len(result["launches"]) == 1


@pytest.mark.parametrize("host", HOSTS)
def test_newer_broken_driver_is_not_downgraded_to_pinned_package(tmp_path, host):
    card = gpu(version="32.0.16.2124", code=0)
    result = scenario(tmp_path, host, entities=[card], videos=[card])
    assert result["error"] and not result["downloads"] and not result["launches"]


@pytest.mark.parametrize("host", HOSTS)
def test_zero_exit_without_working_driver_does_not_claim_ready(tmp_path, host):
    result = scenario(tmp_path, host, entities=[gpu()])
    assert result["error"] and result["values"] == []
