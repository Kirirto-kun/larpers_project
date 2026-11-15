from datetime import timedelta
from graphlib import CycleError, TopologicalSorter

from .. import models as m
from ..artifacts.storage import safe_relative, secret_path
from ..db import digest, now, payload
from ..errors import Problem
from ..jobs.scheduler import RESOURCE_KEYS
from ..native_worker import distinct_paths, input_path, resource_policy, validate_command


def invalid(message):
    return Problem("INVALID_PLAN", message, 422)


def owned_input(db, user, artifact_id, *, accepted=False):
    row = db.get(m.Artifact, artifact_id)
    permitted = row and row.pool_id == user.pool_id and row.owner_id == user.id
    if not permitted or not (row.kind in {"input", "project"} or accepted and row.kind == "output"):
        raise Problem("ARTIFACT_NOT_READY", "The selected input is not available to this user", 409)
    return row


def manifest(db, user, request):
    items = []
    for item in request.inputs:
        row = owned_input(db, user, item.artifact_id)
        items.append({**payload(item), "sha256": row.sha256, "size_bytes": row.size_bytes})
    project = None
    if request.project_artifact_id:
        row = owned_input(db, user, request.project_artifact_id)
        if row.kind != "project":
            raise invalid("The project artifact must be a validated ZIP")
        project = row.sha256
    return project, digest({"project_digest": project, "inputs": items})


def validate_mount(binding, row):
    if binding.archive and row.kind != "project":
        raise invalid("Only a validated project ZIP may be expanded")
    if binding.archive and binding.mount_path == "/workspace":
        return
    prefix = "/workspace/" if binding.archive else "/inputs/"
    if not binding.mount_path.startswith(prefix):
        raise invalid("Input mounts must use their declared sandbox directory")
    path = safe_relative(binding.mount_path.removeprefix(prefix))
    if secret_path(path) or binding.mount_path.startswith("/inputs/stages/"):
        raise invalid("Input mount uses a reserved path")


def validate_plan(db, app, user, plan, request, *, server_evidence=False):
    project, manifest_sha = manifest(db, user, request)
    if request.project_config:
        from ..project_execution import validate_project_plan

        metadata = []
        for item in request.inputs:
            row = owned_input(db, user, item.artifact_id)
            metadata.append({**payload(item), "sha256": row.sha256, "size_bytes": row.size_bytes})
        validate_project_plan(
            plan,
            request.project_config,
            request.project_artifact_id,
            project,
            metadata,
            app.catalog,
            current_time=now(db),
            manifest_digest=manifest_sha,
        )
    elif any(
        stage.adapter.id in {"campus.project-map", "campus.project-reduce"} for stage in plan.stages
    ):
        raise invalid("Distributed project stages require their explicit project configuration")
    if plan.input_manifest_digest != manifest_sha or plan.project_digest != project:
        raise invalid("Plan does not match the selected immutable inputs")
    current = now(db)
    if not current < plan.job_deadline_at <= current + timedelta(hours=48):
        raise invalid("Job deadline must be in the next 48 hours")
    if plan.questions:
        raise invalid("A runnable plan cannot have unresolved questions")
    stages = {stage.stage_id: stage for stage in plan.stages}
    if len(stages) != len(plan.stages) or (plan.execution_kind != "dag" and len(stages) != 1):
        raise invalid("Stage IDs must be unique and match the execution kind")
    if any(
        set(s.depends_on) - stages.keys() or len(s.depends_on) != len(set(s.depends_on))
        for s in plan.stages
    ):
        raise invalid("Plan refers to an unknown or duplicate dependency")
    try:
        tuple(TopologicalSorter({s.stage_id: s.depends_on for s in plan.stages}).static_order())
    except CycleError as exc:
        raise invalid("Stage dependencies must be acyclic") from exc
    if request.recipe_id and request.recipe_id not in app.catalog.recipes:
        raise invalid("Requested recipe is not registered")
    for item in plan.evidence:
        if item.source not in {*request.selected_files, "request", "catalog", "inputs", "probe"}:
            raise invalid("Plan evidence must refer to inspected inputs or the trusted catalog")
        if item.source == "probe" and not server_evidence:
            raise invalid("Only the server can attach probe evidence")
    selected = {(item.artifact_id, item.mount_path, item.archive) for item in request.inputs}
    for stage in plan.stages:
        adapter = app.catalog.adapter(stage.adapter)
        if request.recipe_id and app.catalog.recipes[request.recipe_id].adapter != stage.adapter:
            raise invalid("Plan does not use the requested recipe adapter")
        if plan.execution_kind == "map" and not adapter.parallel:
            raise invalid("Map requires a registered parallel adapter")
        if stage.validation_policy not in adapter.policies:
            raise invalid("Validation policy is not supported by the adapter")
        if stage.resume_policy == "checkpoint" and not adapter.checkpoints:
            raise invalid("Adapter does not support complete checkpoints")
        if not stage.argv[0] or any("\x00" in arg for arg in stage.argv):
            raise invalid("argv must be a non-empty executable and literal arguments")
        if stage.working_dir != "/workspace":
            if not stage.working_dir.startswith("/workspace/"):
                raise invalid("Working directory must be under /workspace")
            safe_relative(stage.working_dir.removeprefix("/workspace/"))
        if (
            stage.adapter.id in {"campus.python", "campus.project-map", "campus.project-reduce"}
            and request.project_artifact_id
        ):
            archives = [binding for binding in stage.bindings if binding.archive]
            if (
                len(archives) != 1
                or archives[0].artifact_id != request.project_artifact_id
                or archives[0].mount_path != "/workspace"
            ):
                raise invalid(
                    "Each Python stage must bind the selected project ZIP exactly once "
                    "at /workspace with archive=true; add this binding before running"
                )
        if not distinct_paths([b.mount_path for b in stage.bindings]):
            raise invalid("Duplicate input mount")
        for binding in stage.bindings:
            row = owned_input(db, user, binding.artifact_id)
            if (binding.artifact_id, binding.mount_path, binding.archive) not in selected and not (
                binding.artifact_id == request.project_artifact_id and binding.archive
            ):
                raise invalid("Plan tries to mount an input the user did not select")
            validate_mount(binding, row)
        paths = [b.mount_path for b in stage.bindings]
        if any(a.startswith(b + "/") for a in paths for b in paths if a != b):
            raise invalid("Input mounts overlap")
        if not distinct_paths([o.path for o in stage.outputs]):
            raise invalid("Output paths must be unique")
        for output in stage.outputs:
            if (
                secret_path(safe_relative(output.path))
                or output.max_bytes > app.settings.upload_limit_bytes
            ):
                raise invalid("Output is outside the declared storage policy")
        variants = [v.variant_id for v in stage.variants]
        if len(set(variants)) != len(variants):
            raise invalid("Variant IDs must be unique")
        environments = [app.catalog.environments[key] for key in adapter.image_ids]
        for variant in stage.variants:
            validate_variant(variant, environments, request.accelerator)
            if stage.adapter.id in {"campus.python", "campus.project-map", "campus.project-reduce"}:
                try:
                    validate_command(stage)
                    resource_policy(stage, variant)
                    for binding in stage.bindings:
                        input_path(binding)
                except ValueError as exc:
                    raise invalid(str(exc)) from exc
    return plan


def validate_variant(variant, environments, required_accelerator):
    if required_accelerator and required_accelerator != variant.accelerator:
        raise invalid("Variant contradicts the requested accelerator")
    matches = [
        e
        for e in environments
        if (e.image_digest, e.platform, e.accelerator)
        == (variant.image_digest, variant.platform, variant.accelerator)
    ]
    if not any(
        all(
            getattr(variant.resource_requirements, k) <= getattr(e.resource_requirements, k)
            for k in RESOURCE_KEYS
        )
        for e in matches
    ):
        raise Problem(
            "UNSUPPORTED_ENVIRONMENT", "Variant requires an unregistered image or resource profile", 422
        )
    limits = variant.resource_requirements
    if (variant.accelerator == "cpu" and (limits.gpu_slots or limits.vram_bytes)) or (
        variant.accelerator == "cuda" and (limits.gpu_slots != 1 or not limits.vram_bytes)
    ):
        raise invalid("Accelerator and resource limits disagree")
