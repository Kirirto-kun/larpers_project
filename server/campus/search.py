from datetime import timedelta

from sqlalchemy import select

from . import dto
from . import models as m
from .db import digest, now, write_lock
from .errors import Problem
from .jobs.service import create_job, owned_job, stage_for
from .planner.validation import validate_variant


def start_search(db, app, actor, job_id, body):
    write_lock(db)
    source = owned_job(db, actor, job_id)
    user = db.get(m.User, source.owner_id)
    row = db.get(m.Artifact, body.index_artifact_id)
    if not row or not row.attempt_id:
        raise Problem("ARTIFACT_NOT_READY", "The index must be an accepted output", 409)
    result = db.get(m.Attempt, row.attempt_id)
    task = db.get(m.Task, result.task_id)
    accepted = db.get(m.Acceptance, task.id)
    if (
        source.finished_at is None
        or source.cancelled_at
        or task.job_id != source.id
        or not accepted
        or accepted.attempt_id != result.id
        or str(row.id) not in {o["artifact_id"] for o in accepted.result["outputs"]}
    ):
        raise Problem("ARTIFACT_NOT_READY", "The index must be an accepted output of this job", 409)
    origin = stage_for(source, task)
    adapter = app.catalog.adapter(origin.adapter)
    stage = adapter.search_stage
    model_digest = origin.parameters.get("model_digest")
    index_path = origin.parameters.get("index_output")
    if not stage or not model_digest or row.name != index_path or stage.adapter != adapter.ref:
        raise Problem(
            "UNSUPPORTED_SEARCH", "The accepted output is not a registered searchable index", 422
        )
    if (
        stage.depends_on
        or stage.validation_policy not in adapter.policies
        or stage.validation_policy == "structural"
    ):
        raise Problem("UNSUPPORTED_SEARCH", "The search adapter is not configured for validation", 422)
    for variant in stage.variants:
        validate_variant(variant, [app.catalog.environments[k] for k in adapter.image_ids], None)
    active = db.scalars(
        select(m.Job.id)
        .where(
            m.Job.owner_id == user.id,
            m.Job.purpose == "search",
            m.Job.finished_at.is_(None),
        )
        .limit(app.settings.max_active_jobs_per_owner)
    ).all()
    if len(active) >= app.settings.max_active_jobs_per_owner:
        raise Problem("QUOTA_EXCEEDED", "Too many active searches", 429, retryable=True)
    stage = stage.model_copy(
        update={
            "bindings": [dto.InputBinding(artifact_id=row.id, mount_path="/inputs/index")],
            "parameters": {
                **origin.parameters,
                "query": body.query,
                "top_k": body.top_k,
                "model_digest": model_digest,
                "index_digest": row.sha256,
            },
        }
    )
    plan = dto.JobPlan(
        plan_revision=1,
        input_manifest_digest=digest({"index": row.sha256, "query": body.query}),
        execution_kind="single",
        stages=[stage],
        job_deadline_at=now(db) + timedelta(minutes=15),
        retry_policy=dto.RetryPolicy(),
    )
    return create_job(
        db,
        app,
        user,
        plan,
        {stage.stage_id: [{"item_ids": [], "units": 0, "parameters": {}}]},
        purpose="search",
    )
