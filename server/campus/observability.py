from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select

from . import dto
from . import models as m
from .auth import node_state, session_for
from .db import now, payload, write_lock
from .errors import Problem, conflict, missing
from .events import emit
from .jobs.leases import fenced
from .jobs.scheduler import active_attempts
from .jobs.service import describe, owned_job


def node_in_pool(db, actor, node_id):
    actor.member()
    node = db.get(m.Node, node_id)
    if not node or node.pool_id != actor.user.pool_id:
        raise missing()
    return node


def describe_node(db, app, node, current):
    units = db.scalar(
        select(func.coalesce(func.sum(m.Task.units), 0))
        .select_from(m.Acceptance)
        .join(m.Task, m.Task.id == m.Acceptance.task_id)
        .join(m.Attempt, m.Attempt.id == m.Acceptance.attempt_id)
        .join(m.Job, m.Job.id == m.Task.job_id)
        .where(m.Attempt.node_id == node.id, m.Job.purpose == "work")
    )
    return dto.Node(
        id=node.id,
        name=node.name,
        state=node_state(node, current, app.settings.offline_seconds),
        last_seen_at=node.last_seen_at,
        capabilities=node.capabilities,
        accepted_units=units,
        active_attempts=len(active_attempts(db, node.id, current)),
    )


def control(db, actor, node_id, body):
    write_lock(db)
    node = node_in_pool(db, actor, node_id)
    actor.owns(node.owner_id, node.pool_id)
    if body.action in {"quarantine", "release"}:
        actor.admin()
    if node.mode == "quarantined" and body.action != "release":
        raise Problem("QUARANTINED", "An administrator must release the device", 409)
    if body.action == "release" and node.mode != "quarantined":
        raise Problem("INVALID_STATE", "Only a quarantined device can be released", 409)
    node.mode = {
        "drain": "draining",
        "stop": "paused",
        "resume": "ready",
        "quarantine": "quarantined",
        "release": "paused",
    }[body.action]
    if body.action in {"stop", "quarantine"}:
        # Rotate the session fence immediately, even if resume races the reaper.
        node.current_session_id = None
        node.capabilities = None
    emit(db, node.pool_id, "node", node.id, "node_controlled", action=body.action)
    return node


def summary(db, app, actor):
    user = actor.member()
    current = now(db)
    nodes = list(db.scalars(select(m.Node).where(m.Node.pool_id == user.pool_id)))
    jobs = list(db.scalars(select(m.Job).where(m.Job.pool_id == user.pool_id)))
    states = Counter(describe(db, job).state for job in jobs)
    units = db.scalar(
        select(func.coalesce(func.sum(m.Ledger.accepted_units), 0))
        .join(m.User, m.User.id == m.Ledger.consumer_id)
        .where(m.User.pool_id == user.pool_id)
    )
    return dto.PoolSummary(
        pool_id=user.pool_id,
        nodes=len(nodes),
        jobs_by_state=states,
        accepted_units=units,
        ready_nodes=sum(node_state(n, current, app.settings.offline_seconds) == "ready" for n in nodes),
    )


def credits(db, actor):
    user = actor.member()
    query = (
        select(m.Ledger)
        .join(m.User, m.User.id == m.Ledger.consumer_id)
        .where(m.User.pool_id == user.pool_id)
    )
    if not actor.privileged:
        query = query.where((m.Ledger.consumer_id == user.id) | (m.Ledger.contributor_id == user.id))
    return [
        dto.Credit(
            task_id=r.task_id,
            contributor_id=r.contributor_id,
            consumer_id=r.consumer_id,
            accepted_units=r.accepted_units,
            unit=r.unit,
            rate_version=r.rate_version,
            amount=r.amount,
            occurred_at=r.created_at,
        )
        for r in db.scalars(query.order_by(m.Ledger.created_at.desc()).limit(200))
    ]


def logs(db, actor, job_id, cursor):
    owned_job(db, actor, job_id)
    rows = list(
        db.scalars(
            select(m.Log).where(m.Log.job_id == job_id, m.Log.id > cursor).order_by(m.Log.id).limit(100)
        )
    )
    truncated = bool(
        db.scalar(
            select(m.Attempt.id)
            .join(m.Task)
            .where(
                m.Task.job_id == job_id,
                m.Attempt.log_truncated,
            )
            .limit(1)
        )
    )
    return dto.LogPage(
        entries=[
            dto.LogEntry(
                cursor=r.id,
                attempt_id=r.attempt_id,
                sequence=r.sequence,
                occurred_at=r.created_at,
                text=r.text,
                gap=r.gap,
            )
            for r in rows
        ],
        cursor=rows[-1].id if rows else cursor,
        truncated=truncated,
    )


def append_log(db, app, actor, session_id, attempt_id, body):
    _, attempt, _, job = fenced(db, actor, session_id, attempt_id, body)
    prior = db.scalar(
        select(m.Log).where(m.Log.attempt_id == attempt.id).order_by(m.Log.sequence.desc()).limit(1)
    )
    stored = db.scalar(
        select(m.Log).where(m.Log.attempt_id == attempt.id, m.Log.sequence == body.sequence)
    )
    if stored:
        if stored.text != body.text:
            raise conflict("Log sequence has a different payload")
        return
    if "\x00" in body.text:
        raise Problem("INVALID_LOG", "Log chunk contains a NUL character", 422)
    if prior and body.sequence <= prior.sequence:
        raise Problem("STALE_SEQUENCE", "Log sequence must increase", 409)
    size = len(body.text.encode())
    if (
        attempt.log_bytes + size > app.settings.max_log_bytes_per_attempt
        or size == 0
        or body.sequence >= 4096
    ):
        attempt.log_truncated = True
        return
    attempt.log_bytes += size
    gap = body.sequence != (prior.sequence + 1 if prior else 0)
    db.add(m.Log(job_id=job.id, attempt_id=attempt.id, sequence=body.sequence, text=body.text, gap=gap))


def append_metric(db, actor, session_id, body, attempt_id=None):
    write_lock(db)
    if attempt_id:
        node, _, _, _ = fenced(db, actor, session_id, attempt_id, body)
        sample = body.model_dump(mode="json", exclude={"generation", "lease_token"})
    else:
        node, _ = session_for(db, actor, session_id, touch=True)
        sample = payload(body)
    stream = attempt_id or session_id
    previous = db.scalar(
        select(m.Metric).where(m.Metric.stream_id == stream).order_by(m.Metric.sequence.desc()).limit(1)
    )
    stored = db.scalar(
        select(m.Metric).where(m.Metric.stream_id == stream, m.Metric.sequence == body.sequence)
    )
    if stored:
        if stored.sample != sample:
            raise conflict("Telemetry sequence has a different payload")
        return
    if previous and body.sequence <= previous.sequence:
        raise Problem("STALE_SEQUENCE", "Telemetry sequence must increase", 409)
    if previous and now(db) - previous.created_at < timedelta(milliseconds=800):
        raise Problem(
            "RATE_LIMITED", "Telemetry is limited to one sample per second", 429, retryable=True
        )
    gap = body.sequence != (previous.sequence + 1 if previous else 0)
    db.add(
        m.Metric(
            node_id=node.id,
            stream_id=stream,
            attempt_id=attempt_id,
            sequence=body.sequence,
            sample=sample,
            gap=gap,
        )
    )
