from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError


def project_value():
    return {
        "schema_version": "1",
        "name": "Independent document batches",
        "data": ["data/**/*.json"],
        "stages": [
            {
                "id": "summarize",
                "kind": "map",
                "inputs": ["data/**/*.json"],
                "command": ["python", "worker.py", "--input", "{input}", "--device={accelerator}"],
                "outputs": [{"path": "partial.json", "max_bytes": 4096}],
            },
            {
                "id": "merge",
                "kind": "reduce",
                "depends_on": ["summarize"],
                "command": ["python", "merge.py", "--accelerator", "{accelerator}"],
                "outputs": [{"path": "result.json", "max_bytes": 8192}],
            },
        ],
    }


def test_project_defaults_reserve_bounded_cpu_and_cuda_work():
    from campus.project_schema import ProjectConfig

    config = ProjectConfig.model_validate(project_value())
    stage = config.stages[0]
    assert stage.timeout_seconds == 300
    assert stage.accelerator == "auto"
    assert stage.resources.model_dump() == {
        "cpu_millis": 1000,
        "memory_bytes": 536870912,
        "disk_bytes": 268435456,
        "vram_bytes": 1073741824,
    }
    assert stage.outputs[0].required is True
    assert config.stages[1].inputs == []


@pytest.mark.parametrize("path", ["stages", "Stages/x"])
def test_project_outputs_reject_the_reserved_intermediate_download_namespace(path):
    from campus.project_schema import ProjectConfig

    value = project_value()
    value["stages"][0]["outputs"][0]["path"] = path
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(value)


def test_project_output_named_stages_json_does_not_collide_with_download_namespace():
    from campus.project_schema import ProjectConfig

    value = project_value()
    value["stages"][0]["outputs"][0]["path"] = "stages.json"
    config = ProjectConfig.model_validate(value)
    assert config.stages[0].outputs[0].path == "stages.json"


@pytest.mark.parametrize(
    "path",
    [
        "../data/*.json",
        "/data/*.json",
        "data/../*.json",
        "data\\*.json",
        "C:/*.json",
        "data//*.json",
        "data/.env",
        ".ssh/*",
        "data/credentials.json",
        "data/NUL.json",
        "data/file. ",
        "stages/*.json",
        "project.zip",
        "data/\x00.json",
        "data/<bad>.json",
    ],
)
def test_project_rejects_nonportable_secret_or_reserved_data_globs(path):
    from campus.project_schema import ProjectConfig

    value = project_value()
    value["data"] = [path]
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(value)


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value["stages"][0].update(id="Merge"),
        lambda value: value["stages"][0].update(depends_on=["merge"]),
        lambda value: value["stages"][1].update(depends_on=["absent"]),
        lambda value: value["stages"][1].update(depends_on=["summarize", "summarize"]),
        lambda value: value["stages"][0].update(inputs=[]),
        lambda value: value["stages"][1].update(command=["python", "merge.py", "{input}"]),
        lambda value: value["stages"][0].update(command=["python", "{password}"]),
        lambda value: value["stages"][0].update(command=["python", "{input.__class__}"]),
        lambda value: value["stages"][0].update(command=["", "worker.py"]),
        lambda value: value["stages"][0].update(command=["python", "bad\x00arg"]),
        lambda value: value["stages"][0].update(timeout_seconds=86401),
        lambda value: value["stages"][0].update(resources={"cpu_millis": 4001}),
        lambda value: value["stages"][0].update(resources={"memory_bytes": 33554431}),
        lambda value: value["stages"][0].update(resources={"disk_bytes": 536870913}),
        lambda value: value["stages"][0].update(resources={"vram_bytes": 4294967297}),
        lambda value: value["stages"][0].update(resources={"cpu_millis": True}),
        lambda value: value["stages"][0].update(outputs=[{"path": ".env", "max_bytes": 10}]),
        lambda value: value["stages"][0].update(
            outputs=[{"path": "out", "max_bytes": 10}, {"path": "OUT/x.json", "max_bytes": 10}]
        ),
        lambda value: value["stages"][0].update(
            outputs=[{"path": "result.json", "max_bytes": 536870912}]
        ),
        lambda value: value.update(data=["data/*.json", "DATA/*.json"]),
        lambda value: value.update(unknown="not part of the contract"),
    ],
)
def test_project_rejects_ambiguous_dags_commands_and_unbounded_resources(change):
    from campus.project_schema import ProjectConfig

    value = deepcopy(project_value())
    change(value)
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(value)


def test_project_globs_cover_nested_files_without_crossing_single_star_segments():
    from campus.project_schema import matches_glob

    assert matches_glob("data/a.json", "data/**/*.json")
    assert matches_glob("data/nested/a.json", "data/**/*.json")
    assert matches_glob("data/nested/a.json", "data/**")
    assert matches_glob("data/a1.json", "data/a[0-9].json")
    assert not matches_glob("data/nested/a.json", "data/*.json")
    assert not matches_glob("data/A.JSON", "data/*.json")


def execution_context():
    from datetime import UTC, datetime
    from pathlib import Path
    from uuid import UUID

    from campus import dto
    from campus.catalog import Adapter, Catalog
    from campus.project_schema import ProjectConfig

    catalog = Catalog(Path(__file__).resolve().parents[1] / "campus/data/product-catalog.json")
    for kind in ("map", "reduce"):
        ref = dto.AdapterRef(id=f"campus.project-{kind}", version="1")
        catalog.adapters[(ref.id, ref.version)] = Adapter(
            ref=ref,
            image_ids=["python-cpu-amd64", "python-cpu-arm64", "python-cuda-amd64"],
            policies=["structural"],
            parallel=kind == "map",
            command=["python", "-m", "campus.project_adapter"],
        )
    value = project_value()
    value["stages"][0]["inputs"] = ["data/train/*.json"]
    value["stages"].append(
        {
            "id": "evaluate",
            "kind": "map",
            "depends_on": ["merge"],
            "inputs": ["data/test/*.json"],
            "command": ["python", "evaluate.py", "--input={input}", "--accelerator={accelerator}"],
            "outputs": [{"path": "predictions.json", "max_bytes": 8192}],
        }
    )
    inputs = [
        {
            "artifact_id": str(UUID(int=index + 1)),
            "mount_path": "/inputs/" + path,
            "archive": False,
            "sha256": str(index + 1) * 64,
            "size_bytes": 100 + index,
        }
        for index, path in enumerate(
            ["data/train/b.json", "data/train/a.json", "data/test/b.json", "data/test/a.json"]
        )
    ]
    return {
        "config": ProjectConfig.model_validate(value),
        "project_id": UUID(int=100),
        "project_digest": "f" * 64,
        "inputs": inputs,
        "catalog": catalog,
        "current_time": datetime(2026, 11, 15, tzinfo=UTC),
    }


def test_compiler_binds_only_each_stage_selected_immutable_files_and_catalog_images():
    from campus.db import digest
    from campus.project_execution import compile_plan

    context = execution_context()
    plan = compile_plan(**context)
    assert plan.execution_kind == "dag"
    assert plan.plan_revision == 1
    assert plan.project_digest == "f" * 64
    assert plan.input_manifest_digest == digest(
        {"project_digest": "f" * 64, "inputs": context["inputs"]}
    )
    assert [stage.stage_id for stage in plan.stages] == ["summarize", "merge", "evaluate"]
    assert plan.stages[0].adapter.id == "campus.project-map"
    assert plan.stages[1].adapter.id == "campus.project-reduce"
    assert plan.stages[2].depends_on == ["merge"]
    assert [binding.mount_path for binding in plan.stages[0].bindings] == [
        "/workspace",
        "/inputs/data/train/a.json",
        "/inputs/data/train/b.json",
    ]
    assert [binding.mount_path for binding in plan.stages[1].bindings] == ["/workspace"]
    assert [binding.mount_path for binding in plan.stages[2].bindings] == [
        "/workspace",
        "/inputs/data/test/a.json",
        "/inputs/data/test/b.json",
    ]
    assert plan.stages[0].argv == ["python", "worker.py", "--input", "{input}", "--device={accelerator}"]
    assert plan.stages[0].parameters["project_stage"]["id"] == "summarize"
    assert {variant.accelerator for variant in plan.stages[0].variants} == {"cpu", "cuda"}
    for variant in plan.stages[0].variants:
        assert variant.resource_requirements.cpu_millis == 1000
        assert variant.resource_requirements.vram_bytes == (
            1073741824 if variant.accelerator == "cuda" else 0
        )
        assert variant.resource_requirements.gpu_slots == int(variant.accelerator == "cuda")
        assert variant.image_digest in {
            environment.image_digest for environment in context["catalog"].environments.values()
        }


def test_compiler_accepts_the_selected_archive_in_manifest_without_duplicate_binding():
    from campus.db import digest
    from campus.project_execution import compile_plan

    context = execution_context()
    context["inputs"].insert(
        0,
        {
            "artifact_id": str(context["project_id"]),
            "mount_path": "/workspace",
            "archive": True,
            "sha256": "f" * 64,
            "size_bytes": 99,
        },
    )
    plan = compile_plan(**context)
    assert len(plan.stages[0].bindings) == 3
    assert plan.input_manifest_digest == digest(
        {"project_digest": "f" * 64, "inputs": context["inputs"]}
    )
    assert compile_plan(**context, manifest_digest="a" * 64).input_manifest_digest == "a" * 64


@pytest.mark.parametrize("accelerator,expected", [("cpu", {"cpu"}), ("cuda", {"cuda"})])
def test_compiler_preserves_explicit_accelerator_selection(accelerator, expected):
    from campus.project_execution import compile_plan
    from campus.project_schema import ProjectConfig

    context = execution_context()
    value = context["config"].model_dump()
    value["stages"][0]["accelerator"] = accelerator
    context["config"] = ProjectConfig.model_validate(value)
    plan = compile_plan(**context)
    assert {variant.accelerator for variant in plan.stages[0].variants} == expected


def test_preparer_splits_dependent_maps_into_individual_selected_file_tasks():
    from campus.project_execution import compile_plan, prepare_tasks

    context = execution_context()
    plan = compile_plan(**context)
    tasks = prepare_tasks(plan, context["config"], context["inputs"], max_tasks=5)
    assert [task["parameters"] for task in tasks["summarize"]] == [
        {"input": "/inputs/data/train/a.json"},
        {"input": "/inputs/data/train/b.json"},
    ]
    assert [task["parameters"] for task in tasks["evaluate"]] == [
        {"input": "/inputs/data/test/a.json"},
        {"input": "/inputs/data/test/b.json"},
    ]
    assert tasks["merge"] == [{"item_ids": [], "parameters": {}, "units": 1}]
    assert all(task["units"] == 1 and len(task["item_ids"]) == 1 for task in tasks["evaluate"])
    assert len({task["item_ids"][0] for task in tasks["evaluate"]}) == 2


def test_argv_resolution_is_literal_and_bindings_filter_to_the_task_input():
    from campus.project_execution import compile_plan, filter_bindings, resolve_argv

    context = execution_context()
    stage = compile_plan(**context).stages[0]
    parameters = {"input": "/inputs/data/train/a.json"}
    assert resolve_argv(stage, parameters, "cuda") == [
        "python",
        "worker.py",
        "--input",
        "/inputs/data/train/a.json",
        "--device=cuda",
    ]
    assert [binding.mount_path for binding in filter_bindings(stage, parameters)] == [
        "/workspace",
        "/inputs/data/train/a.json",
    ]
    assert stage.argv[-1] == "--device={accelerator}"
    assert len(stage.bindings) == 3


@pytest.mark.parametrize(
    "change",
    [
        lambda plan: plan.stages[0].argv.append("--upload-secrets"),
        lambda plan: plan.stages[2].depends_on.clear(),
        lambda plan: setattr(plan.stages[0].outputs[0], "path", "changed.json"),
        lambda plan: plan.stages[0].bindings.pop(),
        lambda plan: plan.stages[0].bindings.append(plan.stages[2].bindings[1]),
        lambda plan: plan.stages[0].parameters["project_stage"].update(inputs=["data/test/*.json"]),
        lambda plan: setattr(plan.stages[0].variants[0], "accelerator", "cuda"),
        lambda plan: setattr(plan.stages[0].variants[0].resource_requirements, "cpu_millis", 3000),
        lambda plan: setattr(plan, "project_digest", "a" * 64),
        lambda plan: setattr(plan, "input_manifest_digest", "a" * 64),
        lambda plan: setattr(plan, "execution_kind", "single"),
        lambda plan: plan.stages.reverse(),
    ],
)
def test_plan_validation_rejects_changes_to_the_executable_contract(change):
    from campus.errors import Problem
    from campus.project_execution import compile_plan, validate_project_plan

    context = execution_context()
    plan = compile_plan(**context)
    change(plan)
    with pytest.raises(Problem) as failure:
        validate_project_plan(plan, **context)
    assert failure.value.code == "INVALID_PLAN"


def test_plan_validation_allows_review_explanation_without_altering_semantics():
    from campus.project_execution import compile_plan, validate_project_plan

    context = execution_context()
    plan = compile_plan(**context)
    plan.assumptions = ["Three stages process immutable shards offline."]
    assert validate_project_plan(plan, **context) is plan


@pytest.mark.parametrize(
    "change",
    [
        lambda context: context["inputs"][0].update(mount_path="/inputs/../secret.json"),
        lambda context: context["inputs"][0].update(mount_path="/inputs/.env"),
        lambda context: context["inputs"][0].update(mount_path="/inputs/stages/a/result.json"),
        lambda context: context["inputs"][0].update(mount_path="/inputs/not-declared.txt"),
        lambda context: context["inputs"][0].update(mount_path="/inputs/DATA/train/a.json"),
        lambda context: context["inputs"][0].update(archive=True),
        lambda context: context["inputs"][0].update(sha256="not-a-checksum"),
        lambda context: context["inputs"].clear(),
        lambda context: context["inputs"].append(dict(context["inputs"][0])),
    ],
)
def test_compiler_rejects_unsafe_missing_or_undeclared_input_manifests(change):
    from campus.errors import Problem
    from campus.project_execution import compile_plan

    context = execution_context()
    change(context)
    with pytest.raises(Problem):
        compile_plan(**context)


def test_preparer_enforces_the_whole_dag_task_budget():
    from campus.errors import Problem
    from campus.project_execution import compile_plan, prepare_tasks

    context = execution_context()
    plan = compile_plan(**context)
    with pytest.raises(Problem) as failure:
        prepare_tasks(plan, context["config"], context["inputs"], max_tasks=4)
    assert failure.value.code == "QUOTA_EXCEEDED"


@pytest.mark.parametrize(
    "parameters,accelerator",
    [
        ({}, "cpu"),
        ({"input": "/inputs/data/test/a.json"}, "cpu"),
        ({"input": "/inputs/../secret.json"}, "cpu"),
        ({"input": "/inputs/data/train/a.json", "extra": "not trusted"}, "cpu"),
        ({"input": "/inputs/data/train/a.json"}, "rocm"),
    ],
)
def test_runtime_resolution_rejects_unselected_inputs_and_untrusted_parameters(parameters, accelerator):
    from campus.errors import Problem
    from campus.project_execution import compile_plan, resolve_argv

    context = execution_context()
    stage = compile_plan(**context).stages[0]
    with pytest.raises(Problem):
        resolve_argv(stage, parameters, accelerator)


@pytest.mark.parametrize(
    "path",
    [
        "/inputs/data/train/a.json",
        "/inputs/.env",
        "/inputs/not-selected.json",
    ],
)
def test_runtime_binding_filter_rejects_duplicate_unsafe_and_unselected_bindings(path):
    from campus import dto
    from campus.errors import Problem
    from campus.project_execution import compile_plan, filter_bindings

    context = execution_context()
    stage = compile_plan(**context).stages[0]
    stage.bindings.append(dto.InputBinding(artifact_id=context["project_id"], mount_path=path))
    with pytest.raises(Problem):
        filter_bindings(stage, {"input": "/inputs/data/train/a.json"})


def test_command_arguments_with_shell_punctuation_stay_literal():
    from campus.project_execution import compile_plan, resolve_argv
    from campus.project_schema import ProjectConfig

    context = execution_context()
    value = context["config"].model_dump()
    value["stages"][0]["command"].append("$(whoami); > /tmp/unwanted & literal")
    context["config"] = ProjectConfig.model_validate(value)
    stage = compile_plan(**context).stages[0]
    assert (
        resolve_argv(stage, {"input": "/inputs/data/train/a.json"}, "cpu")[-1]
        == "$(whoami); > /tmp/unwanted & literal"
    )


def test_single_reduce_project_without_datasets_prepares_one_container_task():
    from campus.project_execution import compile_plan, prepare_tasks, resolve_argv
    from campus.project_schema import ProjectConfig

    context = execution_context()
    value = project_value()
    value["data"] = []
    value["stages"] = [value["stages"][1]]
    value["stages"][0]["depends_on"] = []
    context["config"] = ProjectConfig.model_validate(value)
    context["inputs"] = []
    plan = compile_plan(**context)
    assert plan.execution_kind == "single"
    assert prepare_tasks(plan, context["config"], [], 1) == {
        "merge": [{"item_ids": [], "parameters": {}, "units": 1}]
    }
    assert resolve_argv(plan.stages[0], {}, "cpu") == ["python", "merge.py", "--accelerator", "cpu"]


def test_zero_vram_auto_stage_stays_cpu_only():
    from campus.project_execution import compile_plan
    from campus.project_schema import ProjectConfig

    context = execution_context()
    value = context["config"].model_dump()
    value["stages"][0]["resources"]["vram_bytes"] = 0
    context["config"] = ProjectConfig.model_validate(value)
    assert {variant.accelerator for variant in compile_plan(**context).stages[0].variants} == {"cpu"}


def test_compiler_refuses_to_reduce_a_user_reservation_to_fit_a_catalog_profile():
    from campus.errors import Problem
    from campus.project_execution import compile_plan

    context = execution_context()
    for environment in context["catalog"].environments.values():
        environment.resource_requirements.memory_bytes = 268435456
    with pytest.raises(Problem):
        compile_plan(**context)


@pytest.mark.parametrize("kind", ["map", "reduce"])
def test_project_adapter_prepares_only_metadata_without_executing_project_code(kind):
    from campus.project_adapter import handle_request
    from campus.project_execution import compile_plan

    context = execution_context()
    stage = compile_plan(**context).stages[0 if kind == "map" else 1]
    data = {"stage": stage.model_dump(mode="json"), "inputs": context["inputs"], "max_tasks": 10}
    checked = handle_request({"operation": "validate_input", "payload": data})
    assert checked.result == {"valid": True}
    response = handle_request({"operation": "split", "payload": data})
    assert response.error is None
    assert len(response.result["tasks"]) == (2 if kind == "map" else 1)
    assert response.result["tasks"][0]["parameters"] == (
        {"input": "/inputs/data/train/a.json"} if kind == "map" else {}
    )


def test_project_adapter_protocol_is_bounded_and_does_not_import_selected_scripts():
    import json
    import os
    import subprocess
    import sys

    from campus.project_execution import compile_plan

    context = execution_context()
    stage = compile_plan(**context).stages[0]
    request = {
        "operation": "split",
        "payload": {
            "stage": stage.model_dump(mode="json"),
            "inputs": context["inputs"],
            "max_tasks": 1,
        },
    }
    response = subprocess.run(
        [sys.executable, "-m", "campus.project_adapter"],
        # pytest's pythonpath setting does not propagate to child interpreters.
        cwd=Path(__file__).resolve().parents[1],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert response.returncode == 0, response.stderr
    body = json.loads(response.stdout)
    assert body["result"] is None
    assert body["error"]


def test_project_adapter_rejects_operations_and_payloads_outside_metadata_splitting():
    from campus.project_adapter import handle_request

    assert handle_request({"operation": "verify", "payload": {}}).error
    assert handle_request({"operation": "split", "payload": {"command": ["python", "script.py"]}}).error
