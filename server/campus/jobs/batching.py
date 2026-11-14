"""Resize only never-leased map tasks, using accepted server-observed durations."""

import math
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import func, select

from .. import models as m
from ..db import now
from ..events import emit


def observed_rate(db, node, stage, variant):
    observations = db.execute(
        select(m.Task.units, m.Attempt.started_at, m.Attempt.receipt, m.Job.plan, m.Task.stage_id)
        .select_from(m.Acceptance)
        .join(m.Task, m.Task.id == m.Acceptance.task_id)
        .join(m.Attempt, m.Attempt.id == m.Acceptance.attempt_id)
        .join(m.Job, m.Job.id == m.Task.job_id)
        .where(
            m.Attempt.node_id == node.id,
            m.Task.units > 0,
            m.Attempt.started_at.is_not(None),
            m.Attempt.variant["image_digest"].astext == variant.image_digest,
        )
        .order_by(m.Acceptance.created_at.desc())
        .limit(20)
    )
    rates = []
    for units, started, receipt, plan, stage_id in observations:
        previous = next(s for s in plan["stages"] if s["stage_id"] == stage_id)
        if (
            previous["adapter"] != stage.adapter.model_dump()
            or previous["parameters"] != stage.parameters
        ):
            continue
        elapsed = (datetime.fromisoformat(receipt["submitted_at"]) - started).total_seconds()
        if elapsed > 0:
            rates.append(units / elapsed)
    return median(rates) if rates else None


def resize(db, app, node, job, task, stage, variant):
    adapter = app.catalog.adapter(stage.adapter)
    if (
        not adapter.adaptive_batches
        or job.purpose != "work"
        or not task.item_ids
        or task.generation
        or stage.depends_on
    ):
        return
    rate = observed_rate(db, node, stage, variant)
    desired = (
        min(adapter.max_batch_units, max(1, int(rate * 10))) if rate else adapter.initial_batch_units
    )
    peers = list(
        db.scalars(
            select(m.Task).where(
                m.Task.job_id == job.id,
                m.Task.stage_id == task.stage_id,
                m.Task.generation == 0,
                m.Task.state.in_(("queued", "waiting_for_capacity")),
            )
        )
    )
    waiting = sum(len(peer.item_ids) for peer in peers)
    ready_nodes = db.scalar(
        select(func.count())
        .select_from(m.Node)
        .where(
            m.Node.pool_id == node.pool_id,
            m.Node.mode == "ready",
            m.Node.current_session_id.is_not(None),
            m.Node.last_seen_at > now(db) - timedelta(seconds=app.settings.offline_seconds),
        )
    )
    desired = min(desired, max(1, math.ceil(waiting / max(1, ready_nodes))))
    if desired >= len(task.item_ids):
        return
    if (
        db.scalar(select(func.count()).select_from(m.Task).where(m.Task.job_id == job.id))
        >= app.settings.max_tasks_per_job
    ):
        return
    remainder = m.Task(
        job_id=job.id,
        stage_id=task.stage_id,
        state="queued",
        item_ids=task.item_ids[desired:],
        parameters=task.parameters,
        units=len(task.item_ids) - desired,
        available_at=task.available_at,
    )
    task.item_ids = task.item_ids[:desired]
    task.units = desired
    db.add(remainder)
    db.flush()
    emit(
        db,
        job.pool_id,
        "job",
        job.id,
        "batch_resized",
        task_id=str(task.id),
        remainder_task_id=str(remainder.id),
        assigned_units=desired,
        remaining_units=remainder.units,
    )
