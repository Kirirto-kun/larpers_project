"""Exercise native quotas against the unchanged, pinned container guard."""

import hashlib
import importlib
from pathlib import Path

import pytest

import campus
from campus import dto
from campus.native_worker import input_path, resource_policy, validate_command


def stage(*, disk_bytes=1 << 20, memory_bytes=32 << 20, output_bytes=1024):
    return dto.Stage(
        stage_id="capture",
        adapter=dto.AdapterRef(id="campus.python", version="1"),
        variants=[
            dto.Variant(
                variant_id="cpu",
                image_digest="campus/test@sha256:" + "1" * 64,
                platform="linux/amd64",
                accelerator="cpu",
                resource_requirements=dto.Resources(
                    cpu_millis=1000,
                    memory_bytes=memory_bytes,
                    disk_bytes=disk_bytes,
                ),
            )
        ],
        argv=["python", "train.py"],
        working_dir="/workspace",
        outputs=[dto.Output(path="result.json", max_bytes=output_bytes)],
        task_deadline_ms=30_000,
        validation_policy="structural",
        resume_policy="restart",
    )


@pytest.fixture
def guard_files(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root))
    # The coordinator and SDK share the campus package name. Expose only the
    # SDK module needed by the real guard, retaining coordinator imports.
    monkeypatch.setattr(campus, "__path__", [*campus.__path__, str(root / "sdk/python/campus")])
    return importlib.import_module("environments.guard.files")


def test_capture_preserves_small_result_with_unchanged_64kib_project(tmp_path, guard_files):
    declared = stage()
    limits = resource_policy(declared, declared.variants[0])
    workspace, outputs = tmp_path / "workspace", tmp_path / "outputs"
    workspace.mkdir()
    outputs.mkdir()
    source = b"#" + b"x" * 65_534 + b"\n"
    assert len(source) == 64 << 10
    (workspace / "train.py").write_bytes(source)
    before = guard_files.scan(workspace, 10_000, limits["workspace_bytes"])
    (workspace / "result.json").write_bytes(b"{}")

    captured = guard_files.capture(
        workspace,
        outputs,
        tmp_path / "export",
        before,
        [output.path for output in declared.outputs],
        max_bytes=limits["output_bytes"],
    )

    assert captured["total_bytes"] == 2
    assert captured["files"] == [
        {
            "path": "workspace/result.json",
            "size_bytes": 2,
            "digest": hashlib.sha256(b"{}").hexdigest(),
        }
    ]
    assert (tmp_path / "export/workspace/result.json").read_bytes() == b"{}"
    assert not (tmp_path / "export/workspace/train.py").exists()
    assert (workspace / "train.py").read_bytes() == source
    assert (
        sum(limits[key] for key in ("workspace_bytes", "output_bytes", "checkpoint_bytes", "tmp_bytes"))
        == declared.variants[0].resource_requirements.disk_bytes
    )
    assert limits["workspace_bytes"] <= 16 * limits["output_bytes"]
    assert declared.outputs[0].max_bytes == 1024


@pytest.mark.parametrize(
    ("disk_bytes", "output_bytes"),
    [(1 << 20, 1024), (34 << 20, 1 << 20), (512 << 20, 16 << 20)],
)
def test_capture_envelope_stays_within_reserved_disk(disk_bytes, output_bytes):
    declared = stage(disk_bytes=disk_bytes, output_bytes=output_bytes)
    limits = resource_policy(declared, declared.variants[0])
    disk_keys = ("workspace_bytes", "output_bytes", "checkpoint_bytes", "tmp_bytes")
    assert all(limits[key] > 0 for key in disk_keys)
    assert sum(limits[key] for key in disk_keys) == disk_bytes
    assert limits["workspace_bytes"] <= 16 * limits["output_bytes"]
    assert limits["output_bytes"] >= output_bytes


@pytest.mark.parametrize("overrides", [{"disk_bytes": 1024}, {"memory_bytes": (32 << 20) - 1}])
def test_capture_policy_rejects_unrepresentable_resource_limits(overrides):
    declared = stage(**overrides)
    with pytest.raises(ValueError, match="sandbox quotas"):
        resource_policy(declared, declared.variants[0])


def test_native_command_limit_counts_utf8_bytes():
    declared = stage().model_copy(update={"argv": ["é" * 2048] * 16})
    assert sum(len(arg.encode("utf-8")) for arg in declared.argv) == 65_536
    validate_command(declared)
    oversized = declared.model_copy(update={"argv": [*declared.argv, "a"]})
    assert sum(len(arg) for arg in oversized.argv) < 65_536
    with pytest.raises(ValueError, match="65,536 bytes"):
        validate_command(oversized)


@pytest.mark.parametrize("filename", ["project.zip", "PROJECT.ZIP", "Project.Zip"])
def test_native_project_archive_name_is_reserved_case_insensitively(filename):
    binding = dto.InputBinding(
        artifact_id="11111111-1111-4111-8111-111111111111",
        mount_path="/inputs/" + filename,
    )
    with pytest.raises(ValueError, match="reserved for validated project archives"):
        input_path(binding)
    assert input_path(binding.model_copy(update={"archive": True, "mount_path": "/workspace"})) == (
        "project.zip"
    )
