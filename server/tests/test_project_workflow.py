import io
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

from conftest import CAPS, open_harness
from sqlalchemy import select

from campus import dto
from campus import models as m
from campus.catalog import Catalog
from campus.db import now
from campus.main import plan_once, verify_once
from campus.planner.provider import ProjectReview, Proposal


def config():
    def stage(name, kind, dependencies, inputs):
        return {
            "id": name,
            "kind": kind,
            "depends_on": dependencies,
            "inputs": inputs,
            "command": ["python", "worker.py", name]
            + (["--input", "{input}", "--accelerator", "{accelerator}"] if kind == "map" else []),
            "outputs": [{"path": "result.txt", "max_bytes": 1024}],
            "accelerator": "cpu",
            "resources": {
                "cpu_millis": 1000,
                "memory_bytes": 128 << 20,
                "disk_bytes": 32 << 20,
                "vram_bytes": 0,
            },
            "timeout_seconds": 60,
        }

    return {
        "schema_version": "1",
        "name": "One global model",
        "data": ["dataset/*.txt"],
        "stages": [
            stage("fit", "map", [], ["dataset/train-*.txt"]),
            stage("model", "reduce", ["fit"], []),
            stage("evaluate", "map", ["model"], ["dataset/test-*.txt"]),
            stage("aggregate", "reduce", ["model", "evaluate"], []),
        ],
    }


def request(cp, project_config=None):
    project_config = project_config or config()
    archive = io.BytesIO()
    with ZipFile(archive, "w") as out:
        out.writestr("campus.json", json.dumps(project_config))
        out.writestr("worker.py", "# selected map/reduce source\nprint('worker')\n")
        out.writestr("private-notes.txt", "UNSELECTED_PRIVATE_CONTENT")
    project = cp.upload(archive.getvalue(), "project.zip", kind="project", part_size=4096)
    inputs = [{"artifact_id": project["id"], "mount_path": "/workspace", "archive": True}]
    for name in ("train-a", "train-b", "test-a", "test-b"):
        data = cp.upload(b"DATASET_CONTENT_NOT_FOR_LLM_" + name.encode(), name + ".txt", part_size=4096)
        inputs.append(
            {"artifact_id": data["id"], "mount_path": f"/inputs/dataset/{name}.txt", "archive": False}
        )
    return {
        "prompt": "Train one model and evaluate all independent shards.",
        "project_artifact_id": project["id"],
        "project_config": project_config,
        "inputs": inputs,
        "selected_files": ["worker.py"],
        "share_with_llm": True,
    }


def configure(cp):
    cp.runtime.catalog = Catalog(Path(__file__).parents[1] / "campus/data/product-catalog.json")
    cp.runtime.settings.lease_ms = 60_000


def preview(cp, body):
    value = cp.call("POST", "/job-previews", body=body, code=202)
    plan_once(cp.runtime)
    return cp.call("GET", "/job-previews/" + value["id"])


def native(cp):
    caps = deepcopy(CAPS)
    cpu = cp.runtime.catalog.environments["python-cpu-arm64"]
    caps.update(
        image_digests=[cpu.image_digest],
        offered_budget={
            "cpu_millis": 2000,
            "memory_bytes": 1 << 30,
            "disk_bytes": 1 << 30,
            "gpu_slots": 0,
            "vram_bytes": 0,
        },
    )
    device, _ = cp.device(caps)
    settings = cp.call("GET", "/device/settings", headers=device)
    cp.call(
        "POST",
        "/device/settings/ack",
        headers=device,
        body={"revision": settings["revision"], "mode": settings["mode"], "actual_state": "running"},
    )
    return {**device, "X-Campus-Worker-Protocol": "2"}


def test_project_review_compiles_one_dag_and_dependent_maps_run_on_disjoint_inputs(setup):
    received = []

    def provider(settings, context, budget):
        received.append(context)
        return Proposal(
            project_review=ProjectReview(
                approved=True, summary="Independent shards merge before test inference."
            )
        )

    for cp in open_harness(setup, planning_provider=provider):
        configure(cp)
        view = preview(cp, request(cp))
        assert view["state"] == "ready", view
        assert len(received) == 1 and received[0]["project_config"]["name"] == "One global model"
        assert "UNSELECTED_PRIVATE_CONTENT" not in str(received)
        assert "DATASET_CONTENT_NOT_FOR_LLM" not in str(received)
        assert received[0]["selected_files"] == {
            "worker.py": "# selected map/reduce source\nprint('worker')\n"
        }
        job = cp.call(
            "POST",
            "/jobs",
            body={"preview_id": view["id"], "plan_revision": view["plan_revision"]},
            headers={**cp.owner, "Idempotency-Key": str(uuid4())},
            code=201,
        )
        try:
            assert len(job["tasks"]) == 6 and job["total_units"] == 6
            first, second = native(cp), native(cp)
            a, b = cp.claim(first), cp.claim(second)
            assert a["stage_id"] == b["stage_id"] == "fit" and a["item_ids"] != b["item_ids"]
            for assignment in (a, b):
                data = [i["name"] for i in assignment["inputs"] if i["name"] != "project.zip"]
                assert len(data) == 1 and data[0].startswith("dataset/train-")
                assert "/inputs/" + data[0] in assignment["argv"]
                assert "{input}" not in assignment["argv"] and "cpu" in assignment["argv"]
            cp.complete(first, a)
            cp.claim(first, code=204)
            cp.complete(second, b)
            model = cp.claim(first)
            assert model["stage_id"] == "model"
            assert len([i for i in model["inputs"] if i["name"].startswith("stages/fit/")]) == 2
            cp.complete(first, model)
            a, b = cp.claim(first), cp.claim(second)
            assert a["stage_id"] == b["stage_id"] == "evaluate" and a["item_ids"] != b["item_ids"]
            for assignment in (a, b):
                assert (
                    len([i for i in assignment["inputs"] if i["name"].startswith("dataset/test-")]) == 1
                )
                assert (
                    len([i for i in assignment["inputs"] if i["name"].startswith("stages/model/")]) == 1
                )
                assert not any(i["name"].startswith("dataset/train-") for i in assignment["inputs"])
            cp.complete(first, a)
            cp.complete(second, b)
            final = cp.claim(first)
            assert final["stage_id"] == "aggregate"
            cp.complete(first, final)
            result = cp.call("GET", "/jobs/" + job["id"])
            assert result["state"] == "succeeded" and result["accepted_units"] == 6
            assert len({a["name"] for a in result["artifacts"]}) == 6
            assert sum(a["name"] == "result.txt" for a in result["artifacts"]) == 1
            assert len({t["node_id"] for t in result["tasks"]}) == 2
        finally:
            cp.call("POST", "/jobs/" + job["id"] + "/cancel")


def test_project_review_cache_is_bound_to_code_inputs_selection_and_provider(setup):
    calls = []

    def provider(settings, context, budget):
        calls.append(context)
        return Proposal(
            project_review=ProjectReview(approved=True, summary="Reviewed exact project contract.")
        )

    for cp in open_harness(setup, planning_provider=provider):
        configure(cp)
        body = request(cp)
        first = preview(cp, body)
        assert first["state"] == "ready"
        again = preview(cp, body)
        assert again["state"] == "ready" and len(calls) == 1
        changed = {**body, "selected_files": ["campus.json"]}
        assert preview(cp, changed)["state"] == "ready" and len(calls) == 2
        cp.runtime.settings.llm_model = "different-model"
        assert preview(cp, body)["state"] == "ready" and len(calls) == 3


def test_project_rejected_review_and_undeclared_inputs_fail_without_execution(setup):
    for cp in open_harness(
        setup,
        planning_provider=lambda *args: Proposal(
            project_review=ProjectReview(
                approved=False, summary="Unsupported shared mutable training state."
            )
        ),
    ):
        configure(cp)
        body = request(cp)
        rejected = preview(cp, body)
        assert rejected["state"] == "failed" and rejected["error_code"] == "PROJECT_REVIEW_REJECTED"
        altered = deepcopy(body)
        altered["inputs"][1]["mount_path"] = "/inputs/undeclared.txt"
        bad = preview(cp, altered)
        assert bad["state"] == "failed" and bad["error_code"] in {"INVALID_INPUT", "INVALID_PROJECT"}


def test_project_retry_keeps_accepted_shards_and_fences_late_completion(setup):
    for cp in open_harness(
        setup,
        planning_provider=lambda *args: Proposal(
            project_review=ProjectReview(approved=True, summary="Independent shard work.")
        ),
    ):
        configure(cp)
        view = preview(cp, request(cp))
        assert view["state"] == "ready"
        job = cp.call(
            "POST",
            "/jobs",
            body={"preview_id": view["id"], "plan_revision": view["plan_revision"]},
            headers={**cp.owner, "Idempotency-Key": str(uuid4())},
            code=201,
        )
        try:
            one, two = native(cp), native(cp)
            lost, accepted = cp.claim(one), cp.claim(two)
            accepted_result = cp.complete(two, accepted)
            with cp.runtime.sessions.begin() as db:
                attempt = db.get(m.Attempt, UUID(lost["attempt_id"]))
                attempt.lease_expires_at = attempt.reserved_until = now(db) - timedelta(seconds=1)
            cp.maintenance()
            with cp.runtime.sessions.begin() as db:
                task = db.get(m.Task, UUID(lost["task_id"]))
                task.available_at = now(db) - timedelta(seconds=1)
            retried = cp.claim(two)
            assert retried["task_id"] == lost["task_id"] and retried["generation"] == 2
            cp.attempt(
                one,
                lost,
                "complete",
                extra={
                    "manifest": {
                        "outputs": [
                            {"path": "result.txt", "artifact_id": accepted_result.artifact["id"]}
                        ],
                        "item_ids": lost["item_ids"],
                    }
                },
                code=409,
            )
            cp.complete(two, retried)
            result = cp.call("GET", "/jobs/" + job["id"])
            assert result["accepted_units"] == 2
            with cp.runtime.sessions() as db:
                assert (
                    len(
                        list(
                            db.scalars(
                                select(m.Acceptance).join(m.Task).where(m.Task.job_id == UUID(job["id"]))
                            )
                        )
                    )
                    == 2
                )
        finally:
            cp.call("POST", "/jobs/" + job["id"] + "/cancel")


def test_final_map_names_are_unique_and_long_valid_outputs_keep_valid_download_metadata(setup):
    for cp in open_harness(
        setup,
        planning_provider=lambda *args: Proposal(
            project_review=ProjectReview(approved=True, summary="Independent shard outputs.")
        ),
    ):
        configure(cp)
        value = config()
        value["stages"] = value["stages"][:1]
        value["stages"][0]["id"] = "f" * 64
        output = "a" * 220 + "/" + "b" * 220 + "/result.txt"
        value["stages"][0]["outputs"][0]["path"] = output
        view = preview(cp, request(cp, value))
        assert view["state"] == "ready"
        job = cp.call(
            "POST",
            "/jobs",
            body={"preview_id": view["id"], "plan_revision": view["plan_revision"]},
            headers={**cp.owner, "Idempotency-Key": str(uuid4())},
            code=201,
        )
        try:
            device = native(cp)
            for _ in range(2):
                assignment = cp.claim(device)
                artifact = cp.upload(
                    b"result",
                    output,
                    device=device,
                    assignment=assignment,
                    kind="output",
                    part_size=4096,
                )
                cp.attempt(
                    device,
                    assignment,
                    "complete",
                    extra={
                        "manifest": {
                            "outputs": [{"path": output, "artifact_id": artifact["id"]}],
                            "item_ids": assignment["item_ids"],
                        }
                    },
                    code=202,
                )
                verify_once(cp.runtime)
            result = cp.call("GET", "/jobs/" + job["id"])
            assert result["state"] == "succeeded"
            assert len({item["name"] for item in result["artifacts"]}) == 2
            for item in result["artifacts"]:
                assert item["name"].startswith("stages/")
                assert dto.Artifact.model_validate(item).id
        finally:
            cp.call("POST", "/jobs/" + job["id"] + "/cancel")


def test_auto_project_placement_uses_measured_throughput_instead_of_always_cuda(setup, monkeypatch):
    from campus.jobs import scheduler

    monkeypatch.setattr(
        scheduler,
        "observed_rate",
        lambda db, node, stage, variant: 2.0 if variant.accelerator == "cpu" else 0.5,
    )
    for cp in open_harness(
        setup,
        planning_provider=lambda *args: Proposal(
            project_review=ProjectReview(approved=True, summary="Independent CPU/CUDA shards.")
        ),
    ):
        configure(cp)
        value = config()
        value["stages"] = value["stages"][:1]
        value["stages"][0]["accelerator"] = "auto"
        value["stages"][0]["resources"]["vram_bytes"] = 512 << 20
        view = preview(cp, request(cp, value))
        assert view["state"] == "ready"
        job = cp.call(
            "POST",
            "/jobs",
            body={"preview_id": view["id"], "plan_revision": view["plan_revision"]},
            headers={**cp.owner, "Idempotency-Key": str(uuid4())},
            code=201,
        )
        try:
            caps = deepcopy(CAPS)
            caps.update(
                arch="amd64",
                image_digests=[
                    cp.runtime.catalog.environments[name].image_digest
                    for name in ("python-cpu-amd64", "python-cuda-amd64")
                ],
                gpus=[
                    {
                        "id": "0",
                        "vendor": "nvidia",
                        "vram_bytes": 2 << 30,
                        "driver": "test",
                        "cuda_verified": True,
                    }
                ],
                offered_budget={
                    "cpu_millis": 2000,
                    "memory_bytes": 1 << 30,
                    "disk_bytes": 1 << 30,
                    "gpu_slots": 1,
                    "vram_bytes": 2 << 30,
                },
            )
            device, _ = cp.device(caps)
            settings = cp.call("GET", "/device/settings", headers=device)
            cp.call(
                "POST",
                "/device/settings/ack",
                headers=device,
                body={
                    "revision": settings["revision"],
                    "mode": settings["mode"],
                    "actual_state": "running",
                },
            )
            work = cp.claim({**device, "X-Campus-Worker-Protocol": "2"})
            assert work["accelerator"] == "cpu" and "cpu" in work["argv"]
        finally:
            cp.call("POST", "/jobs/" + job["id"] + "/cancel")
