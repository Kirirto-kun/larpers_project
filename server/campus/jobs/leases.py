import hmac
from datetime import timedelta

from sqlalchemy import select

from .. import dto
from .. import models as m
from ..auth import session_for
from ..db import digest, now, payload, secret_hash, write_lock
from ..errors import Problem, conflict, missing
from ..events import emit
from .service import COMPUTE, artifact_dto, retry_or_fail, stage_for

STALE = ("STALE_ATTEMPT", "The attempt is no longer authoritative", 409)


def context(db, actor, session_id, attempt_id, *, touch=False):
    node, session = session_for(db, actor, session_id, touch=touch)
    attempt = db.get(m.Attempt, attempt_id)
    if not attempt or attempt.node_id != node.id:
        raise missing()
    if attempt.session_id != session.id:
        raise Problem(*STALE)
    task = db.get(m.Task, attempt.task_id)
    return node, attempt, task, db.get(m.Job, task.job_id)


def authenticate_proof(attempt, proof):
    if attempt.generation != proof.generation or not hmac.compare_digest(
        attempt.token_hash, secret_hash(proof.lease_token)
    ):
        raise Problem(*STALE)


def live(ctx, current):
    node, attempt, task, job = ctx
    if (
        attempt.id != task.current_attempt_id
        or attempt.state not in COMPUTE
        or job.cancelled_at
        or node.mode in {"paused", "quarantined"}
    ):
        raise Problem(*STALE)
    if attempt.lease_expires_at <= current:
        raise Problem("LEASE_EXPIRED", "The attempt lease has expired", 409)


def fenced(db, actor, session_id, attempt_id, proof):
    write_lock(db)
    ctx = context(db, actor, session_id, attempt_id, touch=True)
    authenticate_proof(ctx[1], proof)
    live(ctx, now(db))
    return ctx


def receipt(attempt, current):
    return dto.LeaseReceipt(
        attempt_id=attempt.id,
        state=attempt.state,
        lease_expires_at=attempt.lease_expires_at,
        lease_remaining_ms=max(0, int((attempt.lease_expires_at - current).total_seconds() * 1000)),
    )


def started(db, actor, session_id, attempt_id, body):
    _, attempt, task, job = fenced(db, actor, session_id, attempt_id, body)
    current = now(db)
    if attempt.state == "leased":
        task.state = attempt.state = "running"
        attempt.started_at = current
        emit(
            db,
            job.pool_id,
            "job",
            job.id,
            "attempt_started",
            attempt_id=str(attempt.id),
            task_id=str(task.id),
        )
    return receipt(attempt, current)


def renew_attempt(db, app, actor, session_id, attempt_id, body):
    _, attempt, _, _ = fenced(db, actor, session_id, attempt_id, body)
    current = now(db)
    if attempt.lease_expires_at <= current:
        raise Problem("LEASE_EXPIRED", "The attempt lease has expired", 409)
    attempt.lease_expires_at = min(
        attempt.deadline_at, current + timedelta(milliseconds=app.settings.lease_ms)
    )
    return receipt(attempt, current)


def output_manifest(db, stage, task, attempt, manifest):
    if len(manifest.item_ids) != len(set(manifest.item_ids)) or set(manifest.item_ids) != set(
        task.item_ids
    ):
        raise Problem("INVALID_RESULT", "Result does not match the assigned item IDs", 422)
    expected = {output.path: output for output in stage.outputs}
    actual = {output.path for output in manifest.outputs}
    if (
        len(actual) != len(manifest.outputs)
        or actual - expected.keys()
        or {o.path for o in expected.values() if o.required} - actual
    ):
        raise Problem("INVALID_RESULT", "Result paths do not match the declared outputs", 422)
    for output in manifest.outputs:
        row = db.get(m.Artifact, output.artifact_id)
        if (
            not row
            or row.attempt_id != attempt.id
            or row.kind != "output"
            or row.name != output.path
            or row.size_bytes > expected[output.path].max_bytes
        ):
            raise Problem(
                "ARTIFACT_NOT_READY", "A result output is missing or exceeds its declared limit", 409
            )


def complete_attempt(db, app, actor, session_id, attempt_id, body):
    write_lock(db)
    ctx = context(db, actor, session_id, attempt_id)
    node, attempt, task, job = ctx
    authenticate_proof(attempt, body)
    key = digest(body.manifest)
    if attempt.receipt is not None:
        if key != attempt.completion_digest:
            raise conflict("Completion was already submitted with a different result")
        return dto.CompletionReceipt.model_validate(attempt.receipt)
    current = now(db)
    live(ctx, current)
    output_manifest(db, stage_for(job, task), task, attempt, body.manifest)
    current = now(db)
    live(ctx, current)
    node.last_seen_at = current
    task.state = attempt.state = "verifying"
    attempt.completion_digest = key
    attempt.result = payload(body.manifest)
    attempt.verification_deadline_at = min(
        job.deadline_at, current + timedelta(seconds=app.settings.verification_seconds)
    )
    result = dto.CompletionReceipt(
        attempt_id=attempt.id, task_id=task.id, state="verifying", submitted_at=current
    )
    attempt.receipt = payload(result)
    emit(
        db,
        job.pool_id,
        "job",
        job.id,
        "verification_pending",
        task_id=str(task.id),
        attempt_id=str(attempt.id),
    )
    return result


def attempt_status(db, actor, session_id, attempt_id):
    _, attempt, task, _ = context(db, actor, session_id, attempt_id)
    return dto.AttemptStatus(
        attempt_id=attempt.id,
        task_id=task.id,
        generation=attempt.generation,
        state=attempt.state,
        error_code=attempt.error_code,
        receipt=attempt.receipt,
    )


def fail(db, actor, session_id, attempt_id, body, release=False):
    _, attempt, task, job = fenced(db, actor, session_id, attempt_id, body)
    code = "WORKER_RELEASED" if release else body.code
    temporary = release or (
        body.retryable and code in {"OOM", "RUNTIME_INTERRUPTED", "NETWORK_ERROR", "NODE_BUSY"}
    )
    retry_or_fail(db, job, task, attempt, code, temporary, now(db))
    return dto.AttemptStatus(
        attempt_id=attempt.id,
        task_id=task.id,
        generation=attempt.generation,
        state=attempt.state,
        error_code=code,
    )


def checkpoint_digest(job, task, variant):
    return digest(
        {
            "manifest_digest": job.manifest_digest,
            "stage_id": task.stage_id,
            "item_ids": task.item_ids,
            "image_digest": variant.image_digest,
        }
    )


def checkpoint(db, app, actor, session_id, attempt_id, body):
    ctx = fenced(db, actor, session_id, attempt_id, body)
    _, attempt, task, job = ctx
    stage = stage_for(job, task)
    expected = checkpoint_digest(job, task, dto.Variant.model_validate(attempt.variant))
    row = db.get(m.Artifact, body.artifact_id)
    if (
        stage.resume_policy != "checkpoint"
        or body.compatibility_digest != expected
        or not row
        or row.kind != "checkpoint"
        or row.attempt_id != attempt.id
    ):
        raise Problem("INVALID_CHECKPOINT", "Checkpoint is incomplete or incompatible", 422)
    latest = db.scalar(
        select(m.Checkpoint)
        .where(m.Checkpoint.attempt_id == attempt.id)
        .order_by(m.Checkpoint.sequence.desc())
        .limit(1)
    )
    if latest and latest.sequence >= body.sequence:
        if latest.sequence == body.sequence and latest.artifact_id == row.id:
            return artifact_dto(row)
        raise conflict("Checkpoint sequence must increase")
    live(ctx, now(db))
    db.add(
        m.Checkpoint(
            task_id=task.id,
            attempt_id=attempt.id,
            artifact_id=row.id,
            compatibility_digest=expected,
            sequence=body.sequence,
        )
    )
    emit(
        db,
        job.pool_id,
        "job",
        job.id,
        "checkpoint_published",
        task_id=str(task.id),
        artifact_id=str(row.id),
    )
    return artifact_dto(row)
