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
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "sha256:" + "a" * 64


def generator():
    source = ROOT / "bootstrap/prepare_test_kit.py"
    assert source.exists(), "the offline readiness kit generator must exist"
    spec = importlib.util.spec_from_file_location("campus_prepare_kit", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def kit_inputs(tmp_path, monkeypatch):
    profile = {"id": "python-ml-cpu-v1-arm64", "accelerator": "cpu", "platform": "linux/arm64", "image_digest": IMAGE, "guard_version": "1"}
    profiles = tmp_path / "input profiles.json"
    profiles.write_text(json.dumps({"schema_version": "1", "profiles": [
        {**profile, "id": "other-amd64", "platform": "linux/amd64", "image_digest": "sha256:" + "b" * 64},
        {**profile, "private_cache": "/Users/builder/private/model", "credential": "must-not-travel"},
    ]}), encoding="utf-8")
    binary = tmp_path / "campus MachO"
    binary.write_bytes(struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0) + b"test ARM64 executable payload")
    archive = tmp_path / "export bytes"
    archive.write_bytes(bytes(range(256)) * 8192 + b"archive end")
    inspection = tmp_path / "inspect.json"
    inspection.write_text(json.dumps([{"Id": IMAGE, "Os": "linux", "Architecture": "arm64", "Config": {"Labels": {"io.campus.guard": "1", "io.campus.accelerator": "cpu"}}}]), encoding="utf-8")
    trace = tmp_path / "export-calls.jsonl"
    fake = tmp_path / "fake_export.py"
    fake.write_text('''import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with open(os.environ["CAMPUS_KIT_EXPORT_TRACE"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[:2] == ["image", "inspect"]:
    print(pathlib.Path(os.environ["CAMPUS_KIT_INSPECT"]).read_text())
elif args[:2] == ["image", "save"]:
    output = pathlib.Path(args[args.index("-o") + 1])
    if os.environ.get("CAMPUS_KIT_SAVE_FAIL"):
        output.write_bytes(b"partial export")
        sys.exit(9)
    shutil.copyfile(os.environ["CAMPUS_KIT_EXPORT_SOURCE"], output)
    if os.environ.get("CAMPUS_KIT_RACE_OUT"):
        pathlib.Path(os.environ["CAMPUS_KIT_RACE_OUT"]).mkdir()
else:
    sys.exit(8)
''', encoding="utf-8")
    monkeypatch.setenv("CAMPUS_KIT_EXPORT_TRACE", str(trace))
    monkeypatch.setenv("CAMPUS_KIT_INSPECT", str(inspection))
    monkeypatch.setenv("CAMPUS_KIT_EXPORT_SOURCE", str(archive))
    return {"profiles": profiles, "profile": profile, "binary": binary, "archive": archive,
            "inspection": inspection, "trace": trace, "docker": (sys.executable, str(fake)), "out": tmp_path / "Mac kit 空间"}


def make_kit(inputs):
    return generator().prepare_kit(inputs["profiles"], inputs["binary"], inputs["out"], docker=inputs["docker"])


def test_kit_exports_only_verified_arm_cpu_image_and_checksums_all_payloads(kit_inputs):
    result = make_kit(kit_inputs)
    out = kit_inputs["out"]
    assert result == out
    assert {path.name for path in out.iterdir()} == {"campus", "image.tar.gz", "profiles.json", "SHA256SUMS", "README.md", "check.sh"}
    assert (out / "campus").read_bytes() == kit_inputs["binary"].read_bytes()
    assert gzip.decompress((out / "image.tar.gz").read_bytes()) == kit_inputs["archive"].read_bytes()
    assert json.loads((out / "profiles.json").read_text()) == {"schema_version": "1", "profiles": [kit_inputs["profile"]]}
    lines = (out / "SHA256SUMS").read_text().splitlines()
    manifest = dict(line.split("  ", 1)[::-1] for line in lines)
    assert set(manifest) == {"campus", "image.tar.gz", "profiles.json", "README.md", "check.sh"}
    assert all(sha(out / name) == digest for name, digest in manifest.items())
    calls = [json.loads(line) for line in kit_inputs["trace"].read_text().splitlines()]
    assert calls[0] == ["image", "inspect", IMAGE]
    assert calls[1][:3] == ["image", "save", "-o"]
    assert calls[1][-1] == IMAGE
    assert len(calls) == 2
    assert not list(out.parent.glob(".campus-kit-*"))


@pytest.mark.parametrize("change", [{"accelerator": "cuda"}, {"guard_version": "0"}, {"image_digest": "campus/cpu:latest"}, {"platform": "linux/amd64"}])
def test_invalid_or_unresolved_arm_profile_fails_before_docker(kit_inputs, change):
    source = {"schema_version": "1", "profiles": [{**kit_inputs["profile"], **change}]}
    kit_inputs["profiles"].write_text(json.dumps(source))
    with pytest.raises(ValueError):
        make_kit(kit_inputs)
    assert not kit_inputs["trace"].exists()
    assert not kit_inputs["out"].exists()


def test_ambiguous_arm_cpu_profiles_are_rejected(kit_inputs):
    kit_inputs["profiles"].write_text(json.dumps({"schema_version": "1", "profiles": [kit_inputs["profile"], {**kit_inputs["profile"], "id": "another"}]}))
    with pytest.raises(ValueError):
        make_kit(kit_inputs)
    assert not kit_inputs["trace"].exists()


@pytest.mark.parametrize("change", [{"Architecture": "amd64"}, {"Os": "windows"}, {"Id": "sha256:" + "b" * 64}, {"Config": {"Labels": {"io.campus.guard": "0", "io.campus.accelerator": "cpu"}}}])
def test_actual_docker_identity_platform_and_guard_are_checked_before_export(kit_inputs, change):
    image, = json.loads(kit_inputs["inspection"].read_text())
    image.update(change)
    kit_inputs["inspection"].write_text(json.dumps([image]))
    with pytest.raises(ValueError):
        make_kit(kit_inputs)
    calls = [json.loads(line) for line in kit_inputs["trace"].read_text().splitlines()]
    assert calls == [["image", "inspect", IMAGE]]
    assert not kit_inputs["out"].exists()


@pytest.mark.parametrize("header", [b"not MachO", struct.pack("<8I", 0xFEEDFACF, 0x01000007, 0, 2, 0, 0, 0, 0), struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 6, 0, 0, 0, 0)])
def test_binary_must_be_a_native_arm64_macho_executable(kit_inputs, header):
    kit_inputs["binary"].write_bytes(header)
    with pytest.raises(ValueError):
        make_kit(kit_inputs)
    assert not kit_inputs["trace"].exists()
    assert not kit_inputs["out"].exists()


def test_failed_image_export_removes_private_staging_without_publishing(kit_inputs, monkeypatch):
    monkeypatch.setenv("CAMPUS_KIT_SAVE_FAIL", "1")
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        make_kit(kit_inputs)
    assert not kit_inputs["out"].exists()
    assert not list(kit_inputs["out"].parent.glob(".campus-kit-*"))


def test_existing_output_is_never_replaced_or_exported_into(kit_inputs):
    kit_inputs["out"].mkdir()
    marker = kit_inputs["out"] / "keep"
    marker.write_text("owner data")
    with pytest.raises(FileExistsError):
        make_kit(kit_inputs)
    assert marker.read_text() == "owner data"
    assert not kit_inputs["trace"].exists()


def test_concurrently_created_empty_output_is_preserved(kit_inputs, monkeypatch):
    monkeypatch.setenv("CAMPUS_KIT_RACE_OUT", str(kit_inputs["out"]))
    with pytest.raises(FileExistsError):
        make_kit(kit_inputs)
    assert list(kit_inputs["out"].iterdir()) == []
    assert not list(kit_inputs["out"].parent.glob(".campus-kit-*"))


def test_gzip_stream_is_bounded_and_preserves_bytes_without_host_filename():
    module = generator()

    class ChunkReader(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 65536, "kit compression must not read an unbounded image into memory"
            return super().read(size)

    data = bytes(range(256)) * 8192
    result = io.BytesIO()
    module.gzip_stream(ChunkReader(data), result)
    compressed = result.getvalue()
    assert gzip.decompress(compressed) == data
    assert compressed[3] & 8 == 0  # Gzip FNAME would leak the maker's filename.
    assert compressed[4:8] == b"\0\0\0\0"
    assert compressed[8] == 4  # Gzip XFL identifies the requested fastest level.


def test_probe_parent_enforces_timeout_without_host_cli_help():
    module = generator()
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-c", module.bounded_probe_code("import time; time.sleep(5)", seconds=0.1)], capture_output=True, text=True, timeout=4)
    assert result.returncode != 0
    assert "TimeoutExpired" in result.stderr
    assert time.monotonic() - started < 3


def test_probe_performs_actual_tiny_cpu_torch_calculation():
    # Match the thread limits passed to the real Docker probe before torch/
    # BLAS initialization, rather than inheriting an unrestricted host import.
    environment = dict(os.environ, OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    result = subprocess.run([sys.executable, "-c", generator().bounded_probe_code()], env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["check"] == "cpu_tiny_calculation"
    assert record["device"] == "cpu"
    assert record["result"] == [[7.0, 10.0], [15.0, 22.0]]


def posix(path):
    value = str(path).replace("\\", "/")
    return "/" + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ":" else value


@pytest.fixture
def checker(kit_inputs, monkeypatch, tmp_path):
    shell = str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe") if os.name == "nt" else shutil.which("sh")
    if not shell or not Path(shell).exists():
        pytest.skip("POSIX shell unavailable")

    def run(*, corrupt=False, system="Darwin", architecture="arm64", doctor_exit="0", records_link=None):
        make_kit(kit_inputs)
        out = kit_inputs["out"]
        (out / "campus").write_text('#!/bin/sh\nprintf \'%s\\0\' "$@" > "$CAMPUS_KIT_CAMPUS_TRACE"\nprintf \'{"os":"darwin","arch":"arm64"}\\n\'\nexit "$CAMPUS_KIT_DOCTOR_EXIT"\n', encoding="utf-8", newline="\n")
        manifest = dict(line.split("  ", 1)[::-1] for line in (out / "SHA256SUMS").read_text().splitlines())
        manifest["campus"] = sha(out / "campus")
        (out / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in manifest.items()), newline="\n")
        if corrupt:
            (out / "campus").write_bytes(b"corrupted in transit")
        if records_link is not None:
            outside = tmp_path / "outside-records"
            outside.mkdir()
            protected = outside / "doctor.json"
            protected.write_text("owner data")
            records = out / "readiness-records"
            try:
                if records_link == "directory":
                    os.symlink(outside, records, target_is_directory=True)
                else:
                    records.mkdir()
                    os.symlink(protected, records / "doctor.json")
            except OSError as error:
                pytest.skip(f"symlink creation unavailable: {error}")
        commands = tmp_path / "mock host commands"
        commands.mkdir()
        (commands / "uname").write_text('#!/bin/sh\ncase "$1" in -s) printf \'%s\\n\' "$CAMPUS_KIT_TEST_OS";; -m) printf \'%s\\n\' "$CAMPUS_KIT_TEST_ARCH";; *) exit 1;; esac\n', encoding="utf-8", newline="\n")
        (commands / "docker").write_text('''#!/bin/sh
printf '%s\\0' "$@" >> "$CAMPUS_KIT_DOCKER_TRACE"
printf '\\0' >> "$CAMPUS_KIT_DOCKER_TRACE"
case "$1" in
    load) printf 'Loaded verified image\\n' ;;
    image) printf '%s linux/arm64 1 cpu\\n' "$CAMPUS_KIT_EXPECTED_IMAGE" ;;
    run) printf '{"check":"cpu_tiny_calculation","device":"cpu","result":[[7,10],[15,22]]}\\n' ;;
    rm) exit 0 ;;
    *) exit 8 ;;
esac
''', encoding="utf-8", newline="\n")
        for command in commands.iterdir():
            command.chmod(0o700)
        campus_trace = tmp_path / "campus-argv.bin"
        docker_trace = tmp_path / "docker-argv.bin"
        env = dict(os.environ, CAMPUS_KIT_CAMPUS_TRACE=posix(campus_trace), CAMPUS_KIT_DOCKER_TRACE=posix(docker_trace), CAMPUS_KIT_EXPECTED_IMAGE=IMAGE,
                   CAMPUS_KIT_TEST_OS=system, CAMPUS_KIT_TEST_ARCH=architecture, CAMPUS_KIT_DOCTOR_EXIT=doctor_exit)
        result = subprocess.run([shell, "-c", 'PATH="$1:/usr/bin:/usr/bin/core_perl:/bin"; export PATH; shift; exec sh "$@"', "kit-test", posix(commands), posix(out / "check.sh")],
                                env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        calls = [] if not docker_trace.exists() else [[value.decode("utf-8") for value in raw.split(b"\0")] for raw in docker_trace.read_bytes().split(b"\0\0") if raw]
        return result, campus_trace, calls, out

    return run


def test_checker_executes_literal_native_argv_and_bounded_sandbox(checker):
    result, campus_trace, calls, out = checker()
    assert result.returncode == 0, result.stderr
    assert campus_trace.read_bytes().decode("utf-8").split("\0")[:-1] == ["doctor", "--profiles", "./profiles.json"]
    assert calls[0] == ["load", "--input", "./image.tar.gz"]
    assert [call[0] for call in calls] == ["load", "image", "run"]
    run, = [call for call in calls if call[0] == "run"]
    assert {"--pull=never", "--platform=linux/arm64", "--network=none", "--read-only", "--user=65532:65532", "--cap-drop=ALL", "--pids-limit=64", "--memory=1073741824", "--memory-swap=1073741824", "--cpus=1", "--restart=no", "--security-opt=no-new-privileges:true"} <= set(run)
    assert run[run.index("--entrypoint") + 1] == "/opt/campus/.venv/bin/python"
    assert run[-3:-1] == [IMAGE, "-c"]
    assert not {"--privileged", "--volume", "--mount", "-v"} & set(run)
    assert json.loads((out / "readiness-records/cpu-check.json").read_text())["device"] == "cpu"


def test_checker_checksum_failure_prevents_load_or_execution(checker):
    result, campus_trace, calls, out = checker(corrupt=True)
    assert result.returncode != 0
    assert not campus_trace.exists()
    assert calls == []


@pytest.mark.parametrize("system,architecture", [("Linux", "arm64"), ("Darwin", "x86_64")])
def test_checker_refuses_other_platforms_before_runtime_commands(checker, system, architecture):
    result, campus_trace, calls, out = checker(system=system, architecture=architecture)
    assert result.returncode != 0
    assert not campus_trace.exists()
    assert calls == []


def test_checker_failed_doctor_stops_before_torch_probe(checker):
    result, campus_trace, calls, out = checker(doctor_exit="1")
    assert result.returncode != 0
    assert campus_trace.exists()
    assert not [call for call in calls if call[0] == "run"]


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_checker_refuses_linked_record_paths_without_overwriting_owner_files(checker, kit_inputs, kind):
    result, campus_trace, calls, out = checker(records_link=kind)
    assert result.returncode != 0
    assert (out.parent / "outside-records/doctor.json").read_text() == "owner data"
    assert not campus_trace.exists()
    assert calls == []
