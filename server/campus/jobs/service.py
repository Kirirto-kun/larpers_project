from datetime import timedelta

from sqlalchemy import func, select

from .. import dto
from .. import models as m
from ..db import digest, now, payload, write_lock
from ..errors import Problem, conflict, missing
from ..events import emit

TERMINAL = {"succeeded", "failed", "cancelled"}
COMPUTE = {"leased", "running"}
RUNNABLE = {"queued", "waiting_for_capacity", "retry_wait"}


def owned_job(db, actor, job_id):
    job = db.get(m.Job, job_id)
    if not job:
        raise missing()
    actor.owns(job.owner_id, job.pool_id)
    return job


def owned_preview(db, actor, preview_id):
    preview = db.get(m.Preview, preview_id)
    if not preview:
        raise missing()
    actor.owns(preview.owner_id)
    return preview


def artifact_dto(row):
    return dto.Artifact.model_validate(row, from_attributes=True)


def current_plan(preview, revision):
    if preview.state != "ready" or preview.revision != revision:
        raise Problem("STALE_PREVIEW", "Refresh and review the current ready plan", 409)
    return dto.JobPlan.model_validate(preview.plan)


def start_job(db, app, actor, body, key):
    user = actor.member()
    write_lock(db)
    previous = db.scalar(select(m.Job).where(m.Job.owner_id == user.id, m.Job.idempotency_key == key))
    request_digest = digest(body)
    if previous:
        if previous.request_digest != request_digest:
            raise conflict("Idempotency key has already been used with a different request")
        return previous
    preview = owned_preview(db, actor, body.preview_id)
    if preview.owner_id != user.id:
        raise missing()
    plan = current_plan(preview, body.plan_revision)
    if digest(plan) != preview.plan_digest:
        raise Problem("INVALID_PLAN", "The stored plan digest no longer matches its revision", 409)
    from ..planner.validation import validate_plan

    validate_plan(
        db,
        app,
        user,
        plan,
        dto.PreviewRequest.model_validate(preview.request),
        server_evidence=preview.probe_job_id is not None,
    )
    active = db.scalar(
        select(func.count())
        .select_from(m.Job)
        .where(
            m.Job.owner_id == user.id,
            m.Job.finished_at.is_(None),
            m.Job.purpose == "work",
        )
    )
    if active >= app.settings.max_active_jobs_per_owner:
        raise Problem("QUOTA_EXCEEDED", "Too many active jobs", 429, retryable=True)
    return create_job(
        db,
        app,
        user,
        plan,
        preview.prepared,
        preview_id=preview.id,
        key=key,
        request_digest=request_digest,
    )


def create_job(
    db,
    app,
    user,
    plan,
    prepared,
    *,
    purpose="work",
    preview_id=None,
    key=None,
    request_digest=None,
    node_ids=None,
    concurrency=None,
):
    current = now(db)
    if plan.job_deadline_at <= current:
        raise Problem("PLAN_EXPIRED", "The job deadline has passed", 409)
    job = m.Job(
        pool_id=user.pool_id,
        owner_id=user.id,
        preview_id=preview_id,
        idempotency_key=key,
        request_digest=request_digest,
        manifest_digest=digest(plan),
        plan=payload(plan),
        purpose=purpose,
        deadline_at=plan.job_deadline_at,
        node_ids=node_ids,
        concurrency=concurrency,
    )
    db.add(job)
    db.flush()
    count = sum(len(specs) for specs in prepared.values())
    if not 0 < count <= app.settings.max_tasks_per_job:
        raise Problem("QUOTA_EXCEEDED", "The plan exceeds the task limit", 422)
    for stage_id, specs in prepared.items():
        for spec in specs:
            db.add(
                m.Task(
                    job_id=job.id,
                    stage_id=stage_id,
                    state="queued",
                    item_ids=spec["item_ids"],
                    parameters=spec["parameters"],
                    units=spec["units"],
                    available_at=current,
                )
            )
    emit(db, job.pool_id, "job", job.id, "job_created", purpose=purpose)
    db.flush()
    return job


def tasks_for(db, job_id):
    return list(
        db.scalars(select(m.Task).where(m.Task.job_id == job_id).order_by(m.Task.created_at, m.Task.id))
    )


def describe(db, job):
    tasks = tasks_for(db, job.id)
    artifacts = []
    attempts = {
        a.id: a
        for a in db.scalars(
            select(m.Attempt)
            .join(m.Task, m.Task.id == m.Attempt.task_id)
            .where(m.Task.job_id == job.id, m.Attempt.id == m.Task.current_attempt_id)
        )
    }
    for acceptance in db.scalars(select(m.Acceptance).join(m.Task).where(m.Task.job_id == job.id)):
        for output in acceptance.result["outputs"]:
            row = db.get(m.Artifact, output["artifact_id"])
            if row:
                artifacts.append(artifact_dto(row))
    states = {task.state for task in tasks}
    if job.cancelled_at:
        state = "cancelled"
    elif states == {"succeeded"}:
        state = "succeeded"
    elif states <= TERMINAL:
        state = "partial" if "succeeded" in states else "failed"
    elif states - TERMINAL == {"waiting_for_capacity"}:
        state = "waiting_for_capacity"
    elif states & (COMPUTE | {"verifying", "succeeded", "failed"}):
        state = "running"
    else:
        state = "queued"
    return dto.Job(
        id=job.id,
        owner_id=job.owner_id,
        purpose=job.purpose,
        state=state,
        created_at=job.created_at,
        accepted_units=sum(task.units for task in tasks if task.state == "succeeded"),
        failed_units=sum(task.units for task in tasks if task.state in {"failed", "cancelled"}),
        total_units=sum(task.units for task in tasks),
        artifacts=artifacts,
        search_result=job.search_result,
        tasks=[
            dto.Task(
                id=t.id,
                stage_id=t.stage_id,
                state=t.state,
                generation=t.generation,
                units=t.units,
                error_code=t.error_code,
                attempt_id=t.current_attempt_id,
                node_id=attempts[t.current_attempt_id].node_id
                if t.current_attempt_id in attempts
                else None,
                verification_level=t.verification_level,
            )
            for t in tasks
        ],
    )


def update_finished(db, job, current):
    db.flush()
    if not db.scalar(
        select(m.Task.id).where(m.Task.job_id == job.id, m.Task.state.not_in(TERMINAL)).limit(1)
    ):
        job.finished_at = current


def cancel(db, job):
    write_lock(db)
    if job.finished_at or job.cancelled_at:
        return
    current = now(db)
    job.cancelled_at = job.finished_at = current
    for task in tasks_for(db, job.id):
        if task.state in TERMINAL:
            continue
        task.state = "cancelled"
        if task.current_attempt_id:
            attempt = db.get(m.Attempt, task.current_attempt_id)
            if attempt and attempt.state not in TERMINAL:
                attempt.state = "cancelled"
    emit(db, job.pool_id, "job", job.id, "job_cancelled")


def stage_for(job, task):
    return dto.Stage.model_validate(
        next(stage for stage in job.plan["stages"] if stage["stage_id"] == task.stage_id)
    )


def retry_or_fail(db, job, task, attempt, code, retryable, current):
    policy = job.plan["retry_policy"]
    attempt.state = "failed"
    attempt.error_code = task.error_code = code
    if retryable and task.generation < policy["max_attempts"] and current < job.deadline_at:
        base = policy["backoff_ms"] * 2 ** (task.generation - 1)
        jitter = int.from_bytes(task.id.bytes[-2:], "big") / 65535
        task.available_at = min(
            job.deadline_at, current + timedelta(milliseconds=base * (0.75 + 0.5 * jitter))
        )
        task.state = "retry_wait"
    else:
        task.state = "failed"
        update_finished(db, job, current)
    emit(
        db,
        job.pool_id,
        "job",
        job.id,
        "attempt_failed",
        task_id=str(task.id),
        attempt_id=str(attempt.id),
        code=code,
        state=task.state,
    )
