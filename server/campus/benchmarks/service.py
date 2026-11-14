from datetime import timedelta
from statistics import median

from sqlalchemy import select

from .. import dto
from .. import models as m
from ..auth import device_node
from ..db import digest, now, payload, write_lock
from ..errors import Problem, conflict, missing
from ..jobs.service import create_job, current_plan, owned_preview
from ..planner.validation import validate_plan


def create(db, app, actor, body):
    actor.admin()
    write_lock(db)
    user = actor.user
    preview = owned_preview(db, actor, body.preview_id)
    if preview.owner_id != user.id:
        raise missing()
    plan = current_plan(preview, body.plan_revision)
    validate_plan(
        db,
        app,
        user,
        plan,
        dto.PreviewRequest.model_validate(preview.request),
        server_evidence=preview.probe_job_id is not None,
    )
    if any(stage.validation_policy == "structural" for stage in plan.stages):
        raise Problem("VALIDATOR_REQUIRED", "Benchmark plans require full validation", 422)
    nodes = list(
        db.scalars(select(m.Node).where(m.Node.id.in_(body.node_ids), m.Node.pool_id == user.pool_id))
    )
    if len(nodes) != len(body.node_ids):
        raise Problem("INVALID_NODES", "Benchmark requires distinct registered nodes in this pool", 422)
    unit_stages = [
        stage for stage in plan.stages if any(item["units"] for item in preview.prepared[stage.stage_id])
    ]
    if len(unit_stages) != 1 or len(preview.prepared[unit_stages[0].stage_id]) < len(nodes):
        raise Problem(
            "BENCHMARK_CAPACITY",
            "Benchmark requires one measurable stage with enough distinct tasks",
            422,
        )
    target = body.batch_policy.get("target_units")
    if (
        set(body.batch_policy) - {"target_units"}
        or target
        and any(spec["units"] > target for spec in preview.prepared[unit_stages[0].stage_id])
    ):
        raise Problem(
            "BENCHMARK_MISMATCH", "The immutable plan does not match the requested batch policy", 422
        )
    config = payload(body)
    row = m.Benchmark(
        pool_id=user.pool_id,
        owner_id=user.id,
        config=config,
        config_digest=digest({"config": config, "plan_digest": preview.plan_digest}),
        manifest_digest=plan.input_manifest_digest,
        plan=payload(plan),
        prepared=preview.prepared,
    )
    db.add(row)
    db.flush()
    return row


def owned(db, actor, benchmark_id):
    row = db.get(m.Benchmark, benchmark_id)
    if not row or row.pool_id != actor.user.pool_id:
        raise missing()
    return row


def standalone(db, actor, benchmark_id, body):
    write_lock(db)
    node = device_node(db, actor)
    row = owned(db, actor, benchmark_id)
    config = dto.BenchmarkCreate.model_validate(row.config)
    plan = dto.JobPlan.model_validate(row.plan)
    if (
        node.id != body.node_id
        or node.id not in config.node_ids
        or body.config_digest != row.config_digest
        or body.manifest_digest != row.manifest_digest
        or body.repetition >= config.repetitions
        or body.warm != config.warm
        or not body.quality_passed
        or body.ended_at <= body.started_at
        or body.concurrency != config.concurrency
        or body.batch_policy != config.batch_policy
        or body.units != sum(spec["units"] for specs in row.prepared.values() for spec in specs)
    ):
        raise Problem(
            "BENCHMARK_MISMATCH", "Standalone record does not match experiment configuration", 422
        )
    if not any(
        body.adapter == stage.adapter
        and body.model_digest == stage.parameters.get("model_digest")
        and any(
            body.image_digest == v.image_digest and body.resource_limits == v.resource_requirements
            for v in stage.variants
        )
        for stage in plan.stages
        if not stage.depends_on
    ):
        raise Problem("BENCHMARK_MISMATCH", "Standalone runtime does not match the plan", 422)
    key = (row.id, node.id, body.repetition)
    stored = db.get(m.Standalone, key)
    record = payload(body)
    if stored and stored.record != record:
        raise conflict("Standalone repetition was already recorded with a different payload")
    if not stored:
        previous = db.scalar(
            select(m.Standalone)
            .where(
                m.Standalone.benchmark_id == row.id,
                m.Standalone.node_id == node.id,
            )
            .limit(1)
        )
        runtime = ("image_digest", "resource_limits", "adapter", "model_digest")
        if previous and any(record[k] != previous.record[k] for k in runtime):
            raise Problem(
                "BENCHMARK_MISMATCH", "Standalone repetitions must use a fixed runtime profile", 422
            )
        if row.status != "collecting":
            raise Problem("BENCHMARK_LOCKED", "Standalone collection has closed", 409)
        db.add(
            m.Standalone(
                benchmark_id=row.id,
                node_id=node.id,
                repetition=body.repetition,
                record=record,
                credential_id=actor.credential.id,
            )
        )
    return row


def tick(db, app):
    write_lock(db)
    for row in db.scalars(
        select(m.Benchmark)
        .where(m.Benchmark.status.in_(("collecting", "running")))
        .order_by(m.Benchmark.created_at)
        .limit(100)
    ):
        config = dto.BenchmarkCreate.model_validate(row.config)
        if row.status == "collecting":
            count = len(
                db.scalars(select(m.Standalone.node_id).where(m.Standalone.benchmark_id == row.id)).all()
            )
            if count < len(config.node_ids) * config.repetitions:
                continue
        runs = row.service_runs
        matrix = [[str(node)] for node in config.node_ids] + [[str(node) for node in config.node_ids]]
        repetitions = len(matrix) * config.repetitions
        if runs:
            job = db.get(m.Job, runs[-1]["job_id"])
            if not job.finished_at:
                continue
            unsuccessful = db.scalar(
                select(m.Task.id).where(m.Task.job_id == job.id, m.Task.state != "succeeded").limit(1)
            )
            if unsuccessful:
                row.status = "failed"
                continue
        if len(runs) >= repetitions:
            final = report(db, row)
            row.status = "complete" if final.pooled_units_per_second is not None else "failed"
            continue
        group, repetition = divmod(len(runs), config.repetitions)
        plan = dto.JobPlan.model_validate(row.plan).model_copy(
            update={"job_deadline_at": now(db) + timedelta(hours=2)}
        )
        job = create_job(
            db,
            app,
            db.get(m.User, row.owner_id),
            plan,
            row.prepared,
            purpose="benchmark",
            node_ids=matrix[group],
            concurrency=config.concurrency,
        )
        row.service_runs = [
            *runs,
            {
                "job_id": str(job.id),
                "node_ids": matrix[group],
                "repetition": repetition,
                "kind": "pool" if group == len(matrix) - 1 else "single_node_through_service",
            },
        ]
        row.status = "running"


def report(db, row):
    records = [
        dto.StandaloneRecord.model_validate(r.record)
        for r in db.scalars(
            select(m.Standalone)
            .where(m.Standalone.benchmark_id == row.id)
            .order_by(m.Standalone.node_id, m.Standalone.repetition)
        )
    ]
    config = dto.BenchmarkCreate.model_validate(row.config)
    profiles = {record.node_id: record for record in records}
    standalone_rates = {}
    for node in config.node_ids:
        rates = [r.units / (r.duration_ms / 1000) for r in records if r.node_id == node]
        if len(rates) == config.repetitions:
            standalone_rates[str(node)] = median(rates)
    service_runs, pool_rates = [], []
    for run in row.service_runs:
        job = db.get(m.Job, run["job_id"])
        accepted = db.execute(
            select(m.Task.units, m.Attempt.node_id, m.Attempt.id, m.Attempt.variant)
            .select_from(m.Acceptance)
            .join(m.Task, m.Task.id == m.Acceptance.task_id)
            .join(m.Attempt, m.Attempt.id == m.Acceptance.attempt_id)
            .where(m.Task.job_id == job.id)
        ).all()
        units = sum(a.units for a in accepted)
        participants = sorted({str(a.node_id) for a in accepted if a.units})
        duration = (job.finished_at - job.created_at).total_seconds() if job.finished_at else None
        rate = units / duration if duration and duration > 0 else None
        matched_runtime = all(
            a.node_id in profiles
            and profiles[a.node_id].image_digest == a.variant["image_digest"]
            and payload(profiles[a.node_id].resource_limits) == a.variant["resource_requirements"]
            for a in accepted
            if a.units
        )
        comparable = bool(
            rate
            and matched_runtime
            and set(participants) == set(run["node_ids"])
            and units == sum(spec["units"] for specs in row.prepared.values() for spec in specs)
        )
        if run["kind"] == "pool" and comparable:
            pool_rates.append(rate)
        metrics = db.scalars(
            select(m.Metric)
            .where(m.Metric.attempt_id.in_([a.id for a in accepted]))
            .order_by(m.Metric.id)
            .limit(10_000)
        ).all()
        service_runs.append(
            {
                **run,
                "accepted_units": units,
                "actual_nodes": participants,
                "duration_ms": int(duration * 1000) if duration else None,
                "comparable": comparable,
                "units_per_second": rate,
                "telemetry_source": "worker_reported",
                "utilization": [
                    {
                        "node_id": str(m.node_id),
                        "occurred_at": m.created_at.isoformat(),
                        "gap": m.gap,
                        **m.sample,
                    }
                    for m in metrics
                ],
            }
        )
    pooled = median(pool_rates) if len(pool_rates) == config.repetitions else None
    return dto.BenchmarkReport(
        id=row.id,
        config=config,
        config_digest=row.config_digest,
        manifest_digest=row.manifest_digest,
        status=row.status,
        standalone_results=records,
        service_job_ids=[r["job_id"] for r in row.service_runs],
        standalone_units_per_second=standalone_rates,
        pooled_units_per_second=pooled,
        service_runs=service_runs,
        efficiency_percent=pooled / sum(standalone_rates.values()) * 100
        if pooled and standalone_rates
        else None,
        speedup=pooled / max(standalone_rates.values()) if pooled and standalone_rates else None,
    )
