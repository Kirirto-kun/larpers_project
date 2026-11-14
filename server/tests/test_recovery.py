import io
import json
from datetime import timedelta
from hashlib import sha256
from zipfile import ZipFile

from conftest import sample_plan
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_leases import expire

from campus import dto
from campus import models as m
from campus.db import now
from campus.jobs.leases import checkpoint_digest
from campus.main import create_app, verify_once


def test_restart_recovers_verifying_and_keeps_accepted_output(cp):
    job = cp.job()
    node, _ = cp.device()
    assignment = cp.claim(node)
    result = cp.complete(node, assignment, verify=False)
    restart = create_app(cp.runtime.settings, enable_background=False)
    with TestClient(restart, base_url=cp.runtime.settings.public_origin) as client:
        verify_once(restart.state.runtime)
        response = client.get(f"/api/v1/jobs/{job['id']}", headers=cp.owner)
        assert response.json()["state"] == "succeeded"
        assert (
            client.get(f"/api/v1/artifacts/{result.artifact['id']}/content", headers=cp.owner).content
            == b"computed"
        )


def test_verifier_timeout_does_not_reissue_compute(cp):
    job = cp.job(policy="full")
    node, _ = cp.device()
    assignment = cp.claim(node)
    cp.complete(node, assignment, verify=False)
    with cp.runtime.sessions.begin() as db:
        row = db.get(m.Attempt, assignment["attempt_id"])
        row.verification_deadline_at = now(db) - timedelta(seconds=1)
    verify_once(cp.runtime)
    result = cp.call("GET", f"/jobs/{job['id']}")
    assert result["state"] == "failed"
    assert result["tasks"][0]["error_code"] == "VERIFICATION_TIMEOUT"
    cp.claim(node, 204)


def test_checkpoint_only_resumes_published_compatible_blob(cp):
    plan = sample_plan()
    plan["stages"][0]["resume_policy"] = "checkpoint"
    cp.job(plan=plan)
    node, _ = cp.device()
    first = cp.claim(node)
    with cp.runtime.sessions() as db:
        job, task = db.get(m.Job, first["job_id"]), db.get(m.Task, first["task_id"])
        attempt = db.get(m.Attempt, first["attempt_id"])
        digest = checkpoint_digest(job, task, dto.Variant.model_validate(attempt.variant))
    state = b'{"step": 3}'
    archive = io.BytesIO()
    with ZipFile(archive, "w") as file:
        file.writestr("trainer-state.json", state)
        file.writestr(
            "campus-checkpoint.json",
            json.dumps(
                {
                    "compatibility_digest": digest,
                    "files": [
                        {
                            "path": "trainer-state.json",
                            "size_bytes": len(state),
                            "sha256": sha256(state).hexdigest(),
                        }
                    ],
                }
            ),
        )
    output = cp.upload(
        archive.getvalue(), "state.zip", kind="checkpoint", device=node, assignment=first, part_size=1024
    )
    body = {"artifact_id": output["id"], "compatibility_digest": digest, "sequence": 1}
    cp.attempt(node, first, "checkpoints", extra={**body, "artifact_id": first["task_id"]}, code=422)
    cp.attempt(node, first, "checkpoints", extra=body)
    cp.attempt(node, first, "checkpoints", extra={**body, "sequence": 2})
    cp.attempt(node, first, "checkpoints", extra=body)
    with cp.runtime.sessions() as db:
        latest = db.scalar(
            select(m.Checkpoint)
            .where(m.Checkpoint.attempt_id == first["attempt_id"])
            .order_by(m.Checkpoint.sequence.desc())
        )
        assert latest.sequence == 2
    expire(cp, first)
    cp.maintenance()
    second = cp.claim(node)
    assert second["resume_checkpoint"]["id"] == output["id"]
    assert (
        cp.call("GET", f"/artifacts/{output['id']}/content", headers=node).content == archive.getvalue()
    )
