"""Exercise bootstrap scripts as processes with downloads replaced at the edge."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SECRET = "invite-secret-秘密 value&literal"
EXTRA = ["--endpoint", "https://coordinator.example", "--label", "设备 with spaces;$(literal)"]


def posix(path):
    value = str(path).replace("\\", "/")
    if len(value) > 1 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


def shell_path():
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
        return str(candidate) if candidate.exists() else None
    return shutil.which("sh")


@pytest.fixture
def sh_bootstrap(tmp_path):
    shell = shell_path()
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = ROOT / "bootstrap/join.sh"
    command_directory = tmp_path / "mock commands"
    command_directory.mkdir()
    source = tmp_path / "downloaded payload"
    source.write_text('#!/bin/sh\nprintf \'%s\\0\' "$@" > "$BOOTSTRAP_EXECUTION_MARKER"\nexit "${BOOTSTRAP_EXIT_CODE:-0}"\n', encoding="utf-8", newline="\n")
    source.chmod(0o700)
    curl = command_directory / "curl"
    curl.write_text('''#!/bin/sh
set -eu
printf '%s\\n' "$@" > "$BOOTSTRAP_CURL_TRACE"
output=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) output=$2; shift 2 ;;
        *) shift ;;
    esac
done
cp "$BOOTSTRAP_BINARY_SOURCE" "$output"
printf '%s' "${BOOTSTRAP_HTTP_STATUS:-200}"
exit "${BOOTSTRAP_CURL_EXIT:-0}"
''', encoding="utf-8", newline="\n")
    curl.chmod(0o700)
    uname = command_directory / "uname"
    uname.write_text('''#!/bin/sh
case "$1" in
    -s) printf '%s\\n' "${BOOTSTRAP_TEST_OS:-Linux}" ;;
    -m) printf '%s\\n' "${BOOTSTRAP_TEST_ARCH:-x86_64}" ;;
    *) exit 1 ;;
esac
''', encoding="utf-8", newline="\n")
    uname.chmod(0o700)
    cache = tmp_path / "用户 cache"
    marker = tmp_path / "execution.bin"
    trace = tmp_path / "curl.txt"

    def run(*, sha=None, base="https://releases.example/campus/0.1.0", extras=EXTRA, environment=None):
        assert script.exists(), "the POSIX bootstrap must be implemented"
        env = dict(os.environ)
        env.update({"BOOTSTRAP_BINARY_SOURCE": posix(source), "BOOTSTRAP_EXECUTION_MARKER": posix(marker), "BOOTSTRAP_CURL_TRACE": posix(trace)})
        env.update(environment or {})
        digest = hashlib.sha256(source.read_bytes()).hexdigest() if sha is None else sha
        result = subprocess.run([
            shell, "-c", 'PATH="$1:/usr/bin:/bin"; export PATH; shift; exec sh "$@"', "bootstrap-test",
            posix(command_directory), posix(script), "--release-base-url", base,
            "--sha256", digest, "--invite", SECRET, "--cache-dir", posix(cache), "--", *extras,
        ], env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        assert SECRET not in result.stdout + result.stderr
        return result

    return run, cache, marker, trace, source


def test_sh_verifies_download_and_preserves_unicode_and_literal_argv(sh_bootstrap):
    run, cache, marker, trace, source = sh_bootstrap
    result = run()
    assert result.returncode == 0, result.stderr
    assert marker.read_bytes().decode("utf-8").split("\0")[:-1] == ["join", SECRET, *EXTRA]
    assert len(list(cache.glob("campus-linux-amd64-*"))) == 1
    arguments = trace.read_text().splitlines()
    assert "https://releases.example/campus/0.1.0/campus-linux-amd64" in arguments
    assert arguments[arguments.index("--proto") + 1] == "=https"
    assert "--location" not in arguments and "-L" not in arguments
    assert SECRET not in trace.read_text()


def test_sh_checksum_mismatch_never_executes_download(sh_bootstrap):
    run, cache, marker, trace, source = sh_bootstrap
    result = run(sha="0" * 64)
    assert result.returncode != 0
    assert not marker.exists()
    assert not list(cache.iterdir())


@pytest.mark.parametrize("base", ["http://releases.example", "https://user:pass@example.com/path", "https://example.com/path?query=1", "https://example.com/path#fragment", "https:///no-host"])
def test_sh_rejects_untrusted_or_ambiguous_release_urls_before_download(sh_bootstrap, base):
    run, cache, marker, trace, source = sh_bootstrap
    assert run(base=base).returncode != 0
    assert not marker.exists()
    assert not trace.exists()


@pytest.mark.parametrize("status", ["301", "302", "307", "308"])
def test_sh_refuses_redirects_even_when_payload_checksum_would_match(sh_bootstrap, status):
    run, cache, marker, trace, source = sh_bootstrap
    assert run(environment={"BOOTSTRAP_HTTP_STATUS": status}).returncode != 0
    assert not marker.exists()
    assert list(cache.iterdir()) == []


def test_sh_rechecks_cached_binary_before_execute(sh_bootstrap):
    run, cache, marker, trace, source = sh_bootstrap
    assert run().returncode == 0
    marker.unlink()
    cached, = cache.glob("campus-*")
    cached.write_bytes(b"tampered cached executable")
    assert run().returncode != 0
    assert not marker.exists()


def test_sh_uses_native_arm64_and_propagates_runner_exit_code(sh_bootstrap):
    run, cache, marker, trace, source = sh_bootstrap
    result = run(environment={"BOOTSTRAP_TEST_OS": "Darwin", "BOOTSTRAP_TEST_ARCH": "arm64", "BOOTSTRAP_EXIT_CODE": "7"})
    assert result.returncode == 7
    assert "https://releases.example/campus/0.1.0/campus-darwin-arm64" in trace.read_text().splitlines()


def test_sh_unknown_architecture_stops_before_download(sh_bootstrap):
    run, cache, marker, trace, source = sh_bootstrap
    assert run(environment={"BOOTSTRAP_TEST_ARCH": "mips"}).returncode != 0
    assert not trace.exists()
    assert not marker.exists()


@pytest.mark.parametrize("sha", ["", "short", "g" * 64])
def test_sh_requires_a_trusted_sha_before_download(sh_bootstrap, sha):
    run, cache, marker, trace, source = sh_bootstrap
    assert run(sha=sha).returncode != 0
    assert not trace.exists()
    assert not marker.exists()


def test_sh_refuses_a_linked_cache(sh_bootstrap, tmp_path):
    run, cache, marker, trace, source = sh_bootstrap
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, cache, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    assert run().returncode != 0
    assert not trace.exists()
    assert list(outside.iterdir()) == []


@pytest.fixture(params=["pwsh", "powershell"])
def ps_bootstrap(tmp_path, request):
    if os.name != "nt":
        pytest.skip("Windows executable/ACL bootstrap test requires Windows")
    powershell = shutil.which(request.param)
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    script = ROOT / "bootstrap/join.ps1"
    source = Path(sys.base_prefix) / "python.exe"
    assert source.exists(), "test fixture requires the current Python executable"
    cache = tmp_path / "用户 cache"
    marker = tmp_path / "execution.json"
    trace = tmp_path / "request.json"
    (tmp_path / "join").write_text('import json, os, sys\nfrom pathlib import Path\nPath(os.environ["BOOTSTRAP_EXECUTION_MARKER"]).write_text(json.dumps(sys.argv[1:], ensure_ascii=False), encoding="utf-8")\nsys.exit(int(os.environ.get("BOOTSTRAP_EXIT_CODE", "0")))\n', encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text('''function Invoke-WebRequest {
    [CmdletBinding()]
    param([Uri]$Uri, [string]$OutFile, [int]$MaximumRedirection, [switch]$UseBasicParsing, [switch]$PassThru, [int]$TimeoutSec, [string]$SslProtocol)
    $cacheAcl = Get-Acl -LiteralPath $env:BOOTSTRAP_CACHE
    @{uri=$Uri.AbsoluteUri; redirects=$MaximumRedirection; protected=$cacheAcl.AreAccessRulesProtected;
      principals=@($cacheAcl.Access | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value });
      owner=$cacheAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value;
      current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    } | ConvertTo-Json | Set-Content -LiteralPath $env:BOOTSTRAP_REQUEST_TRACE -Encoding utf8
    [IO.File]::Copy($env:BOOTSTRAP_BINARY_SOURCE, $OutFile, $true)
    [pscustomobject]@{StatusCode=[int]$env:BOOTSTRAP_HTTP_STATUS}
}
$bootstrapParsedArguments = $env:BOOTSTRAP_JOIN_ARGS | ConvertFrom-Json
$bootstrapParameters = @{
    ReleaseBaseUrl=$env:BOOTSTRAP_RELEASE_BASE
    Sha256=$env:BOOTSTRAP_DIGEST
    Invite=$env:BOOTSTRAP_INVITE
    CacheDirectory=$env:BOOTSTRAP_CACHE
    JoinArguments=[string[]]$bootstrapParsedArguments
}
& $env:BOOTSTRAP_SCRIPT @bootstrapParameters
exit $LASTEXITCODE
''', encoding="utf-8")

    def run(*, sha=None, base="https://releases.example/campus/0.1.0", environment=None):
        assert script.exists(), "the PowerShell bootstrap must be implemented"
        env = dict(os.environ)
        env.update({
            "BOOTSTRAP_BINARY_SOURCE": str(source), "BOOTSTRAP_EXECUTION_MARKER": str(marker), "BOOTSTRAP_REQUEST_TRACE": str(trace),
            "BOOTSTRAP_RELEASE_BASE": base, "BOOTSTRAP_DIGEST": hashlib.sha256(source.read_bytes()).hexdigest() if sha is None else sha,
            "BOOTSTRAP_INVITE": SECRET, "BOOTSTRAP_CACHE": str(cache), "BOOTSTRAP_SCRIPT": str(script),
            "BOOTSTRAP_JOIN_ARGS": json.dumps(EXTRA), "BOOTSTRAP_HTTP_STATUS": "200", "PYTHONHOME": sys.base_prefix,
            "PYTHONNOUSERSITE": "1", "PATH": sys.base_prefix + os.pathsep + os.environ.get("PATH", ""),
        })
        if request.param == "powershell":
            # A Python child otherwise inherits the outer PowerShell Core
            # module path, unlike native Windows PowerShell startup. Core's
            # Security module cannot load into .NET Framework 5.1.
            env["PSModulePath"] = str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/Modules")
        env.update(environment or {})
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                                env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        assert SECRET not in result.stdout + result.stderr
        return result

    return run, cache, marker, trace


def test_ps_verifies_download_and_preserves_unicode_and_literal_argv(ps_bootstrap):
    run, cache, marker, trace = ps_bootstrap
    result = run()
    assert result.returncode == 0, result.stderr
    assert json.loads(marker.read_text(encoding="utf-8")) == [SECRET, *EXTRA]
    request = json.loads(trace.read_text(encoding="utf-8-sig"))
    assert request["uri"] == "https://releases.example/campus/0.1.0/campus-windows-amd64.exe"
    assert request["redirects"] == 0
    assert request["protected"] is True
    assert request["principals"] == [request["current"]]
    assert request["owner"] == request["current"]
    assert len(list(cache.glob("campus-windows-amd64-*.exe"))) == 1


def test_ps_checksum_mismatch_never_executes_download(ps_bootstrap):
    run, cache, marker, trace = ps_bootstrap
    assert run(sha="0" * 64).returncode != 0
    assert not marker.exists()
    assert list(cache.iterdir()) == []


@pytest.mark.parametrize("base", ["http://releases.example", "https://user:pass@example.com/path", "https://example.com/path?query=1", "https://example.com/path#fragment", "https:///no-host"])
def test_ps_rejects_untrusted_or_ambiguous_urls_before_download(ps_bootstrap, base):
    run, cache, marker, trace = ps_bootstrap
    assert run(base=base).returncode != 0
    assert not marker.exists()
    assert not trace.exists()


def test_ps_refuses_redirect_instead_of_executing_payload(ps_bootstrap):
    run, cache, marker, trace = ps_bootstrap
    assert run(environment={"BOOTSTRAP_HTTP_STATUS": "302"}).returncode != 0
    assert not marker.exists()
    assert list(cache.iterdir()) == []


def test_ps_rechecks_cached_binary_and_preserves_runner_exit_code(ps_bootstrap):
    run, cache, marker, trace = ps_bootstrap
    result = run(environment={"BOOTSTRAP_EXIT_CODE": "7"})
    assert result.returncode == 7, result.stderr
    marker.unlink()
    cached, = cache.glob("campus-*.exe")
    cached.write_bytes(b"tampered cached executable")
    assert run().returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize("sha", ["", "short", "g" * 64])
def test_ps_requires_a_trusted_sha_before_download(ps_bootstrap, sha):
    run, cache, marker, trace = ps_bootstrap
    assert run(sha=sha).returncode != 0
    assert not trace.exists()
    assert not marker.exists()


def test_ps_refuses_a_linked_cache(ps_bootstrap, tmp_path):
    run, cache, marker, trace = ps_bootstrap
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, cache, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    assert run().returncode != 0
    assert not trace.exists()
    assert list(outside.iterdir()) == []
