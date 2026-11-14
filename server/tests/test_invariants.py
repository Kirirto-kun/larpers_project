import io
import json
from datetime import timedelta
from uuid import uuid4
from zipfile import ZipFile

import pytest
from conftest import checksum, sample_plan
from sqlalchemy import select
from test_leases import expire

from campus import models as m
from campus.auth import issue_credential
from campus.db import now
from campus.events import emit
from campus.jobs import verification
from campus.main import plan_once, verify_once
from campus.planner import service as planner


def member(cp, *, other_pool=False):
    with cp.runtime.sessions.begin() as db:
        pool_id = cp.call("GET", "/auth/me")["pool_id"]
        if other_pool:
            pool = m.Pool(name="Separate pool")
            db.add(pool)
            db.flush()
            pool_id = pool.id
        user = m.User(pool_id=pool_id, role="owner" if other_pool else "member", name="Other user")
        db.add(user)
        db.flush()
        _, token, _ = issue_credential(db, user, "cli")
    return {"Authorization": "Bearer " + token}


def test_cancelled_reservation_survives_reaper_until_guard_expires(cp):
    job = cp.job()
    worker, _ = cp.device()
    assignment = cp.claim(worker)
    cp.call("POST", f"/jobs/{job['id']}/cancel")
    cp.maintenance()
    cp.job()
    cp.claim(worker, code=204)
    expire(cp, assignment)
    assert cp.claim(worker)["job_id"] != job["id"]


def test_drain_keeps_renew_and_stop_fences_old_session(cp):
    cp.job()
    worker, session = cp.device()
    assignment = cp.claim(worker)
    cp.call("POST", f"/nodes/{session['node_id']}/control", body={"action": "drain"})
    cp.attempt(worker, assignment, "renew")
    cp.claim(worker, code=204)
    cp.call("POST", f"/nodes/{session['node_id']}/control", body={"action": "stop"})
    assert cp.attempt(worker, assignment, "renew", code=409)["code"] == "SESSION_REPLACED"


def test_preview_and_artifacts_do_not_cross_pools(cp):
    preview = cp.preview()
    job = cp.job()
    artifact = cp.upload(b"private", "private.txt")
    outsider = member(cp, other_pool=True)
    for path in (
        f"/job-previews/{preview['id']}",
        f"/job-previews/{preview['id']}/events",
        f"/jobs/{job['id']}",
        f"/jobs/{job['id']}/logs",
        f"/artifacts/{artifact['id']}/content",
    ):
        cp.call("GET", path, headers=outsider, code=404)


def test_fair_queue_serves_next_owner(cp):
    for _ in range(4):
        cp.job()
    first_owner = cp.owner
    cp.owner = member(cp)
    waiting = cp.job()
    cp.owner = first_owner
    worker, _ = cp.device()
    cp.complete(worker, cp.claim(worker))
    assert cp.claim(worker)["job_id"] == waiting["id"]


def test_unsupported_resume_never_restarts(cp):
    plan = sample_plan()
    plan["stages"][0]["resume_policy"] = "unsupported"
    job = cp.job(plan=plan)
    worker, _ = cp.device()
    assignment = cp.claim(worker)
    expire(cp, assignment)
    cp.maintenance()
    assert cp.call("GET", f"/jobs/{job['id']}")["state"] == "failed"
    cp.claim(worker, code=204)


def test_retry_budget_is_finite_and_oom_keeps_item_ids(cp):
    job = cp.job(plan=sample_plan(kind="map"))
    worker, _ = cp.device()
    initial = cp.claim(worker)
    task_id, item_ids = initial["task_id"], initial["item_ids"]
    for generation in range(1, 4):
        assignment = initial if generation == 1 else cp.claim(worker)
        # Other first-generation tasks can run before the retry.
        while assignment["task_id"] != task_id:
            cp.complete(worker, assignment)
            assignment = cp.claim(worker)
        assert assignment["generation"] == generation
        assert assignment["item_ids"] == item_ids
        cp.attempt(worker, assignment, "fail", extra={"code": "OOM", "retryable": True})
    for _ in range(2):
        response = cp.client.post("/api/v1/work/next", headers=worker, json={"wait_ms": 0})
        if response.status_code == 200:
            assert response.json()["task_id"] != task_id
            cp.complete(worker, response.json())
    cp.maintenance()
    assert cp.call("GET", f"/jobs/{job['id']}")["state"] == "partial"


def test_partial_checkpoint_cannot_replace_previous_pointer(cp):
    plan = sample_plan()
    plan["stages"][0]["resume_policy"] = "checkpoint"
    cp.job(plan=plan)
    worker, _ = cp.device()
    assignment = cp.claim(worker)
    archive = io.BytesIO()
    with ZipFile(archive, "w") as file:
        file.writestr(
            "campus-checkpoint.json",
            json.dumps(
                {
                    "compatibility_digest": assignment["checkpoint_compatibility_digest"],
                    "files": [{"path": "trainer-state.json", "size_bytes": 10, "sha256": "0" * 64}],
                }
            ),
        )
    assert (
        cp.upload(
            archive.getvalue(),
            "state.zip",
            kind="checkpoint",
            assignment=assignment,
            device=worker,
            part_size=1024,
            code=422,
        )["code"]
        == "INVALID_CHECKPOINT"
    )
    with cp.runtime.sessions() as db:
        assert (
            db.scalar(select(m.Checkpoint.id).where(m.Checkpoint.task_id == assignment["task_id"]))
            is None
        )


def test_validator_crash_reclaims_verification_without_compute(cp):
    cp.job(policy="full")
    worker, _ = cp.device()
    assignment = cp.claim(worker)
    cp.complete(worker, assignment, verify=False)
    with cp.runtime.sessions.begin() as db:
        crashed = verification.claim(db, cp.runtime)
        attempt = db.get(m.Attempt, assignment["attempt_id"])
        attempt.verification_claim_expires_at = now(db) - timedelta(seconds=1)
    cp.claim(worker, code=204)
    verify_once(cp.runtime)
    with cp.runtime.sessions.begin() as db:
        verification.accept_result(db, cp.runtime, crashed, report={"level": "full", "accepted": True})
        assert db.get(m.Task, assignment["task_id"]).generation == 1


def test_crash_after_blob_publication_is_replayable(cp, monkeypatch):
    from campus.artifacts import uploads

    data = b"durable"
    row = cp.call(
        "POST",
        "/uploads",
        body={"name": "input.txt", "size_bytes": len(data), "sha256": checksum(data)},
        code=201,
    )
    path = f"/uploads/{row['id']}"
    cp.call(
        "PUT", path + "/parts/0", content=data, headers={**cp.owner, "X-Content-SHA256": checksum(data)}
    )
    real = uploads.register_artifact

    def crash(*args):
        raise RuntimeError("simulated crash after file publication")

    monkeypatch.setattr(uploads, "register_artifact", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        cp.call("POST", path + "/finalize")
    assert cp.call("GET", path)["artifact"] is None
    monkeypatch.setattr(uploads, "register_artifact", real)
    artifact = cp.call("POST", path + "/finalize")
    assert cp.call("GET", f"/artifacts/{artifact['id']}/content").content == data


def test_old_event_cursor_requires_snapshot(cp):
    cp.runtime.settings.event_replay_limit = 10
    job = cp.job()
    with cp.runtime.sessions.begin() as db:
        entity = db.get(m.Job, job["id"])
        for i in range(15):
            emit(db, entity.pool_id, "job", entity.id, "test", index=i)
    page = cp.call("GET", f"/jobs/{job['id']}/events?cursor=1")
    assert page["reset"] is True and page["events"] == []
    assert cp.call("GET", f"/jobs/{job['id']}/events?cursor={page['cursor']}")["events"] == []


def test_planner_crash_reclaims_and_fences_old_completion(cp):
    preview = cp.call("POST", "/job-previews", body={"prepared_plan": sample_plan()}, code=202)
    with cp.runtime.sessions.begin() as db:
        original = planner.claim(db)
        db.get(m.Preview, preview["id"]).claim_expires_at = now(db) - timedelta(seconds=1)
    plan_once(cp.runtime)
    with cp.runtime.sessions.begin() as db:
        from campus.errors import Problem

        planner.finish(db, cp.runtime, original, error=Problem("OLD_ERROR", "late error"))
    assert cp.call("GET", f"/job-previews/{preview['id']}")["state"] == "ready"


@pytest.mark.parametrize("value", ["bad\x00name", "\ud800"])
def test_invalid_text_returns_validation_error(cp, value):
    cp.call(
        "POST",
        "/invitations",
        content=json.dumps({"kind": "device", "name": value}).encode(),
        headers={**cp.owner, "Content-Type": "application/json"},
        code=422,
    )


def test_adaptive_batches_preserve_coverage(cp):
    cp.runtime.catalog.adapters[("test", "1")].adaptive_batches = True
    cp.runtime.catalog.adapters[("test", "1")].initial_batch_units = 1
    view = cp.preview(plan=sample_plan(kind="map", policy="full"))
    with cp.runtime.sessions.begin() as db:
        preview = db.get(m.Preview, view["id"])
        preview.prepared = {"execute": [{"item_ids": ["a", "b", "c"], "units": 3, "parameters": {}}]}
    job = cp.call(
        "POST",
        "/jobs",
        body={"preview_id": view["id"], "plan_revision": 1},
        headers={**cp.owner, "Idempotency-Key": str(uuid4())},
        code=201,
    )
    worker, _ = cp.device()
    other, _ = cp.device()
    seen = []
    for current in (worker, other, worker):
        assignment = cp.claim(current)
        seen.extend(assignment["item_ids"])
        cp.complete(current, assignment)
    assert sorted(seen) == ["a", "b", "c"]
    result = cp.call("GET", f"/jobs/{job['id']}")
    assert result["accepted_units"] == result["total_units"] == 3


def test_adaptive_batch_uses_previous_accepted_duration(cp):
    adapter = cp.runtime.catalog.adapters[("test", "1")]
    adapter.adaptive_batches = True
    adapter.initial_batch_units = 1
    view = cp.preview(plan=sample_plan(kind="map", policy="full"))
    with cp.runtime.sessions.begin() as db:
        db.get(m.Preview, view["id"]).prepared = {
            "execute": [{"item_ids": list("abcdef"), "units": 6, "parameters": {}}]
        }
    cp.call(
        "POST",
        "/jobs",
        body={"preview_id": view["id"], "plan_revision": 1},
        headers={**cp.owner, "Idempotency-Key": str(uuid4())},
        code=201,
    )
    worker, _ = cp.device()
    first = cp.claim(worker)
    assert first["item_ids"] == ["a"]
    cp.attempt(worker, first, "started")
    with cp.runtime.sessions.begin() as db:
        db.get(m.Attempt, first["attempt_id"]).started_at = now(db) - timedelta(seconds=4)
    cp.complete(worker, first)
    second = cp.claim(worker)
    assert second["item_ids"] == ["b", "c"]
