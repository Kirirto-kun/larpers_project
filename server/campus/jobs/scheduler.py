from collections import defaultdict
from datetime import timedelta
from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy import select

from .. import dto, native_worker, worker_compat
from .. import models as m
from ..auth import session_for
from ..db import digest, now, payload, secret_hash, write_lock
from ..events import emit
from .batching import observed_rate, resize
from .service import COMPUTE, RUNNABLE, artifact_dto, retry_or_fail, stage_for, update_finished

RESOURCE_KEYS = ("cpu_millis", "memory_bytes", "disk_bytes", "gpu_slots", "vram_bytes")


def budget(node, reserved=()):
    result = dict.fromkeys(RESOURCE_KEYS, 0)
    caps = node.capabilities
    if node.mode != "ready" or not caps or not caps["runtime"]["healthy"]:
        return result
    if caps["power_state"] == "battery" and not caps["allow_on_battery"]:
        return result
    offered = caps["offered_budget"]
    gpu_memory = [
        gpu["vram_bytes"]
        for gpu in caps["gpus"]
        if gpu["cuda_verified"] and gpu["vendor"].casefold() == "nvidia"
    ]
    available = (
        caps["cpu_logical"] * 1000,
        caps["memory_available_bytes"],
        caps["disk_available_bytes"],
        len(gpu_memory),
        max(gpu_memory, default=0),
    )
    for key, hardware in zip(RESOURCE_KEYS, available, strict=True):
        result[key] = min(hardware, offered[key]) - sum(r[key] for r in reserved)
    return result


def compatible(node, variant, remaining):
    if not node.capabilities or not node.current_session_id:
        return False
    caps = node.capabilities
    return (
        variant.platform == f"linux/{caps['arch']}"
        and variant.image_digest in caps["image_digests"]
        and all(getattr(variant.resource_requirements, key) <= remaining[key] for key in RESOURCE_KEYS)
    )


def active_attempts(db, node_id, current):
    return list(
        db.scalars(
            select(m.Attempt).where(
                m.Attempt.node_id == node_id,
                m.Attempt.state.in_(COMPUTE),
                m.Attempt.lease_expires_at > current,
            )
        )
    )


def assignment_inputs(db, job, task, stage):
    result = [
        dto.AssignmentInput(
            **payload(binding), artifact=artifact_dto(db.get(m.Artifact, binding.artifact_id))
        )
        for binding in stage.bindings
    ]
    if stage.depends_on:
        rows = db.execute(
            select(m.Task, m.Acceptance)
            .join(m.Acceptance)
            .where(
                m.Task.job_id == job.id,
                m.Task.stage_id.in_(stage.depends_on),
            )
            .order_by(m.Task.stage_id, m.Task.id)
        )
        for previous, acceptance in rows:
            for item in acceptance.result["outputs"]:
                artifact = artifact_dto(db.get(m.Artifact, item["artifact_id"]))
                result.append(
                    dto.AssignmentInput(
                        artifact_id=artifact.id,
                        artifact=artifact,
                        mount_path=f"/inputs/stages/{previous.stage_id}/{previous.id}/{item['path']}",
                    )
                )
    return result


def checkpoint_for(db, job, task, variant):
    from .leases import checkpoint_digest

    latest = db.scalar(
        select(m.Checkpoint)
        .where(
            m.Checkpoint.task_id == task.id,
            m.Checkpoint.compatibility_digest == checkpoint_digest(job, task, variant),
        )
        .order_by(m.Checkpoint.created_at.desc())
        .limit(1)
    )
    return artifact_dto(db.get(m.Artifact, latest.artifact_id)) if latest else None


def claim_work(db, app, actor, session_id, *, worker_policy=None, native=False):
    write_lock(db)
    node, session = session_for(db, actor, session_id, touch=True)
    if native and (
        node.applied_revision != node.settings_revision
        or node.actual_state != "running"
        or node.settings_error
    ):
        return None
    current = now(db)
    reserved = [
        variant["resource_requirements"]
        for variant in db.scalars(
            select(m.Attempt.variant).where(
                m.Attempt.node_id == node.id, m.Attempt.reserved_until > current
            )
        )
    ]
    remaining = budget(node, reserved)
    if remaining["cpu_millis"] <= 0:
        return None
    candidates = db.execute(
        select(m.Task, m.Job, m.User)
        .join(m.Job, m.Job.id == m.Task.job_id)
        .join(m.User, m.User.id == m.Job.owner_id)
        .where(
            m.Job.pool_id == node.pool_id,
            m.Job.finished_at.is_(None),
            m.Job.deadline_at > current,
            m.Task.state.in_(RUNNABLE),
            m.Task.available_at <= current,
        )
        .order_by(
            m.User.last_dispatched_at.asc().nulls_first(),
            m.Task.created_at,
            m.Task.id,
        )
        .with_for_update(of=m.Task, skip_locked=True)
    )
    dependencies = {}
    choices, rates = [], {}
    selected_owner = None
    for task, job, owner in candidates:
        if len(choices) >= 32:
            break
        if choices and owner.id != selected_owner:
            continue
        if job.node_ids is not None and str(node.id) not in job.node_ids:
            continue
        stage = stage_for(job, task)
        if stage.depends_on:
            if job.id not in dependencies:
                dependencies[job.id] = stage_states(db, job.id)
            ready = dependencies[job.id]
            if any(ready.get(dep) != {"succeeded"} for dep in stage.depends_on):
                continue
        if job.concurrency is not None and db.scalar(
            select(m.Task.id)
            .join(m.Attempt, m.Attempt.id == m.Task.current_attempt_id)
            .where(
                m.Task.job_id == job.id,
                m.Task.state.in_(COMPUTE),
                m.Attempt.node_id == node.id,
            )
            .offset(job.concurrency - 1)
            .limit(1)
        ):
            continue
        variants = sorted(stage.variants, key=lambda v: v.accelerator != "cuda")
        if job.purpose == "benchmark" and task.units:
            variants = benchmark_variants(db, job, node, variants)
        variant = next(
            (
                v
                for v in variants
                if compatible(node, v, remaining)
                and worker_compat.supports(stage, v, worker_policy, node=node)
                and (
                    not native
                    or native_worker.supports(
                        stage, v, node, gpu_busy=any(r["gpu_slots"] for r in reserved)
                    )
                )
            ),
            None,
        )
        if variant:
            selected_owner = owner.id
            # Owner fairness comes first. Avoid starving older or retrying work
            # while preferring cheaper transfers among fresh compatible tasks.
            if task.generation or (current - task.created_at).total_seconds() >= 30:
                choices = [((), task, job, owner, stage, variant)]
                break
            key = (
                variant.image_digest,
                stage.adapter.id,
                stage.adapter.version,
                digest(stage.parameters),
            )
            if key not in rates:
                rates[key] = observed_rate(db, node, stage, variant)
            cached = set(node.capabilities.get("cached_artifact_digests", []))
            inputs = assignment_inputs(db, job, task, stage)
            missing = {
                item.artifact.sha256: item.artifact.size_bytes
                for item in inputs
                if item.artifact.sha256 not in cached
            }
            transfer = sum(missing.values())
            throughput = node.capabilities.get("transfer_bytes_per_second")
            compute = task.units / rates[key] if rates[key] else 0
            cost = (transfer / throughput + compute, 0) if throughput else (transfer, compute)
            choices.append(((cost, task.created_at, task.id), task, job, owner, stage, variant))
    if choices:
        _, task, job, owner, stage, variant = min(choices, key=lambda item: item[0])
        resize(db, app, node, job, task, stage, variant)
        return lease(
            db,
            app,
            node,
            session,
            current,
            job,
            task,
            owner,
            stage,
            variant,
            worker_policy=worker_policy,
            native=native,
        )
    return None


def benchmark_variants(db, job, node, variants):
    benchmark = db.scalar(
        select(m.Benchmark).where(m.Benchmark.service_runs.contains([{"job_id": str(job.id)}]))
    )
    if not benchmark:
        return []
    record = db.scalar(
        select(m.Standalone)
        .where(
            m.Standalone.benchmark_id == benchmark.id,
            m.Standalone.node_id == node.id,
        )
        .limit(1)
    )
    if not record:
        return []
    return [
        v
        for v in variants
        if v.image_digest == record.record["image_digest"]
        and payload(v.resource_requirements) == record.record["resource_limits"]
    ]


def lease(
    db,
    app,
    node,
    session,
    current,
    job,
    task,
    owner,
    stage,
    variant,
    *,
    worker_policy=None,
    native=False,
):
    from .leases import checkpoint_digest

    token = token_urlsafe(32)
    deadline = min(job.deadline_at, current + timedelta(milliseconds=stage.task_deadline_ms))
    lease_end = min(deadline, current + timedelta(milliseconds=app.settings.lease_ms))
    task.generation += 1
    attempt = m.Attempt(
        id=uuid4(),
        task_id=task.id,
        node_id=node.id,
        session_id=session.id,
        generation=task.generation,
        token_hash=secret_hash(token),
        state="leased",
        variant=payload(variant),
        deadline_at=deadline,
        lease_expires_at=lease_end,
        reserved_until=lease_end,
    )
    task.state, task.current_attempt_id, task.error_code = "leased", attempt.id, None
    owner.last_dispatched_at = current
    db.add(attempt)
    emit(
        db,
        job.pool_id,
        "job",
        job.id,
        "task_leased",
        task_id=str(task.id),
        attempt_id=str(attempt.id),
        node_id=str(node.id),
        generation=attempt.generation,
    )
    value = dto.Assignment(
        job_id=job.id,
        task_id=task.id,
        stage_id=stage.stage_id,
        attempt_id=attempt.id,
        generation=attempt.generation,
        variant_id=variant.variant_id,
        lease_token=token,
        lease_expires_at=lease_end,
        lease_remaining_ms=int((lease_end - current).total_seconds() * 1000),
        heartbeat_interval_ms=app.settings.heartbeat_interval_ms,
        task_deadline_at=deadline,
        manifest_digest=job.manifest_digest,
        adapter=stage.adapter,
        image_digest=variant.image_digest,
        platform=variant.platform,
        accelerator=variant.accelerator,
        argv=stage.argv,
        working_dir=stage.working_dir,
        inputs=assignment_inputs(db, job, task, stage),
        outputs=stage.outputs,
        resource_limits=variant.resource_requirements,
        network_policy=stage.network_policy,
        resume_checkpoint=checkpoint_for(db, job, task, variant)
        if stage.resume_policy == "checkpoint"
        else None,
        validator_policy=stage.validation_policy,
        item_ids=task.item_ids,
        checkpoint_compatibility_digest=(
            checkpoint_digest(job, task, variant) if stage.resume_policy == "checkpoint" else None
        ),
        parameters={**stage.parameters, **task.parameters},
    )
    remaining_ms = int((deadline - current).total_seconds() * 1000)
    if native:
        return payload(
            dto.NativeAssignment.model_validate(
                native_worker.assignment(payload(value), stage, variant, node, remaining_ms=remaining_ms)
            )
        )
    return (
        worker_compat.assignment(payload(value), worker_policy, remaining_ms=remaining_ms)
        if worker_policy
        else value
    )


def stage_states(db, job_id):
    states = defaultdict(set)
    for stage_id, state in db.execute(
        select(m.Task.stage_id, m.Task.state).where(m.Task.job_id == job_id)
    ):
        states[stage_id].add(state)
    return states


def reap(db, app):
    write_lock(db)
    current = now(db)
    for attempt, task, job, node in db.execute(
        select(m.Attempt, m.Task, m.Job, m.Node)
        .join(m.Task, m.Attempt.task_id == m.Task.id)
        .join(m.Job, m.Task.job_id == m.Job.id)
        .join(m.Node, m.Node.id == m.Attempt.node_id)
        .where(
            m.Attempt.state.in_(COMPUTE),
            m.Job.finished_at.is_(None),
            (m.Attempt.lease_expires_at <= current)
            | m.Node.current_session_id.is_(None)
            | (m.Node.current_session_id != m.Attempt.session_id)
            | m.Node.mode.in_(("paused", "quarantined")),
        )
        .limit(200)
    ):
        expired = attempt.lease_expires_at <= current
        code = "TASK_DEADLINE" if attempt.deadline_at <= current else "LEASE_EXPIRED"
        retry_or_fail(db, job, task, attempt, code if expired else "SESSION_REPLACED", True, current)
    for job in db.scalars(select(m.Job).where(m.Job.finished_at.is_(None))):
        db.flush()
        states = stage_states(db, job.id)
        nodes = list(
            db.scalars(
                select(m.Node).where(
                    m.Node.pool_id == job.pool_id,
                    m.Node.last_seen_at > current - timedelta(seconds=app.settings.offline_seconds),
                )
            )
        )
        for task in db.scalars(
            select(m.Task).where(m.Task.job_id == job.id, m.Task.state.in_(RUNNABLE | {"verifying"}))
        ):
            if job.deadline_at <= current:
                fail_deadline(db, job, task, "JOB_DEADLINE")
                continue
            if task.state == "verifying":
                continue
            stage = stage_for(job, task)
            if any(states[dep] & {"failed", "cancelled"} for dep in stage.depends_on):
                fail_deadline(db, job, task, "UPSTREAM_FAILED")
                continue
            if task.available_at > current or any(
                states[dep] != {"succeeded"} for dep in stage.depends_on
            ):
                continue
            present = any(
                compatible(node, variant, budget(node))
                for node in nodes
                if job.node_ids is None or str(node.id) in job.node_ids
                for variant in stage.variants
            )
            desired = "queued" if present else "waiting_for_capacity"
            if task.state != desired:
                task.state = desired
                emit(
                    db, job.pool_id, "job", job.id, "task_capacity", task_id=str(task.id), state=desired
                )
        update_finished(db, job, current)


def fail_deadline(db, job, task, code):
    task.state, task.error_code = "failed", code
    if task.current_attempt_id:
        attempt = db.get(m.Attempt, task.current_attempt_id)
        attempt.state, attempt.error_code = "failed", code
    emit(db, job.pool_id, "job", job.id, "task_failed", task_id=str(task.id), code=code)
