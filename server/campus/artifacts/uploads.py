import math
from datetime import timedelta

from sqlalchemy import func, select

from .. import dto
from .. import models as m
from ..db import now, write_lock
from ..errors import Problem, conflict, forbidden, missing
from ..jobs import leases
from ..jobs.service import artifact_dto, stage_for
from .checkpoints import validate_checkpoint
from .storage import safe_relative, secret_path, validate_archive


def owned(db, actor, session_id, upload_id, *, mutate=False):
    row = db.get(m.Upload, upload_id)
    if not row or row.pool_id != actor.user.pool_id:
        raise missing()
    if row.attempt_id:
        if actor.credential.scope != "device":
            raise missing()
        ctx = leases.context(db, actor, session_id, row.attempt_id)
        if mutate and row.state != "ready":
            leases.live(ctx, now(db))
    else:
        actor.owns(row.owner_id, row.pool_id)
    if mutate and row.state != "ready" and (row.state == "expired" or row.expires_at <= now(db)):
        raise Problem("UPLOAD_EXPIRED", "The upload has expired", 409)
    return row


def describe(db, row):
    parts = db.scalars(
        select(m.UploadPart).where(m.UploadPart.upload_id == row.id).order_by(m.UploadPart.number)
    ).all()
    artifact = db.scalar(select(m.Artifact).where(m.Artifact.upload_id == row.id))
    return dto.Upload(
        id=row.id,
        state=row.state,
        part_size_bytes=row.part_size_bytes,
        part_count=math.ceil(row.size_bytes / row.part_size_bytes),
        parts=[dto.UploadPart(number=p.number, size_bytes=p.size_bytes, sha256=p.sha256) for p in parts],
        artifact=artifact_dto(artifact) if artifact else None,
    )


def begin(db, app, actor, session_id, body):
    write_lock(db)
    name = safe_relative(body.name)
    if secret_path(name):
        raise Problem("BLOCKED_FILE", "This path cannot be uploaded", 422)
    if any(ord(c) < 32 for c in body.media_type):
        raise Problem("INVALID_MEDIA_TYPE", "The media type is invalid", 422)
    owner = actor.user.id
    if body.kind in {"output", "checkpoint"}:
        if not body.attempt_id or not body.proof:
            raise Problem("PROOF_REQUIRED", "Attempt proof is required", 422)
        _, _, task, job = leases.fenced(db, actor, session_id, body.attempt_id, body.proof)
        stage = stage_for(job, task)
        owner = job.owner_id
        if body.kind == "output":
            output = next((o for o in stage.outputs if o.path == body.name), None)
            if not output or body.size_bytes > output.max_bytes:
                raise Problem("UNDECLARED_OUTPUT", "The output path or size was not declared", 422)
        elif stage.resume_policy != "checkpoint":
            raise Problem("INVALID_CHECKPOINT", "The task has no checkpoint support", 422)
    else:
        actor.member()
        if body.proof or body.attempt_id:
            raise Problem("UNEXPECTED_PROOF", "Input upload cannot be scoped to an attempt", 422)
    if (
        body.size_bytes > app.settings.upload_limit_bytes
        or math.ceil(body.size_bytes / body.part_size_bytes) > 4096
    ):
        raise Problem("QUOTA_EXCEEDED", "The upload exceeds the size or part limit", 413)
    current = now(db)
    total, pending = db.execute(
        select(
            func.coalesce(func.sum(m.Upload.size_bytes), 0),
            func.count().filter(m.Upload.state == "pending"),
        ).where(
            m.Upload.owner_id == owner,
            (m.Upload.state == "ready")
            | ((m.Upload.state == "pending") & (m.Upload.expires_at > current)),
        )
    ).one()
    if (
        total + body.size_bytes > app.settings.owner_storage_limit_bytes
        or pending >= app.settings.max_pending_uploads
    ):
        raise Problem("QUOTA_EXCEEDED", "The storage quota is exhausted", 429)
    row = m.Upload(
        pool_id=actor.user.pool_id,
        owner_id=owner,
        attempt_id=body.attempt_id,
        name=str(name),
        kind=body.kind,
        size_bytes=body.size_bytes,
        sha256=body.sha256,
        media_type=body.media_type,
        part_size_bytes=body.part_size_bytes,
        state="pending",
        expires_at=current + timedelta(seconds=app.settings.upload_ttl_seconds),
    )
    db.add(row)
    db.flush()
    return describe(db, row)


def check_part(db, actor, session_id, upload_id, part, sha):
    row = owned(db, actor, session_id, upload_id, mutate=True)
    size = min(row.part_size_bytes, row.size_bytes - row.part_size_bytes * part)
    if part < 0 or size <= 0:
        raise Problem("INVALID_PART", "Part number is outside the upload", 422)
    existing = db.get(m.UploadPart, (upload_id, part))
    if existing:
        if existing.sha256 != sha:
            raise conflict("This part was stored with a different checksum")
        return size, True
    if row.state != "pending":
        raise Problem("INVALID_UPLOAD_STATE", "This upload no longer accepts parts", 409)
    return size, False


def register_part(db, app, actor, session_id, upload_id, number, sha, size):
    # Each checksum has a different temporary destination. A rejected replay
    # cannot overwrite the part already referenced by PostgreSQL.
    write_lock(db)
    _, exists = check_part(db, actor, session_id, upload_id, number, sha)
    if not exists:
        if not app.storage.part(upload_id, f"{number}-{sha}").is_file():
            raise Problem(
                "STORAGE_UNAVAILABLE", "Part publication could not be confirmed", 503, retryable=True
            )
        db.add(m.UploadPart(upload_id=upload_id, number=number, sha256=sha, size_bytes=size))
    return dto.UploadPart(number=number, sha256=sha, size_bytes=size)


def finalize_info(db, actor, session_id, upload_id):
    row = owned(db, actor, session_id, upload_id, mutate=True)
    view = describe(db, row)
    if not view.artifact and len(view.parts) != view.part_count:
        raise Problem("UPLOAD_INCOMPLETE", "Some upload parts are missing", 409, retryable=True)
    return row, view


def publish_blob(app, row, view):
    def chunks():
        for part in view.parts:
            file = app.storage.part(row.id, f"{part.number}-{part.sha256}")
            with file.open("rb") as source:
                yield from iter(lambda: source.read(1024 * 1024), b"")

    checkpoint_context = None
    if row.kind == "checkpoint":
        with app.sessions() as db:
            attempt = db.get(m.Attempt, row.attempt_id)
            task = db.get(m.Task, attempt.task_id)
            job = db.get(m.Job, task.job_id)
            stage = stage_for(job, task)
            checkpoint_context = (
                leases.checkpoint_digest(job, task, dto.Variant.model_validate(attempt.variant)),
                app.catalog.adapter(stage.adapter).checkpoint_files,
            )

    def validate(path):
        validate_archive(path, app.settings.upload_limit_bytes)
        if checkpoint_context:
            validate_checkpoint(path, *checkpoint_context)

    app.storage.publish(
        app.storage.blob(row.sha256),
        chunks(),
        row.sha256,
        row.size_bytes,
        validate=validate if row.kind in {"project", "checkpoint"} else None,
    )


def register_artifact(db, app, actor, session_id, upload_id):
    write_lock(db)
    row, view = finalize_info(db, actor, session_id, upload_id)
    if view.artifact:
        return view.artifact
    if not app.storage.blob(row.sha256).is_file():
        raise Problem(
            "STORAGE_UNAVAILABLE", "Artifact publication could not be confirmed", 503, retryable=True
        )
    artifact = m.Artifact(
        upload_id=row.id,
        pool_id=row.pool_id,
        owner_id=row.owner_id,
        attempt_id=row.attempt_id,
        name=row.name,
        kind=row.kind,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        media_type=row.media_type,
    )
    db.add(artifact)
    row.state = "ready"
    db.flush()
    return artifact_dto(artifact)


def resolve_download(db, actor, session_id, artifact_id):
    row = db.get(m.Artifact, artifact_id)
    if not row or row.pool_id != actor.user.pool_id:
        raise missing()
    if actor.credential.scope != "device":
        actor.owns(row.owner_id, row.pool_id)
        if row.attempt_id:
            result = db.scalar(select(m.Acceptance).where(m.Acceptance.attempt_id == row.attempt_id))
            if not result or str(row.id) not in {o["artifact_id"] for o in result.result["outputs"]}:
                raise missing()
        return row

    from ..auth import session_for
    from ..jobs.scheduler import active_attempts, assignment_inputs, checkpoint_for

    node, _ = session_for(db, actor, session_id)
    current = now(db)
    for attempt in active_attempts(db, node.id, current):
        if attempt.session_id != session_id:
            continue
        task = db.get(m.Task, attempt.task_id)
        job = db.get(m.Job, task.job_id)
        try:
            leases.live((node, attempt, task, job), current)
        except Problem:
            continue
        stage = stage_for(job, task)
        allowed = {item.artifact_id for item in assignment_inputs(db, job, task, stage)}
        if stage.resume_policy == "checkpoint":
            checkpoint = checkpoint_for(db, job, task, dto.Variant.model_validate(attempt.variant))
            if checkpoint:
                allowed.add(checkpoint.id)
        if row.id in allowed:
            return row
    raise forbidden()


def reconcile(db, app):
    referenced = set(db.scalars(select(m.Artifact.sha256)))
    missing_blobs = sorted(sha for sha in referenced if not app.storage.blob(sha).is_file())
    return {"referenced_blobs": len(referenced), "missing_blobs": missing_blobs}


def mark_expired(db):
    write_lock(db)
    current = now(db)
    for upload in db.scalars(
        select(m.Upload).where(m.Upload.state == "pending", m.Upload.expires_at <= current)
    ):
        upload.state = "expired"


def collect(db, app):
    # The transaction lock makes publication's final file check and GC mutually
    # exclusive. Fresh filesystem writes have a grace period before collection.
    write_lock(db)
    cutoff = now(db).timestamp() - app.settings.orphan_grace_seconds
    referenced = set(db.scalars(select(m.Artifact.sha256)))
    retained = {
        (str(row.upload_id), f"{row.number}-{row.sha256}")
        for row in db.scalars(select(m.UploadPart).join(m.Upload).where(m.Upload.state == "pending"))
    }
    removed = 0
    for root, preserve in (
        (app.storage.blobs, lambda p: p.name in referenced),
        (app.storage.parts, lambda p: (p.parent.name, p.name) in retained),
    ):
        for path in root.glob("*/*"):
            if not preserve(path) and path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
    return removed
