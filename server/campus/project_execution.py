"""Pure trusted compilation of project declarations into native container work.

No project code is imported or executed here. Dataset bytes are never opened;
the caller supplies account-owned, checksummed immutable artifact metadata.
"""

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import Field, ValidationError

from . import dto
from .db import digest, payload
from .errors import Problem
from .native_worker import resource_policy
from .project_schema import (
    MAX_INPUTS,
    ProjectConfig,
    ProjectStage,
    distinct_paths,
    matches_glob,
    validate_relative,
)

PROJECT_ADAPTERS = {"map": "campus.project-map", "reduce": "campus.project-reduce"}
RESOURCE_KEYS = ("cpu_millis", "memory_bytes", "disk_bytes", "gpu_slots", "vram_bytes")


def invalid(message, code="INVALID_PROJECT"):
    return Problem(code, message, 422)


class ManifestInput(dto.InputBinding):
    archive: bool = Field(default=False, strict=True)
    sha256: dto.SHA256
    size_bytes: int = Field(strict=True, ge=0, le=2**63 - 1)


def _config(config):
    try:
        # Revalidate even model instances: model_copy/update and mutation bypass
        # Pydantic field validators and must never weaken the execution contract.
        return ProjectConfig.model_validate(
            payload(config) if isinstance(config, ProjectConfig) else config
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise invalid("The project configuration is not a valid bounded contract") from exc


def _inputs(config, project_id, project_digest, inputs):
    try:
        project_id = UUID(str(project_id))
        if not isinstance(project_digest, str) or len(project_digest) != 64:
            raise ValueError("A project digest is required")
        if not isinstance(inputs, list) or len(inputs) > MAX_INPUTS:
            raise ValueError("The immutable input inventory exceeds its limit")
        checked = [ManifestInput.model_validate(item) for item in inputs]
        if not distinct_paths([item.mount_path for item in checked]):
            raise ValueError("Input paths overlap or contain case aliases")
        data = []
        archives = []
        for item in checked:
            if item.archive:
                if (
                    item.mount_path != "/workspace"
                    or item.artifact_id != project_id
                    or item.sha256 != project_digest
                ):
                    raise ValueError("Only the selected immutable project may mount at /workspace")
                archives.append(item)
                continue
            if not item.mount_path.startswith("/inputs/"):
                raise ValueError("Project data must mount below /inputs")
            relative = item.mount_path.removeprefix("/inputs/")
            validate_relative(relative, data=True)
            if not any(matches_glob(relative, pattern) for pattern in config.data):
                raise ValueError("Input inventory includes an undeclared data file")
            data.append(item)
        if len(archives) > 1:
            raise ValueError("The project archive may be selected only once")
        for pattern in config.data:
            if not any(matches_glob(item.mount_path[8:], pattern) for item in data):
                raise ValueError("Every declared data glob must select an immutable input")
        return checked, data
    except (ValidationError, ValueError, TypeError) as exc:
        raise invalid(
            "The immutable input inventory is unsafe, missing or inconsistent", "INVALID_INPUT"
        ) from exc


def _selected(stage, data):
    selected = []
    for pattern in stage.inputs:
        matches = [item for item in data if matches_glob(item.mount_path[8:], pattern)]
        if not matches:
            raise invalid(
                "Every stage input glob must select a declared immutable input", "INVALID_INPUT"
            )
        selected.extend(matches)
    unique = {item.mount_path: item for item in selected}
    if stage.kind == "map" and not unique:
        raise invalid("Map stages require at least one selected immutable file", "INVALID_INPUT")
    if len(unique) + 1 > MAX_INPUTS:
        raise invalid("A stage exceeds its project and data binding limit", "INVALID_INPUT")
    return [unique[path] for path in sorted(unique)]


def _resources(stage, accelerator):
    return dto.Resources(
        cpu_millis=stage.resources.cpu_millis,
        memory_bytes=stage.resources.memory_bytes,
        disk_bytes=stage.resources.disk_bytes,
        gpu_slots=int(accelerator == "cuda"),
        vram_bytes=stage.resources.vram_bytes if accelerator == "cuda" else 0,
    )


def _variants(stage, catalog):
    adapter = catalog.adapter(dto.AdapterRef(id=PROJECT_ADAPTERS[stage.kind], version="1"))
    variants = []
    wanted = {"cpu", "cuda"} if stage.accelerator == "auto" else {stage.accelerator}
    if not stage.resources.vram_bytes:
        wanted.discard("cuda")
    for image_id in adapter.image_ids:
        environment = catalog.environments[image_id]
        if environment.accelerator not in wanted:
            continue
        resources = _resources(stage, environment.accelerator)
        if not all(
            getattr(resources, key) <= getattr(environment.resource_requirements, key)
            for key in RESOURCE_KEYS
        ):
            continue
        variants.append(
            dto.Variant(
                variant_id=environment.variant_id,
                image_digest=environment.image_digest,
                platform=environment.platform,
                accelerator=environment.accelerator,
                resource_requirements=resources,
            )
        )
    if not variants or wanted - {variant.accelerator for variant in variants}:
        raise invalid("No registered environment satisfies the declared resources and accelerators")
    if len(variants) > 8 or len({variant.variant_id for variant in variants}) != len(variants):
        raise invalid("Registered project environments have ambiguous variant identities")
    return variants


def _stage(config_stage, project_id, data, catalog):
    selected = _selected(config_stage, data)
    result = dto.Stage(
        stage_id=config_stage.id,
        depends_on=config_stage.depends_on,
        adapter=dto.AdapterRef(id=PROJECT_ADAPTERS[config_stage.kind], version="1"),
        variants=_variants(config_stage, catalog),
        argv=config_stage.command,
        working_dir="/workspace",
        bindings=[dto.InputBinding(artifact_id=project_id, mount_path="/workspace", archive=True)]
        + [
            dto.InputBinding(artifact_id=item.artifact_id, mount_path=item.mount_path, archive=False)
            for item in selected
        ],
        outputs=[dto.Output(**payload(output)) for output in config_stage.outputs],
        task_deadline_ms=config_stage.timeout_seconds * 1000,
        validation_policy="structural",
        resume_policy="restart",
        parameters={"project_stage": payload(config_stage)},
    )
    for variant in result.variants:
        try:
            resource_policy(result, variant)
        except ValueError as exc:
            raise invalid(str(exc)) from exc
    return result


def compile_plan(
    config: ProjectConfig,
    project_id,
    project_digest,
    inputs,
    catalog,
    current_time,
    revision=1,
    *,
    manifest_digest=None,
) -> dto.JobPlan:
    """Compile an owned project and its ordered input manifest deterministically."""
    config = _config(config)
    checked, data = _inputs(config, project_id, project_digest, inputs)
    if not isinstance(current_time, datetime) or current_time.utcoffset() is None:
        raise invalid("Compilation requires an aware coordinator time")
    stages = [_stage(stage, project_id, data, catalog) for stage in config.stages]
    kind = "dag" if len(stages) > 1 else "map" if config.stages[0].kind == "map" else "single"
    try:
        return dto.JobPlan(
            plan_revision=revision,
            project_digest=project_digest,
            input_manifest_digest=manifest_digest
            or digest(
                {
                    "project_digest": project_digest,
                    "inputs": [payload(item) for item in checked],
                }
            ),
            execution_kind=kind,
            stages=stages,
            job_deadline_at=current_time + timedelta(hours=48),
            retry_policy=dto.RetryPolicy(),
            assumptions=[
                "Project commands run offline in protected containers with declared resources."
            ],
            evidence=[
                dto.Evidence(source="request", detail="Compiled from the reviewed project contract.")
            ],
        )
    except ValidationError as exc:
        raise invalid("Project metadata does not form a supported native job") from exc


def validate_project_plan(
    plan,
    config,
    project_id,
    project_digest,
    inputs,
    catalog,
    *,
    current_time=None,
    revision=None,
    manifest_digest=None,
) -> dto.JobPlan:
    """Reject every executable change; allow review explanations and a bounded deadline."""
    try:
        checked_plan = dto.JobPlan.model_validate(
            payload(plan) if isinstance(plan, dto.JobPlan) else plan
        )
        expected = compile_plan(
            config,
            project_id,
            project_digest,
            inputs,
            catalog,
            current_time or checked_plan.job_deadline_at - timedelta(hours=48),
            revision=revision if revision is not None else checked_plan.plan_revision,
            manifest_digest=manifest_digest,
        )
        for name in (
            "schema_version",
            "plan_revision",
            "project_digest",
            "input_manifest_digest",
            "execution_kind",
            "stages",
            "retry_policy",
        ):
            if getattr(checked_plan, name) != getattr(expected, name):
                raise ValueError("The plan changes the reviewed project contract")
        if checked_plan.questions:
            raise ValueError("A runnable project cannot have unresolved questions")
        if current_time is not None and not (
            current_time < checked_plan.job_deadline_at <= current_time + timedelta(hours=48)
        ):
            raise ValueError("Project deadline must be within the next 48 hours")
    except (ValidationError, ValueError, TypeError, Problem) as exc:
        raise invalid(
            "Plan does not match the reviewed immutable project contract", "INVALID_PLAN"
        ) from exc
    return plan if isinstance(plan, dto.JobPlan) else checked_plan


def _contract(stage):
    try:
        if set(stage.parameters) != {"project_stage"}:
            raise ValueError("Project stage parameters must contain only its trusted contract")
        config = ProjectStage.model_validate(stage.parameters["project_stage"])
        if (
            stage.stage_id != config.id
            or stage.adapter != dto.AdapterRef(id=PROJECT_ADAPTERS[config.kind], version="1")
            or stage.argv != config.command
            or stage.depends_on != config.depends_on
            or stage.working_dir != "/workspace"
            or stage.validation_policy != "structural"
            or stage.resume_policy != "restart"
            or stage.network_policy != "none"
            or stage.task_deadline_ms != config.timeout_seconds * 1000
            or [payload(output) for output in stage.outputs]
            != [payload(output) for output in config.outputs]
        ):
            raise ValueError("Native stage differs from its trusted project contract")
        if any(
            variant.accelerator
            not in ({"cpu", "cuda"} if config.accelerator == "auto" else {config.accelerator})
            or variant.resource_requirements != _resources(config, variant.accelerator)
            for variant in stage.variants
        ):
            raise ValueError("Native stage resources differ from the declared reservation")
        return config
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise invalid(
            "The native stage does not carry a valid project contract", "INVALID_PLAN"
        ) from exc


def prepare_tasks(plan, config, inputs, max_tasks):
    """Create one task per immutable map input, including maps with dependencies."""
    config = _config(config)
    if type(max_tasks) is not int or max_tasks < 1:
        raise invalid("Task quota must be a positive integer", "QUOTA_EXCEEDED")
    if [stage.stage_id for stage in plan.stages] != [stage.id for stage in config.stages]:
        raise invalid("Plan stages differ from the reviewed project", "INVALID_PLAN")
    project_ids = set()
    for stage in plan.stages:
        archives = [binding for binding in stage.bindings if binding.archive]
        if len(archives) != 1 or archives[0].mount_path != "/workspace":
            raise invalid("Every project stage must bind one selected project archive", "INVALID_PLAN")
        project_ids.add(archives[0].artifact_id)
    if len(project_ids) != 1:
        raise invalid("Project stages must share one immutable project archive", "INVALID_PLAN")
    _, data = _inputs(config, next(iter(project_ids)), plan.project_digest, inputs)
    result = {}
    count = 0
    for stage, config_stage in zip(plan.stages, config.stages, strict=True):
        if _contract(stage) != config_stage:
            raise invalid("Plan stage differs from the reviewed project", "INVALID_PLAN")
        selected = _selected(config_stage, data)
        expected = [(item.artifact_id, item.mount_path) for item in selected]
        actual = [(item.artifact_id, item.mount_path) for item in stage.bindings if not item.archive]
        if actual != expected:
            raise invalid("Plan input selection differs from the immutable inventory", "INVALID_PLAN")
        specs = _task_specs(config_stage, selected)
        count += len(specs)
        if count > max_tasks:
            raise invalid("The project exceeds the whole-job task limit", "QUOTA_EXCEEDED")
        result[stage.stage_id] = specs
    return result


def _task_specs(config, selected):
    if config.kind == "reduce":
        return [{"item_ids": [], "parameters": {}, "units": 1}]
    return [
        {
            "item_ids": [digest({"path": item.mount_path, "sha256": item.sha256})],
            "parameters": {"input": item.mount_path},
            "units": 1,
        }
        for item in selected
    ]


def _validated_bindings(stage, config):
    try:
        if not distinct_paths([binding.mount_path for binding in stage.bindings]):
            raise ValueError("Project stage bindings overlap or contain case aliases")
        archives = [binding for binding in stage.bindings if binding.archive]
        if len(archives) != 1 or archives[0].mount_path != "/workspace":
            raise ValueError("Every project stage requires exactly one immutable code archive")
        for binding in stage.bindings:
            if binding.archive:
                continue
            if not binding.mount_path.startswith("/inputs/"):
                raise ValueError("Data bindings must mount below /inputs")
            relative = binding.mount_path[8:]
            validate_relative(relative, data=True)
            if not any(matches_glob(relative, pattern) for pattern in config.inputs):
                raise ValueError("Data binding was not selected by the reviewed stage")
        return archives[0]
    except (ValueError, TypeError) as exc:
        raise invalid("Project stage contains unsafe or unselected bindings", "INVALID_PLAN") from exc


def prepare_stage_tasks(stage, inputs, max_tasks):
    """Bounded metadata-only splitter for the optional registered adapter process.

    The normal coordinator path uses ``prepare_tasks`` to validate the complete
    project. This entrypoint additionally verifies the exact stage binding subset;
    it does not need or load project bytes, executable files or a provider.
    """
    config = _contract(stage)
    archive = _validated_bindings(stage, config)
    if type(max_tasks) is not int or max_tasks < 1:
        raise invalid("Task quota must be a positive integer", "QUOTA_EXCEEDED")
    if not isinstance(inputs, list):
        raise invalid("Project inputs must be an immutable metadata inventory", "INVALID_INPUT")
    metadata = [payload(item) if isinstance(item, ManifestInput) else item for item in inputs]
    has_data = any(isinstance(item, dict) and not item.get("archive", False) for item in metadata)
    inventory_config = ProjectConfig(
        schema_version="1",
        name="Trusted stage inventory",
        data=["**"] if has_data else [],
        stages=[config.model_copy(update={"depends_on": []})],
    )
    project_digest = next(
        (
            item.get("sha256")
            for item in metadata
            if isinstance(item, dict)
            and item.get("archive") is True
            and str(item.get("artifact_id")) == str(archive.artifact_id)
        ),
        "0" * 64,
    )
    _, data = _inputs(inventory_config, archive.artifact_id, project_digest, metadata)
    selected = _selected(config, data)
    if [(item.artifact_id, item.mount_path) for item in selected] != [
        (binding.artifact_id, binding.mount_path) for binding in stage.bindings if not binding.archive
    ]:
        raise invalid("Stage binding subset differs from its immutable input inventory", "INVALID_PLAN")
    specs = _task_specs(config, selected)
    if len(specs) > max_tasks:
        raise invalid("The project stage exceeds its task quota", "QUOTA_EXCEEDED")
    return specs


def filter_bindings(stage, task_parameters):
    """Limit a map assignment to its selected file and the immutable code archive."""
    config = _contract(stage)
    if not isinstance(task_parameters, dict):
        raise invalid("Project task parameters must be a bounded object", "INVALID_PLAN")
    archive = _validated_bindings(stage, config)
    if config.kind == "reduce":
        if task_parameters:
            raise invalid("Reduce tasks cannot select arbitrary task inputs", "INVALID_PLAN")
        return list(stage.bindings)
    if set(task_parameters) != {"input"} or not isinstance(task_parameters["input"], str):
        raise invalid("Map tasks require exactly one trusted input parameter", "INVALID_PLAN")
    selected = [
        binding
        for binding in stage.bindings
        if not binding.archive and binding.mount_path == task_parameters["input"]
    ]
    if len(selected) != 1:
        raise invalid("Map task input was not selected by the reviewed stage", "INVALID_PLAN")
    return [archive] + selected


def resolve_argv(stage, task_parameters, accelerator):
    """Expand only the two declared tokens, with no shell or recursive formatting."""
    filter_bindings(stage, task_parameters)
    if accelerator not in {"cpu", "cuda"} or accelerator not in {
        variant.accelerator for variant in stage.variants
    }:
        raise invalid("Selected accelerator is outside the reviewed stage variants", "INVALID_PLAN")
    result = []
    for argument in stage.argv:
        value = argument.replace("{accelerator}", accelerator)
        if "{input}" in value:
            value = value.replace("{input}", task_parameters["input"])
        result.append(value)
    return result
