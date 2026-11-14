import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

TRUSTED_ROOT = Path(__file__).resolve().parents[2]


def make_wheel(directory, name="campus-tiny-local", version="1.0.0", *, tag="py3-none-any", requires=()):
    normalized = name.replace("-", "_")
    filename = f"{normalized}-{version}-{tag}.whl"
    dist = f"{normalized}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: >=3.11\n"
    metadata += "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
    files = {f"{normalized}/__init__.py": 'VALUE = "offline \\u2713"\n'.encode(),
             f"{dist}/METADATA": metadata.encode(),
             f"{dist}/WHEEL": f"Wheel-Version: 1.0\nGenerator: campus-test\nRoot-Is-Purelib: true\nTag: {tag}\n".encode()}
    records = []
    for path, raw in files.items():
        encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        records.append(f"{path},sha256={encoded},{len(raw)}")
    files[f"{dist}/RECORD"] = ("\n".join([*records, f"{dist}/RECORD,,"]) + "\n").encode()
    path = directory / filename
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as wheel:
        for key, value in files.items():
            wheel.writestr(key, value)
    return {"filename": filename, "name": name, "version": version, "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "tags": [tag]}


def make_house(path, *, tag="py3-none-any", requires=()):
    path.mkdir()
    wheel = make_wheel(path, tag=tag, requires=requires)
    manifest = {"schema_version": "1", "kind": "campus-wheelhouse", "platform": "manylinux_2_28_x86_64",
                "python_version": "3.11", "requirements": ["campus-tiny-local==1.0.0"], "files": [wheel]}
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def run_project(root, house, argv):
    code = '''
import sys
sys.path.insert(0, sys.argv[1])
from environments.offline import prepare_and_exec
prepare_and_exec(sys.argv[4:], root=sys.argv[2], wheelhouse=sys.argv[3])
'''
    env = dict(os.environ)
    env["PIP_INDEX_URL"] = "https://must-not-be-used.invalid/simple"
    env["PIP_EXTRA_INDEX_URL"] = "https://must-not-be-used.invalid/extra"
    return subprocess.run([sys.executable, "-I", "-c", code, str(TRUSTED_ROOT), str(root), str(house), *argv],
                          env=env, text=True, encoding="utf-8", capture_output=True, timeout=120)


def test_profile_satisfied_executes_original_argv_and_never_imports_backend(tmp_path):
    root = tmp_path / "用户 project"
    root.mkdir()
    (root / "pyproject.toml").write_text('[build-system]\nrequires=[]\nbuild-backend="evil"\n'
        '[project]\nname="test"\nrequires-python=">=3.11,<3.12"\ndependencies=["packaging==26.3"]\n')
    (root / "evil.py").write_text('raise RuntimeError("backend must never be imported")')
    (root / "capture.py").write_text('import sys,json; print(json.dumps({"argv":sys.argv[1:],"python":sys.executable}))')
    result = run_project(root, tmp_path / "not-needed", [sys.executable, "capture.py", "引号 ' \" $()", "a b"])
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["argv"] == ["引号 ' \" $()", "a b"]
    assert output["python"] == sys.executable
    assert not (root / ".campus-env").exists()


def test_missing_package_installs_actual_wheel_offline_and_preserves_unicode_argv(tmp_path):
    root = tmp_path / "缺失 selected workspace"
    root.mkdir()
    (root / "requirements.txt").write_text("campus-tiny-local==1.0.0\npackaging==26.3\n")
    (root / "run.py").write_text('import campus_tiny_local,json,sys,packaging; '
        'print(json.dumps({"value":campus_tiny_local.VALUE,"argv":sys.argv[1:],"python":sys.executable,"base":packaging.__version__}))')
    house = tmp_path / "prepared wheels"
    make_house(house)
    result = run_project(root, house, ["python", "run.py", "多语言 参数", "$(never-shell)"])
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["value"] == "offline ✓" and output["base"] == "26.3"
    assert output["argv"] == ["多语言 参数", "$(never-shell)"]
    assert Path(output["python"]).is_relative_to(root / ".campus-env")
    assert (root / ".campus-env").is_dir()


@pytest.mark.parametrize("text", ["-r ../outside.txt", "-e .", "thing @ https://example.com/a.whl",
    "git+https://example.com/repo.git", "--index-url https://example.com", "../package", "thing @ file:///tmp/pkg"])
def test_malicious_or_external_requirement_is_rejected_before_exec(tmp_path, text):
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text(text)
    marker = root / "executed"
    result = run_project(root, tmp_path / "unused", [sys.executable, "-c", f"open({str(marker)!r},'w').write('bad')"])
    assert result.returncode != 0
    assert "prepared profile" in result.stderr.lower() or "wheelhouse" in result.stderr.lower()
    assert not marker.exists() and not (root / ".campus-env").exists()


@pytest.mark.parametrize("metadata", [
    '[project]\nname="x"\ndynamic=["dependencies"]\n',
    '[project]\nname="x"\nrequires-python=">=4"\ndependencies=[]\n',
    '[project]\nname="x"\ndependencies="run arbitrary code"\n'])
def test_dynamic_or_incompatible_project_metadata_is_rejected(tmp_path, metadata):
    from environments.offline import read_project_requirements
    (tmp_path / "pyproject.toml").write_text(metadata)
    with pytest.raises(ValueError, match="prepared profile|static|dependencies|requires-python"):
        read_project_requirements(tmp_path)


@pytest.mark.parametrize("damage", ["missing", "checksum", "tag", "transitive-url", "unlisted"])
def test_bad_or_missing_wheelhouse_never_executes_program(tmp_path, damage):
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text("campus-tiny-local==1.0.0")
    house = tmp_path / "house"
    make_house(house, tag="cp311-cp311-manylinux_2_28_aarch64" if damage == "tag" else "py3-none-any",
               requires=["hidden @ https://example.com/hidden.whl"] if damage == "transitive-url" else [])
    if damage == "missing":
        (house / "manifest.json").unlink()
    if damage == "checksum":
        next(house.glob("*.whl")).write_bytes(b"tampered")
    if damage == "unlisted":
        (house / "extra.whl").write_bytes(b"unlocked")
    marker = root / "executed"
    result = run_project(root, house, ["python", "-c", f"open({str(marker)!r},'w').write('bad')"])
    assert result.returncode != 0 and not marker.exists()
    assert "wheelhouse" in result.stderr.lower()


def test_pyproject_requirement_markers_and_compatible_profile_need_no_overlay(tmp_path):
    from environments.offline import read_project_requirements
    (tmp_path / "requirements.txt").write_text('packaging>=25 # normal comment\nnot-a-real-package; python_version < "2"\n')
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["packaging<27"]\nrequires-python=">=3.11"\n')
    requirements = read_project_requirements(tmp_path)
    assert [str(r) for r in requirements] == ['packaging>=25', 'packaging<27']


def test_metadata_symlink_and_oversized_file_are_rejected(tmp_path):
    from environments.offline import read_project_requirements
    outside = tmp_path / "outside.txt"
    outside.write_text("packaging==26.3")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "requirements.txt").symlink_to(outside)
    except OSError:
        pytest.skip("test account cannot create symlinks")
    with pytest.raises(ValueError, match="symlink|regular"):
        read_project_requirements(root)
    (root / "requirements.txt").unlink()
    (root / "requirements.txt").write_bytes(b"x" * ((1 << 20) + 1))
    with pytest.raises(ValueError, match="quota|large|limit"):
        read_project_requirements(root)


def test_runtime_pip_bootstrap_blocks_actual_network_attempt(tmp_path):
    from environments.offline import _OFFLINE_PIP_BOOTSTRAP
    result = subprocess.run([sys.executable, "-I", "-c", _OFFLINE_PIP_BOOTSTRAP, "index", "versions", "campus-test-no-network",
                             "--index-url", "http://127.0.0.1:9/simple", "--trusted-host", "127.0.0.1", "--retries", "0"],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "network disabled" in result.stderr.lower()


def test_prepare_wheelhouse_rejects_unpinned_input_before_download(tmp_path):
    requirement = tmp_path / "requirements.txt"
    requirement.write_text("campus-tiny-local>=1")
    result = subprocess.run([sys.executable, "-m", "environments.prepare_wheelhouse", "--requirements", str(requirement),
        "--to", str(tmp_path / "new-house"), "--platform", "manylinux_2_28_x86_64", "--python-version", "311"],
        text=True, capture_output=True, timeout=20)
    assert result.returncode != 0 and "exact" in result.stderr.lower()
    assert not (tmp_path / "new-house").exists()


def test_wheelhouse_manifest_generation_records_actual_safe_wheel_identity(tmp_path):
    from environments.prepare_wheelhouse import build_manifest
    wheel = make_wheel(tmp_path)
    manifest = build_manifest(tmp_path, platform="manylinux_2_28_x86_64", requirements=["campus-tiny-local==1.0.0"])
    assert manifest["files"] == [wheel]
    assert manifest["python_version"] == "3.11" and manifest["kind"] == "campus-wheelhouse"


def test_incompatible_profile_version_is_overlaid_without_changing_base(tmp_path):
    import importlib.metadata
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text("packaging==27.0")
    (root / "run.py").write_text('import packaging,json,importlib.metadata; '
        'print(json.dumps({"value":packaging.VALUE,"version":importlib.metadata.version("packaging")}))')
    house = tmp_path / "house"
    house.mkdir()
    wheel = make_wheel(house, name="packaging", version="27.0")
    (house / "manifest.json").write_text(json.dumps({"schema_version": "1", "kind": "campus-wheelhouse",
        "platform": "manylinux_2_28_x86_64", "python_version": "3.11", "requirements": ["packaging==27.0"], "files": [wheel]}))
    result = run_project(root, house, ["python", "run.py"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"value": "offline ✓", "version": "27.0"}
    assert importlib.metadata.version("packaging") == "26.3"


def test_real_offline_install_resolves_transitive_local_wheel(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text("campus-tiny-local==1.0.0")
    (root / "run.py").write_text('import campus_tiny_local,campus_child,json; print(json.dumps([campus_tiny_local.VALUE,campus_child.VALUE]))')
    house = tmp_path / "house"
    manifest = make_house(house, requires=["campus-child==1.0.0"])
    manifest["files"].append(make_wheel(house, name="campus-child"))
    (house / "manifest.json").write_text(json.dumps(manifest))
    result = run_project(root, house, ["python", "run.py"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["offline ✓", "offline ✓"]


def test_dependency_subprocess_timeout_is_enforced(tmp_path):
    from environments.offline import _run_bounded
    with pytest.raises(ValueError, match="time limit"):
        _run_bounded([sys.executable, "-c", "import time; time.sleep(5)"], env=os.environ.copy(),
                     timeout=0.05, log_directory=tmp_path)


def test_existing_overlay_is_not_executed_or_removed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text("campus-tiny-local==1.0.0")
    overlay = root / ".campus-env"
    overlay.mkdir()
    (overlay / "do-not-touch").write_text("original")
    house = tmp_path / "house"
    make_house(house)
    result = run_project(root, house, ["python", "-c", "raise Exception('must not execute')"])
    assert result.returncode != 0 and "fresh extracted workspace" in result.stderr
    assert (overlay / "do-not-touch").read_text() == "original"


def test_explicit_online_preparer_publishes_only_verified_binary_download(tmp_path, monkeypatch):
    from environments import prepare_wheelhouse
    from environments.offline import verify_wheelhouse
    requirement = tmp_path / "requirements.txt"
    requirement.write_text("campus-tiny-local==1.0.0")
    destination = tmp_path / "immutable-house"
    def download_local_fixture(command, *, env, check, timeout):
        # Network is replaced only at the explicit download boundary. The real
        # wheel bytes, archive inspection, manifest, rename and verification run.
        assert command[:5] == [sys.executable, "-I", "-m", "pip", "download"]
        assert "--only-binary=:all:" in command and "--no-cache-dir" in command
        assert command[command.index("--platform") + 1] == "manylinux_2_28_x86_64"
        assert command[command.index("--python-version") + 1] == "311"
        assert command[-1] == "campus-tiny-local==1.0.0" and timeout == 300 and check
        assert "PIP_EXTRA_INDEX_URL" not in env
        make_wheel(Path(command[command.index("--dest") + 1]))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(prepare_wheelhouse.subprocess, "run", download_local_fixture)
    assert prepare_wheelhouse.main(["--requirements", str(requirement), "--to", str(destination),
        "--platform", "manylinux_2_28_x86_64", "--python-version", "311"]) == 0
    records = verify_wheelhouse(destination)
    assert records[0][0]["name"] == "campus-tiny-local"
    assert not list(tmp_path.glob(".campus-wheelhouse-*"))
    assert prepare_wheelhouse.main(["--requirements", str(requirement), "--to", str(destination),
        "--platform", "manylinux_2_28_x86_64"]) == 1
