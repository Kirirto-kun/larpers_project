import json
from uuid import uuid4

from conftest import open_harness, sample_plan
from sqlalchemy import func, select

from campus import dto
from campus import models as m
from campus.main import plan_once, verify_once
from campus.planner.provider import Probe, Proposal


def test_probe_reads_bounded_sample_and_supplies_verified_evidence(setup):
    received = []

    def provider(settings, context, budget):
        received.append(context)
        if "probe" not in context:
            return Proposal(probe=Probe(adapter_id="test", adapter_version="1"))
        plan = sample_plan(manifest=context["input_manifest_digest"], policy="full")
        plan["evidence"] = [{"source": "probe", "detail": "Sample schema inspected"}]
        return Proposal(plan=dto.JobPlan.model_validate(plan))

    for cp in open_harness(setup, planning_provider=provider):
        adapter = cp.runtime.catalog.adapters[("test", "1")]
        adapter.probe_stage = dto.Stage.model_validate(sample_plan(policy="full")["stages"][0])
        sample = cp.upload(b"x,y\n1,2\n", "sample.csv")
        large = cp.upload(b"a" * 65537, "large.csv", part_size=65537)
        view = cp.call(
            "POST",
            "/job-previews",
            code=202,
            body={
                "prompt": "Inspect the dataset",
                "inputs": [
                    {"artifact_id": sample["id"], "mount_path": "/inputs/sample.csv"},
                    {"artifact_id": large["id"], "mount_path": "/inputs/large.csv"},
                ],
            },
        )
        plan_once(cp.runtime)
        worker, _ = cp.device()
        work = cp.claim(worker)
        assert [item["artifact_id"] for item in work["inputs"]] == [sample["id"]]
        cp.complete(worker, work, b'{"columns":["x","y"]}')
        plan_once(cp.runtime)
        latest = cp.call("GET", f"/job-previews/{view['id']}")
        assert latest["state"] == "ready", latest
        assert received[-1]["probe"]["outputs"] == {"result.txt": {"columns": ["x", "y"]}}
        assert received[-1]["probe"]["verification"] == "full"
        assert latest["plan"]["evidence"][-1]["source"] == "probe"
        with cp.runtime.sessions() as db:
            probe_tasks = select(m.Task.id).where(m.Task.job_id == work["job_id"])
            assert (
                db.scalar(
                    select(func.count()).select_from(m.Ledger).where(m.Ledger.task_id.in_(probe_tasks))
                )
                == 0
            )
        cp.call(
            "POST",
            "/jobs",
            code=201,
            headers={**cp.owner, "Idempotency-Key": str(uuid4())},
            body={
                "preview_id": view["id"],
                "plan_revision": 1,
            },
        )


def test_cancelling_preview_fences_probe(setup):
    for cp in open_harness(
        setup,
        planning_provider=lambda *args: Proposal(probe=Probe(adapter_id="test", adapter_version="1")),
    ):
        cp.runtime.catalog.adapters[("test", "1")].probe_stage = dto.Stage.model_validate(
            sample_plan(policy="full")["stages"][0]
        )
        view = cp.call("POST", "/job-previews", code=202, body={"prompt": "Inspect"})
        plan_once(cp.runtime)
        worker, _ = cp.device()
        work = cp.claim(worker)
        cp.call("POST", f"/job-previews/{view['id']}/cancel")
        cp.attempt(worker, work, "renew", code=409)
        cp.claim(worker, code=204)


def test_search_uses_accepted_index_and_separate_accounting(cp):
    plan = sample_plan(policy="full")
    plan["stages"][0]["parameters"] = {"model_digest": "a" * 64, "index_output": "result.txt"}
    search = dto.Stage.model_validate(sample_plan(policy="full")["stages"][0])
    search.outputs = [dto.Output(path="search.json", max_bytes=65536)]
    cp.runtime.catalog.adapters[("test", "1")].search_stage = search
    job = cp.job(plan=plan)
    worker, _ = cp.device()
    accepted = cp.complete(worker, cp.claim(worker))
    request = {"query": "Campus compute", "top_k": 1, "index_artifact_id": accepted.artifact["id"]}
    started = cp.call("POST", f"/jobs/{job['id']}/search", code=202, body=request)
    work = cp.claim(worker)
    assert work["job_id"] == started["search_job_id"]
    assert work["parameters"]["query"] == request["query"]
    assert work["inputs"][0]["artifact_id"] == accepted.artifact["id"]
    result = {
        "matches": [{"item_id": "1", "source": "paper.txt", "excerpt": "Campus compute", "score": 0.9}],
        "index_digest": accepted.artifact["sha256"],
        "model_digest": "a" * 64,
    }
    artifact = cp.upload(
        json.dumps(result).encode(),
        "search.json",
        device=worker,
        assignment=work,
        kind="output",
        part_size=4096,
    )
    cp.attempt(
        worker,
        work,
        "complete",
        code=202,
        extra={
            "manifest": {
                "item_ids": [],
                "outputs": [{"path": "search.json", "artifact_id": artifact["id"]}],
            }
        },
    )
    verify_once(cp.runtime)
    outcome = cp.call("GET", f"/jobs/{started['search_job_id']}")
    assert outcome["state"] == "succeeded", outcome
    assert outcome["purpose"] == "search"
    assert outcome["accepted_units"] == outcome["total_units"] == 0
    assert outcome["search_result"] == result
    assert cp.call("GET", f"/jobs/{job['id']}")["accepted_units"] == 1
    with cp.runtime.sessions() as db:
        search_tasks = select(m.Task.id).where(m.Task.job_id == started["search_job_id"])
        assert (
            db.scalar(
                select(func.count()).select_from(m.Ledger).where(m.Ledger.task_id.in_(search_tasks))
            )
            == 0
        )
