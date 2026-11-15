"""Connection package boundaries: real files/archives and native launchers."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def builder():
    source = ROOT / "bootstrap/prepare_connection_kit.py"
    assert source.exists(), "the verified connection package builder must exist"
    spec = importlib.util.spec_from_file_location("campus_connection_builder", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(value).hexdigest()


def binary(target="darwin-arm64", *, metadata_target=None):
    goos, arch = target.split("-")
    if goos == "darwin":
        header = struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0)
    else:
        header = bytearray(256)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 60, 128)
        header[128:132] = b"PE\0\0"
        struct.pack_into("<H", header, 132, 0x8664)
        struct.pack_into("<H", header, 150, 0x22)
        struct.pack_into("<H", header, 152, 0x20B)
        header = bytes(header)
    goos, arch = (metadata_target or target).split("-")
    info = ("path\tcampus/runner/cmd/campus\nbuild\tGOOS=" + goos +
            "\nbuild\tGOARCH=" + arch + "\nbuild\tCGO_ENABLED=0\n").encode()
    module = b"x" * 16 + info + b"y" * 16

    def varint(n):
        result = bytearray()
        while n >= 128:
            result.append((n & 127) | 128)
            n >>= 7
        return bytes(result + bytes([n]))

    return header + b"\0" * ((16 - len(header) % 16) % 16) + b"\xff Go buildinf:" + bytes([8, 2]) + b"\0" * 16 + b"\x08go1.25.6" + varint(len(module)) + module


def image_archive(arch="arm64", accelerator="cpu", *, wrong_index=False):
    data = json.dumps({"kind": "image", "arch": arch, "accelerator": accelerator}).encode()
    image_id = "sha256:" + digest(data)
    descriptor_id = "sha256:" + ("0" * 64 if wrong_index else digest(data))
    index = json.dumps({"schemaVersion": 2, "manifests": [{"digest": descriptor_id, "size": len(data),
                        "annotations": {"io.containerd.image.name": f"campus/{accelerator}:ready"}}]}).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, value in [("index.json", index), ("blobs/sha256/" + digest(data), data)]:
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            archive.addfile(entry, io.BytesIO(value))
    return image_id, stream.getvalue()


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    image_id, archive = image_archive()
    image = tmp_path / "image.tar"
    image.write_bytes(archive)
    native = tmp_path / "native binary"
    native.write_bytes(binary())
    profile = {"id": "cpu-arm", "accelerator": "cpu", "platform": "linux/arm64", "image_digest": image_id, "guard_version": "1"}
    profiles = tmp_path / "source profiles.json"
    profiles.write_text(json.dumps({"schema_version": "1", "profiles": [{**profile, "secret": "must-not-travel"}]}))
    inspection = tmp_path / "inspect.json"
    inspection.write_text(json.dumps([{"Id": image_id, "Os": "linux", "Architecture": "arm64",
        "RepoTags": ["campus/cpu:ready"], "RepoDigests": ["campus/cpu@" + image_id],
        "Config": {"Labels": {"io.campus.guard": "1", "io.campus.accelerator": "cpu"}}}]))
    trace = tmp_path / "docker.jsonl"
    external = tmp_path / "docker_boundary.py"
    external.write_text('''import json, os, pathlib, sys
args = sys.argv[1:]
trace = pathlib.Path(os.environ['CONNECTION_TRACE'])
with trace.open('a') as stream: stream.write(json.dumps(args) + '\\n')
if args[:2] == ['image', 'inspect']:
    value = json.loads(pathlib.Path(os.environ['CONNECTION_INSPECT']).read_text())
    if isinstance(value, dict): value = value[args[-1]]
    if os.environ.get('CONNECTION_CHANGED_TAG') and len(trace.read_text().splitlines()) > 2:
        value[0]['Id'] = 'sha256:' + '0' * 64
    if os.environ.get('CONNECTION_MUTATE_BINARY'):
        pathlib.Path(os.environ['CONNECTION_MUTATE_BINARY']).write_bytes(b'changed binary after validation')
    print(json.dumps(value))
elif args[:2] == ['image', 'save']:
    mapping = json.loads(os.environ.get('CONNECTION_ARCHIVES', '{}'))
    sys.stdout.buffer.write(pathlib.Path(mapping.get(args[-1], os.environ['CONNECTION_ARCHIVE'])).read_bytes())
    if os.environ.get('CONNECTION_SAVE_FAIL'): sys.exit(9)
    if os.environ.get('CONNECTION_RACE_OUT'): pathlib.Path(os.environ['CONNECTION_RACE_OUT']).mkdir()
else: sys.exit(7)
''')
    monkeypatch.setenv("CONNECTION_TRACE", str(trace))
    monkeypatch.setenv("CONNECTION_INSPECT", str(inspection))
    monkeypatch.setenv("CONNECTION_ARCHIVE", str(image))
    return {"profiles": profiles, "profile": profile, "binary": native, "out": tmp_path / "Campus kit 空间",
            "inspection": inspection, "archive": image, "docker": (sys.executable, str(external)), "trace": trace}


def make(inputs, **kwargs):
    return builder().prepare_kit(inputs["profiles"], inputs["binary"], inputs["out"], target="darwin-arm64", docker=inputs["docker"], **kwargs)


def test_builder_creates_pinned_manifest_and_named_image_without_private_metadata(inputs):
    out = make(inputs)
    manifest = json.loads((out / "connection.json").read_text())
    assert manifest["schema_version"] == "1"
    assert (manifest["os"], manifest["arch"]) == ("darwin", "arm64")
    for ref in [manifest["runner"], manifest["profiles"], *manifest["images"]]:
        payload = (out / ref["path"]).read_bytes()
        assert ref["sha256"] == digest(payload)
        assert ref["size_bytes"] == len(payload)
    assert manifest["runner"]["path"] == "campus"
    assert manifest["images"][0]["path"] == "images/cpu.tar.gz"
    assert manifest["images"][0]["image_digest"] == inputs["profile"]["image_digest"]
    assert gzip.decompress((out / "images/cpu.tar.gz").read_bytes()) == inputs["archive"].read_bytes()
    assert "must-not-travel" not in (out / "profiles.json").read_text()
    assert "invite_file" not in manifest and "ca_file" not in manifest
    assert not list(out.rglob("*.tar")), "large uncompressed Docker output must never be staged"
    assert not list(out.parent.glob(".campus-connect-*"))
    calls = [json.loads(line) for line in inputs["trace"].read_text().splitlines()]
    assert ["image", "save", "campus/cpu:ready"] in calls


@pytest.mark.parametrize("payload", [b"not native", binary(metadata_target="windows-amd64"), binary()[:-32]])
def test_wrong_native_header_or_go_build_info_fails_before_docker(inputs, payload):
    inputs["binary"].write_bytes(payload)
    with pytest.raises(ValueError):
        make(inputs)
    assert not inputs["out"].exists()
    assert not inputs["trace"].exists()


@pytest.mark.parametrize("change", [{"Architecture": "amd64"}, {"Id": "sha256:" + "0" * 64},
    {"Config": {"Labels": {"io.campus.guard": "0", "io.campus.accelerator": "cpu"}}}])
def test_wrong_cached_image_identity_platform_or_guard_never_publishes(inputs, change):
    record, = json.loads(inputs["inspection"].read_text())
    record.update(change)
    inputs["inspection"].write_text(json.dumps([record]))
    with pytest.raises(ValueError):
        make(inputs)
    assert not inputs["out"].exists()


def test_exported_index_must_match_inspected_image(inputs):
    _, archive = image_archive(wrong_index=True)
    inputs["archive"].write_bytes(archive)
    with pytest.raises(ValueError):
        make(inputs)
    assert not inputs["out"].exists()


@pytest.mark.parametrize("environment", ["CONNECTION_SAVE_FAIL", "CONNECTION_CHANGED_TAG"])
def test_export_failure_or_tag_race_never_publishes_and_removes_staging(inputs, monkeypatch, environment):
    monkeypatch.setenv(environment, "1")
    with pytest.raises((ValueError, RuntimeError)):
        make(inputs)
    assert not inputs["out"].exists()
    assert not list(inputs["out"].parent.glob(".campus-connect-*"))


def test_existing_and_concurrently_created_output_are_preserved(inputs, monkeypatch):
    monkeypatch.setenv("CONNECTION_RACE_OUT", str(inputs["out"]))
    with pytest.raises(FileExistsError):
        make(inputs)
    assert inputs["out"].is_dir() and not list(inputs["out"].iterdir())
    with pytest.raises(FileExistsError):
        make(inputs)


def test_private_ca_invite_and_endpoint_are_explicit_and_hashed(inputs, tmp_path):
    invite = tmp_path / "invite.txt"
    invite.write_text("https://pool.example/join#token=private-secret\n")
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nexplicit-local-ca\n-----END CERTIFICATE-----\n")
    out = make(inputs, ca_file=ca, invite_file=invite, endpoint="https://pool.example")
    manifest = json.loads((out / "connection.json").read_text())
    assert manifest["endpoint"] == "https://pool.example"
    assert (out / manifest["invite_file"]["path"]).read_bytes() == invite.read_bytes()
    assert manifest["invite_file"]["sha256"] == digest(invite.read_bytes())
    assert manifest["ca_file"]["sha256"] == digest(ca.read_bytes())
    assert "private-secret" not in (out / "Connect.command").read_text()
    assert "private-secret" not in (out / "README.txt").read_text()
    if os.name == "nt":
        result = subprocess.run(["icacls", str(out)], capture_output=True, text=True, check=True)
        assert "(I)" not in result.stdout


def posix(path):
    value = str(path).replace("\\", "/")
    return "/" + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ":" else value


def test_mac_launcher_verifies_both_pins_preserves_literal_argv_and_exit(tmp_path):
    shell = Path("C:/Program Files/Git/bin/sh.exe") if os.name == "nt" else Path(shutil.which("sh") or "missing")
    if not shell.exists(): pytest.skip("POSIX shell unavailable")
    out = tmp_path / "Campus kit 空间"
    out.mkdir()
    executable = out / "campus"
    executable.write_text('#!/bin/sh\nprintf \'%s\\0\' "$@" > "$CONNECTION_ARGS"\nexit 7\n', newline="\n")
    executable.chmod(0o700)
    manifest = out / "connection.json"
    manifest.write_text("{}\n")
    builder().write_launchers(out, "darwin", digest(executable.read_bytes()), digest(manifest.read_bytes()))
    marker = tmp_path / "args.bin"
    env = {**os.environ, "CONNECTION_ARGS": posix(marker), "PATH": "/usr/bin/core_perl:/usr/bin:/bin:" + os.environ.get("PATH", "")}
    result = subprocess.run([str(shell), posix(out / "Connect.command"), "--check-only", "literal 空间;$(touch unsafe)"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 7, result.stderr
    args = marker.read_bytes().decode().split("\0")[:-1]
    assert args == ["connect", "--bundle", posix(out), "--manifest-sha256", digest(manifest.read_bytes()), "--check-only", "literal 空间;$(touch unsafe)"]
    marker.unlink()
    manifest.write_text("tampered")
    assert subprocess.run([str(shell), posix(out / "Connect.command")], env=env, capture_output=True, timeout=15).returncode != 0
    assert not marker.exists()


def test_streaming_compression_enforces_bounded_reads_and_output_limit():
    class Reader(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 65536
            return super().read(size)
    module = builder()
    result = io.BytesIO()
    data = bytes(range(256)) * 4096
    module.gzip_stream(Reader(data), result, limit=1 << 20)
    assert gzip.decompress(result.getvalue()) == data
    with pytest.raises(ValueError):
        module.gzip_stream(Reader(data), io.BytesIO(), limit=100)


def test_binary_changed_during_image_inspection_never_publishes(inputs, monkeypatch):
    inputs["binary"].write_bytes(binary() + b"\0" * 65536)
    monkeypatch.setenv("CONNECTION_MUTATE_BINARY", str(inputs["binary"]))
    with pytest.raises(ValueError):
        make(inputs)
    assert not inputs["out"].exists()


def test_windows_package_contains_native_cpu_and_optional_cuda_with_matching_hashes(inputs, tmp_path, monkeypatch):
    profiles, records, sources = [], {}, {}
    for kind in ("cpu", "cuda"):
        image_id, archive = image_archive("amd64", kind)
        tag = f"campus/{kind}:ready"
        reference = "campus/" + kind + "@" + image_id
        profile = {"id": kind + "-amd64", "accelerator": kind, "platform": "linux/amd64", "image_digest": reference, "guard_version": "1"}
        profiles.append(profile)
        record = [{"Id": image_id, "Os": "linux", "Architecture": "amd64", "RepoTags": [tag], "RepoDigests": [reference],
                   "Config": {"Labels": {"io.campus.guard": "1", "io.campus.accelerator": kind}}}]
        records[reference] = record
        records[tag] = record
        source = tmp_path / (kind + ".tar")
        source.write_bytes(archive)
        sources[tag] = str(source)
    inputs["profiles"].write_text(json.dumps({"schema_version": "1", "profiles": profiles}))
    inputs["binary"].write_bytes(binary("windows-amd64"))
    inputs["inspection"].write_text(json.dumps(records))
    monkeypatch.setenv("CONNECTION_ARCHIVES", json.dumps(sources))
    out = builder().prepare_kit(inputs["profiles"], inputs["binary"], inputs["out"], target="windows-amd64", docker=inputs["docker"])
    manifest = json.loads((out / "connection.json").read_text())
    assert (manifest["os"], manifest["arch"], manifest["runner"]["path"]) == ("windows", "amd64", "campus.exe")
    assert {image["accelerator"] for image in manifest["images"]} == {"cpu", "cuda"}
    for image in manifest["images"]:
        assert digest((out / image["path"]).read_bytes()) == image["sha256"]
    assert (out / "Connect.cmd").exists() and (out / "Connect.ps1").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows launchers require Windows")
@pytest.mark.parametrize("entry", ["Connect.cmd", "Connect.ps1"])
@pytest.mark.parametrize("exit_code", [0, 7, 130])
def test_windows_launcher_verifies_pins_runs_native_process_and_preserves_exit(tmp_path, entry, exit_code):
    out = tmp_path / "Campus kit 空间 & !"
    out.mkdir()
    executable = out / "campus.exe"
    shutil.copyfile(Path(sys.base_prefix) / "python.exe", executable)
    manifest = out / "connection.json"
    manifest.write_text("{}\n")
    builder().write_launchers(out, "windows", digest(executable.read_bytes()), digest(manifest.read_bytes()))
    marker = tmp_path / "arguments.json"
    (tmp_path / "connect").write_text("import json,os,sys\nfrom pathlib import Path\nPath(os.environ['CONNECTION_ARGS']).write_text(json.dumps(sys.argv[1:],ensure_ascii=False),encoding='utf-8')\nsys.exit(" + str(exit_code) + ")\n")
    env = {**os.environ, "CONNECTION_ARGS": str(marker), "PYTHONHOME": sys.base_prefix, "PYTHONNOUSERSITE": "1",
           "PSModulePath": str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/Modules"),
           "PATH": sys.base_prefix + os.pathsep + os.environ.get("PATH", "")}
    command = 'cmd.exe /d /s /c ""' + str(out / entry) + '""' if entry.endswith(".cmd") else ["powershell.exe", "-NoLogo", "-NoProfile", "-File", str(out / entry), "--check-only", "literal 空间;$(touch unsafe)"]
    result = subprocess.run(command, env=env, cwd=tmp_path, input="", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert ("Press Enter" in result.stdout) == (entry.endswith(".cmd") and exit_code not in {0, 130})
    args = json.loads(marker.read_text(encoding="utf-8"))
    assert args[:4] == ["--bundle", str(out), "--manifest-sha256", digest(manifest.read_bytes())]
    assert args[4:] == ([] if entry.endswith(".cmd") else ["--check-only", "literal 空间;$(touch unsafe)"])
    marker.unlink()
    executable.write_bytes(b"tampered native process")
    failed = subprocess.run(command, env=env, cwd=tmp_path, input=b"", capture_output=True, timeout=30)
    assert failed.returncode != 0
    assert (b"Press Enter" in failed.stdout) == entry.endswith(".cmd")
    assert not marker.exists()


def test_no_named_reference_exports_id_without_inventing_canonical_metadata(inputs):
    record, = json.loads(inputs["inspection"].read_text())
    record["RepoTags"], record["RepoDigests"] = [], []
    inputs["inspection"].write_text(json.dumps([record]))
    out = make(inputs)
    manifest = json.loads((out / "connection.json").read_text())
    assert manifest["images"][0]["image_digest"] == record["Id"]
    calls = [json.loads(line) for line in inputs["trace"].read_text().splitlines()]
    assert ["image", "save", record["Id"]] in calls


@pytest.mark.parametrize("payload", [b"line one\nline two\n", b"\xff", b"\r\n", b"token\x00secret"])
def test_invalid_private_invitation_fails_before_docker_and_is_not_printed(inputs, tmp_path, payload):
    invitation = tmp_path / "private invite.txt"
    invitation.write_bytes(payload)
    with pytest.raises(ValueError):
        make(inputs, invite_file=invitation)
    assert not inputs["trace"].exists()
    assert not inputs["out"].exists()
