from datetime import timedelta

import pytest
from conftest import CAPS, sample_plan
from sqlalchemy import select

from campus import models as m
from campus.db import digest, now


def test_incompatible_hardware_waits(cp):
    job = cp.job()
    device, _ = cp.device({**CAPS, "image_digests": []})
    cp.maintenance()
    assert cp.call("GET", f"/jobs/{job['id']}")["state"] == "waiting_for_capacity"
    cp.claim(device, 204)


def test_map_keeps_items_and_counts_unique_units(cp):
    plan = sample_plan(policy="full", kind="map")
    job = cp.job(plan=plan)
    first, _ = cp.device()
    second, _ = cp.device()
    assignments = [cp.claim(first), cp.claim(second)]
    assert assignments[0]["item_ids"] != assignments[1]["item_ids"]
    cp.complete(first, assignments[0])
    cp.complete(second, assignments[1])
    cp.complete(first, cp.claim(first))
    result = cp.call("GET", f"/jobs/{job['id']}")
    assert result["state"] == "succeeded"
    assert result["accepted_units"] == result["total_units"] == 3
    assert len({t["node_id"] for t in result["tasks"]}) == 2


def test_dag_waits_for_accepted_predecessor(cp):
    first = sample_plan()["stages"][0]
    second = dict(first, stage_id="final", depends_on=["execute"])
    job = cp.job(plan=sample_plan(kind="dag", stages=[first, second]))
    node, _ = cp.device()
    other, _ = cp.device()
    assigned = cp.claim(node)
    cp.complete(node, assigned)
    second_assignment = cp.claim(other)
    assert second_assignment["stage_id"] == "execute"
    cp.complete(other, second_assignment)
    third_assignment = cp.claim(node)
    assert third_assignment["stage_id"] == "execute"
    results = cp.complete(node, third_assignment)
    following = cp.claim(other)
    assert following["stage_id"] == "final"
    assert len(following["inputs"]) == 3
    assert results.artifact["id"] in {item["artifact"]["id"] for item in following["inputs"]}
    cp.complete(other, following)
    assert cp.call("GET", f"/jobs/{job['id']}")["state"] == "succeeded"


@pytest.mark.parametrize("aged", [False, True])
def test_cache_preference_preserves_aged_work(cp, aged):
    artifacts, jobs = [], []
    for content in (b"first input", b"cached input"):
        artifact = cp.upload(content, "data.txt")
        artifacts.append(artifact)
        binding = {"artifact_id": artifact["id"], "mount_path": "/inputs/data.txt", "archive": False}
        plan = sample_plan(
            manifest=digest(
                {
                    "project_digest": None,
                    "inputs": [
                        {**binding, "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"]}
                    ],
                }
            )
        )
        plan["stages"][0]["bindings"] = [binding]
        jobs.append(cp.job(plan=plan, fields={"inputs": [binding]}))
    if aged:
        with cp.runtime.sessions.begin() as db:
            first = db.scalar(select(m.Task).where(m.Task.job_id == jobs[0]["id"]))
            first.created_at = now(db) - timedelta(seconds=31)
    worker, _ = cp.device(
        {
            **CAPS,
            "cached_artifact_digests": [artifacts[1]["sha256"]],
            "transfer_bytes_per_second": 1_000_000,
        }
    )
    work = cp.claim(worker)
    assert work["job_id"] == jobs[0 if aged else 1]["id"]
    # A cache hint never grants access to inputs of the unassigned job.
    other = artifacts[1 if aged else 0]
    cp.call("GET", f"/artifacts/{other['id']}/content", headers=worker, code=403)
