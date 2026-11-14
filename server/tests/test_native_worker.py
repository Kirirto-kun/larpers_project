from copy import deepcopy
from types import SimpleNamespace

import pytest
from conftest import CAPS, sample_plan

from campus import dto
from campus.native_worker import assignment, sandbox, supports


def fixture():
    plan = sample_plan(policy="structural")
    stage = plan["stages"][0]
    stage["variants"][0]["resource_requirements"]["disk_bytes"] = 8 << 20
    return dto.Stage.model_validate(stage)


def node():
    return SimpleNamespace(capabilities=deepcopy(CAPS))


def test_explicit_native_quotas_sum_to_reserved_disk():
    stage = fixture()
    limits = sandbox(stage, stage.variants[0], node())
    assert limits["pids"] == 128
    assert limits["cpu_millis"] == stage.variants[0].resource_requirements.cpu_millis
    assert (
        sum(limits[k] for k in ("workspace_bytes", "output_bytes", "checkpoint_bytes", "tmp_bytes"))
        == 8 << 20
    )
    assert limits["output_bytes"] >= sum(o.max_bytes for o in stage.outputs)
    assert limits["workspace_bytes"] <= 16 * limits["output_bytes"]
    assert limits["gpu_ids"] == []


def test_native_rejects_unrepresentable_quota_and_archive_mounts():
    stage = fixture()
    variant = stage.variants[0].model_copy(
        update={
            "resource_requirements": stage.variants[0].resource_requirements.model_copy(
                update={"disk_bytes": 1}
            )
        }
    )
    assert not supports(stage, variant, node())
    binding = dto.InputBinding(
        artifact_id="11111111-1111-4111-8111-111111111111", mount_path="/workspace/nested", archive=True
    )
    assert not supports(stage.model_copy(update={"bindings": [binding]}), stage.variants[0], node())


def test_native_gpu_requires_verified_selector_and_single_gpu_admission():
    stage = fixture()
    variant = stage.variants[0].model_copy(
        update={
            "accelerator": "cuda",
            "resource_requirements": stage.variants[0].resource_requirements.model_copy(
                update={"gpu_slots": 1, "vram_bytes": 1024}
            ),
        }
    )
    gpu_node = node()
    assert not supports(stage, variant, gpu_node)
    gpu_node.capabilities["gpus"] = [
        {"id": "1", "vendor": "nvidia", "cuda_verified": True, "vram_bytes": 2048}
    ]
    assert sandbox(stage, variant, gpu_node)["gpu_ids"] == ["1"]
    assert supports(stage, variant, gpu_node)
    assert not supports(stage, variant, gpu_node, gpu_busy=True)


@pytest.mark.parametrize("path", ["CON", "result.", "folder /model.json", "data/NUL.txt"])
def test_native_rejects_paths_the_windows_runner_cannot_materialize(path):
    stage = fixture()
    invalid = stage.model_copy(update={"outputs": [dto.Output(path=path, max_bytes=1024)]})
    assert not supports(invalid, stage.variants[0], node())


def test_native_rejects_case_aliases_and_ancestor_collisions():
    stage = fixture()
    for paths in [["Result.txt", "result.txt"], ["models", "models/weights.json"]]:
        invalid = stage.model_copy(
            update={"outputs": [dto.Output(path=p, max_bytes=1024) for p in paths]}
        )
        assert not supports(invalid, stage.variants[0], node())


def test_native_wire_flattens_exact_input_bindings_and_outputs():
    stage = fixture()
    value = {
        "inputs": [
            {
                "artifact_id": "input",
                "archive": False,
                "mount_path": "/inputs/data.csv",
                "artifact": {"id": "input", "name": "original.csv", "size_bytes": 5, "sha256": "a" * 64},
            }
        ],
        "outputs": [{"path": "model.json", "max_bytes": 1024, "required": True}],
        "resource_limits": {},
        "validator_policy": "structural",
        "resume_checkpoint": None,
    }
    result = assignment(value, stage, stage.variants[0], node(), remaining_ms=5000)
    assert result["inputs"][0]["name"] == "data.csv"
    assert result["inputs"][0]["id"] == "input"
    assert result["outputs"] == ["model.json"]
    assert result["task_remaining_ms"] == 5000
    assert result["validator_policy"] == {"mode": "structural"}
    assert value["inputs"][0]["artifact"]["name"] == "original.csv"
    value["inputs"][0].update(archive=True, mount_path="/workspace")
    assert (
        assignment(value, stage, stage.variants[0], node(), remaining_ms=5000)["inputs"][0]["name"]
        == "project.zip"
    )
    with pytest.raises(ValueError):
        assignment(value, stage, stage.variants[0], node(), remaining_ms=0)


def test_native_preserves_optional_output_semantics_without_guard_upgrade():
    stage = fixture()
    value = {
        "inputs": [],
        "outputs": [
            {"path": "model.json", "required": True},
            {"path": "optional.json", "required": False},
        ],
        "validator_policy": "structural",
    }
    result = assignment(value, stage, stage.variants[0], node(), remaining_ms=5000)
    assert result["outputs"] == ["model.json"]
    assert result["optional_outputs"] == ["optional.json"]


def test_native_header_gets_rich_wire_and_normal_completion(cp):
    stage = dto.Stage.model_validate(sample_plan(policy="structural")["stages"][0])
    plan = sample_plan(policy="structural", stages=[stage.model_dump(mode="json")])
    job = cp.job(plan=plan)
    caps = deepcopy(CAPS)
    device, _ = cp.device(caps)
    settings = cp.call("GET", "/device/settings", headers=device)
    cp.call(
        "POST",
        "/device/settings/ack",
        headers=device,
        body={"revision": settings["revision"], "mode": settings["mode"], "actual_state": "running"},
    )
    rich = cp.claim({**device, "X-Campus-Worker-Protocol": "2"})
    assert rich["job_id"] == job["id"]
    assert rich["task_remaining_ms"] > 0 and rich["resource_limits"]["pids"] == 128
    assert rich["outputs"] == [o.path for o in stage.outputs]
    assert isinstance(rich["validator_policy"], dict)
    assert all(
        v > 0 for k, v in rich["resource_limits"].items() if k.endswith("_bytes") and k != "vram_bytes"
    )
    cp.complete({**device, "X-Campus-Worker-Protocol": "2"}, rich)
    assert cp.call("GET", "/jobs/" + job["id"])["state"] == "succeeded"


def test_native_pending_settings_never_lease_new_work(cp):
    cp.job(policy="structural")
    device, _ = cp.device()
    native = {**device, "X-Campus-Worker-Protocol": "2"}
    cp.claim(native, 204)
    settings = cp.call("GET", "/device/settings", headers=device)
    cp.call(
        "POST",
        "/device/settings/ack",
        headers=device,
        body={"revision": settings["revision"], "mode": settings["mode"], "actual_state": "running"},
    )
    assert cp.claim(native)["task_remaining_ms"] > 0
