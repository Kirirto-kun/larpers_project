from datetime import timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select

from .. import dto
from .. import models as m
from ..db import digest, now, payload, write_lock
from ..errors import Problem
from ..events import emit
from ..jobs.adapters import prepare, read_json_result
from ..jobs.service import cancel, create_job, owned_preview
from ..project_execution import compile_plan, prepare_tasks
from .inspection import inspect
from .project_reviews import cached_review, review_key
from .validation import manifest, validate_plan


def describe(row):
    return dto.Preview(
        id=row.id,
        state=row.state,
        plan_revision=row.revision,
        plan=row.plan,
        plan_digest=row.plan_digest,
        question=row.question,
        error_code=row.error_code,
        error_message=row.error_message,
    )


def plan_job(db, app, actor, body):
    user = actor.member()
    write_lock(db)
    if not body.prepared_plan and not body.prompt and not body.project_config:
        raise Problem("PROMPT_REQUIRED", "A prompt or a prepared plan is required", 422)
    if body.prepared_plan and (body.selected_files or body.share_with_llm):
        raise Problem("INVALID_REQUEST", "A prepared plan does not share data with the planner", 422)
    if body.selected_files and not body.share_with_llm:
        raise Problem("SHARING_REQUIRED", "Confirm sharing selected project files", 422)
    current = now(db)
    count = db.scalar(
        select(func.count())
        .select_from(m.Preview)
        .where(
            m.Preview.owner_id == user.id,
            m.Preview.created_at > current - timedelta(minutes=1),
        )
    )
    if count >= 20:
        raise Problem("RATE_LIMITED", "Preview rate limit exceeded", 429, retryable=True)
    manifest(db, user, body)
    row = m.Preview(
        owner_id=user.id,
        state="inspecting",
        request=payload(body),
        deadline_at=current + timedelta(seconds=app.settings.llm_planning_seconds),
    )
    db.add(row)
    db.flush()
    announce(db, row, user)
    return describe(row)


def answer(db, app, actor, preview_id, body):
    write_lock(db)
    row = owned_preview(db, actor, preview_id)
    if (
        row.state != "needs_input"
        or row.revision != body.plan_revision
        or row.question["question_id"] != body.question_id
    ):
        raise Problem("STALE_PREVIEW", "This question no longer belongs to the active revision", 409)
    row.revision += 1
    row.answer = payload(body)
    row.question = None
    row.probe_job_id = None
    row.claim_id = row.claim_expires_at = None
    row.state = "inspecting"
    row.deadline_at = now(db) + timedelta(seconds=app.settings.llm_planning_seconds)
    announce(db, row, db.get(m.User, row.owner_id))
    return describe(row)


def cancel_preview(db, actor, preview_id):
    write_lock(db)
    row = owned_preview(db, actor, preview_id)
    if row.state not in {"cancelled", "failed"}:
        row.state = "cancelled"
        if row.probe_job_id:
            cancel(db, db.get(m.Job, row.probe_job_id))
        announce(db, row, db.get(m.User, row.owner_id))
    return describe(row)


def announce(db, row, user):
    emit(
        db,
        user.pool_id,
        "preview",
        row.id,
        "preview_updated",
        state=row.state,
        plan_revision=row.revision,
    )


def claim(db):
    write_lock(db)
    current = now(db)
    rows = db.scalars(
        select(m.Preview)
        .where(
            m.Preview.state.in_(("inspecting", "planning", "validating")),
            (m.Preview.claim_expires_at.is_(None)) | (m.Preview.claim_expires_at <= current),
        )
        .order_by(m.Preview.created_at)
        .limit(100)
    )
    for row in rows:
        user = db.get(m.User, row.owner_id)
        if row.deadline_at <= current:
            set_error(db, row, user, Problem("PLANNER_TIMEOUT", "Planning deadline passed", 503))
            continue
        if row.probe_job_id:
            job = db.get(m.Job, row.probe_job_id)
            if job.finished_at is None:
                continue
        row.claim_id = uuid4()
        row.claim_expires_at = min(row.deadline_at, current + timedelta(seconds=15))
        row.state = "planning"
        announce(db, row, user)
        return row.id, row.claim_id, row.revision


def context_for(db, app, row, user, request):
    context = inspect(db, app, user, request)
    current = now(db)
    context.update(
        {
            "prompt": request.prompt,
            "answer": row.answer,
            "plan_revision": row.revision,
            "current_time": current.isoformat(),
            "default_job_deadline_at": (current + timedelta(minutes=30)).isoformat(),
            "catalog": app.catalog.planner_context(),
            "project_binding": {
                "artifact_id": str(request.project_artifact_id),
                "mount_path": "/workspace",
                "archive": True,
            }
            if request.project_artifact_id
            else None,
            "allowed_evidence_sources": sorted(
                {*request.selected_files, "request", "catalog", "inputs"}
            ),
        }
    )
    if request.project_config:
        context["project_config"] = payload(request.project_config)
    if row.probe_job_id:
        results = list(
            db.scalars(select(m.Acceptance).join(m.Task).where(m.Task.job_id == row.probe_job_id))
        )
        if len(results) != 1:
            raise Problem("PROBE_FAILED", "The registered probe did not return accepted evidence", 422)
        evidence = {}
        for output in results[0].result["outputs"]:
            artifact = db.get(m.Artifact, output["artifact_id"])
            if artifact.size_bytes > 16_384:
                raise Problem("PROBE_LIMIT", "Probe evidence exceeds the inspection limit", 422)
            evidence[output["path"]] = read_json_result(app.storage.blob(artifact.sha256), 16_384)
        context["probe"] = {
            "job_id": str(row.probe_job_id),
            "verification": results[0].report["level"],
            "outputs": evidence,
        }
        context["allowed_evidence_sources"].append("probe")
    return context


def prepare_preview(app, claim_data):
    preview_id, _, revision = claim_data
    with app.sessions() as db:
        row = db.get(m.Preview, preview_id)
        user = db.get(m.User, row.owner_id)
        request = dto.PreviewRequest.model_validate(row.request)
        context = context_for(db, app, row, user, request)
        budget = (row.deadline_at - now(db)).total_seconds()
        project_plan = None
        cached = None
        cache_key = None
        if request.project_config:
            inventory = input_inventory(db, request)
            project_plan = compile_plan(
                request.project_config,
                request.project_artifact_id,
                context["project_digest"],
                inventory,
                app.catalog,
                now(db),
                revision=revision,
                manifest_digest=context["input_manifest_digest"],
            )
            project_plan.job_deadline_at = now(db) + timedelta(minutes=30)
            cache_key = review_key(app.settings, context)
            cached = cached_review(db, user, cache_key)
    plan = request.prepared_plan
    approved_review = None
    if plan is None:
        if cached is None:
            proposal = app.planner(app.settings, context, budget)
            if proposal.question:
                return {"question": proposal.question}
            if proposal.probe:
                if request.project_config:
                    raise Problem(
                        "INVALID_PLAN", "A project contract cannot request an unrelated probe", 422
                    )
                return {"probe": proposal.probe}
        if request.project_config:
            review = cached if cached is not None else proposal.project_review
            if review is None:
                raise Problem(
                    "INVALID_PLAN", "A project requires a bounded review of its declared contract", 422
                )
            if not review.approved:
                raise Problem("PROJECT_REVIEW_REJECTED", review.summary[:256], 422)
            allowed = set(context["allowed_evidence_sources"])
            if any(item.source not in allowed for item in review.evidence):
                raise Problem("INVALID_PLAN", "Project review cites uninspected evidence", 422)
            plan = project_plan
            plan.assumptions = [*plan.assumptions, review.summary[:256], *review.warnings][:24]
            plan.evidence = [*plan.evidence, *review.evidence][:64]
            approved_review = review if cached is None else None
        else:
            if proposal.project_review:
                raise Problem("INVALID_PLAN", "A review alone cannot execute an ordinary request", 422)
            plan = proposal.plan
    if plan is None:
        raise Problem("INVALID_PLAN", "The planner did not return an executable action", 422)
    if plan.plan_revision != revision:
        raise Problem("INVALID_PLAN", "Planner returned the wrong revision", 422)
    if plan.questions:
        return {"question": plan.questions[0]}
    with app.sessions.begin() as db:
        row = db.get(m.Preview, preview_id)
        user = db.get(m.User, row.owner_id)
        write_lock(db)
        db.refresh(row)
        if row.state != "planning" or row.claim_id != claim_data[1] or row.claim_expires_at <= now(db):
            return {}
        validate_plan(db, app, user, plan, request, server_evidence=row.probe_job_id is not None)
        row.state = "validating"
        announce(db, row, user)
        adapter_inputs = input_inventory(db, request)
        budget = (row.deadline_at - now(db)).total_seconds()
    tasks = (
        prepare_tasks(plan, request.project_config, adapter_inputs, app.settings.max_tasks_per_job)
        if request.project_config
        else prepare(
            plan, adapter_inputs, app.catalog, app.settings.max_tasks_per_job, budget_seconds=budget
        )
    )
    return {"plan": plan, "prepared": tasks, "project_review": approved_review, "review_key": cache_key}


def input_inventory(db, request):
    result = []
    for item in request.inputs:
        artifact = db.get(m.Artifact, item.artifact_id)
        result.append({**payload(item), "sha256": artifact.sha256, "size_bytes": artifact.size_bytes})
    return result


def launch_probe(db, app, row, user, proposal):
    if row.probe_job_id:
        raise Problem("INVALID_PROBE", "A revision can run only one registered probe", 422)
    adapter = app.catalog.adapter(
        dto.AdapterRef(id=proposal.adapter_id, version=proposal.adapter_version)
    )
    stage = adapter.probe_stage
    if (
        not stage
        or stage.adapter != adapter.ref
        or stage.bindings
        or stage.depends_on
        or stage.resume_policy == "checkpoint"
        or stage.validation_policy == "structural"
    ):
        raise Problem("INVALID_PROBE", "The requested adapter has no eligible isolated probe", 422)
    original = dto.PreviewRequest.model_validate(row.request)
    samples = [
        binding
        for binding in original.inputs
        if not binding.archive and db.get(m.Artifact, binding.artifact_id).size_bytes <= 65_536
    ]
    samples = samples[:1]
    stage = stage.model_copy(update={"bindings": samples})
    request = dto.PreviewRequest(prompt="Registered capability probe", inputs=samples)
    project, manifest_sha = manifest(db, user, request)
    deadline = min(row.deadline_at, now(db) + timedelta(seconds=45))
    plan = dto.JobPlan(
        plan_revision=row.revision,
        project_digest=project,
        input_manifest_digest=manifest_sha,
        execution_kind="single",
        stages=[stage.model_copy(update={"task_deadline_ms": min(stage.task_deadline_ms, 15_000)})],
        job_deadline_at=deadline,
        retry_policy=dto.RetryPolicy(max_attempts=1),
    )
    validate_plan(db, app, user, plan, request)
    row.probe_job_id = create_job(
        db,
        app,
        user,
        plan,
        {stage.stage_id: [{"item_ids": [], "parameters": {}, "units": 0}]},
        purpose="probe",
        preview_id=row.id,
    ).id
    row.state = "validating"


def set_error(db, row, user, error):
    row.state = "failed"
    row.error_code, row.error_message = error.code, error.message
    if row.probe_job_id:
        cancel(db, db.get(m.Job, row.probe_job_id))
    announce(db, row, user)


def finish(db, app, claim_data, result=None, error=None):
    write_lock(db)
    preview_id, claim_id, revision = claim_data
    row = db.get(m.Preview, preview_id)
    if (
        row.state not in {"planning", "validating"}
        or row.claim_id != claim_id
        or row.revision != revision
        or row.claim_expires_at <= now(db)
    ):
        return
    user = db.get(m.User, row.owner_id)
    row.claim_id = row.claim_expires_at = None
    if row.deadline_at <= now(db):
        error = Problem("PLANNER_TIMEOUT", "Planning deadline passed", 503)
    if error:
        set_error(db, row, user, error)
        return
    if result.get("question"):
        row.question = payload(result["question"])
        row.state = "needs_input"
    elif result.get("probe"):
        try:
            launch_probe(db, app, row, user, result["probe"])
        except Problem as exc:
            set_error(db, row, user, exc)
            return
    elif result.get("plan"):
        row.plan = payload(result["plan"])
        if row.probe_job_id:
            row.plan["evidence"] = [
                *row.plan["evidence"][:63],
                {
                    "source": "probe",
                    "detail": f"Verified probe job {row.probe_job_id}",
                },
            ]
        row.plan_digest = digest(row.plan)
        row.prepared = result["prepared"]
        row.state = "ready"
        if result.get("project_review") is not None:
            emit(
                db,
                user.pool_id,
                "preview",
                row.id,
                "project_review_accepted",
                cache_key=result["review_key"],
                owner_id=str(user.id),
            )
    else:
        return
    announce(db, row, user)


def renew_claim(db, claim_data):
    write_lock(db)
    row = db.get(m.Preview, claim_data[0])
    current = now(db)
    if (
        row.claim_id == claim_data[1]
        and row.state in {"planning", "validating"}
        and row.claim_expires_at > current
        and row.deadline_at > current
    ):
        row.claim_expires_at = min(row.deadline_at, current + timedelta(seconds=15))


def process(app, claim_data):
    try:
        result = prepare_preview(app, claim_data)
    except Problem as exc:
        with app.sessions.begin() as db:
            finish(db, app, claim_data, error=exc)
    except ValidationError:
        with app.sessions.begin() as db:
            finish(
                db,
                app,
                claim_data,
                error=Problem("INVALID_PLAN", "The plan could not be validated", 422),
            )
    else:
        with app.sessions.begin() as db:
            finish(db, app, claim_data, result=result)
