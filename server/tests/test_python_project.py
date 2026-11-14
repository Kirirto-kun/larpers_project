import io
from pathlib import Path
from zipfile import ZipFile

import pytest
from conftest import sample_plan

from campus.catalog import Catalog
from campus.db import digest
from campus.web_product import example_plan


def project_archive(cp, name="main.py"):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(name, "print('selected user project')\n")
    return cp.upload(stream.getvalue(), "project.zip", kind="project", part_size=1024)


def project_preview(cp, project, bindings):
    cp.runtime.catalog = Catalog(Path(__file__).parents[1] / "campus/data/product-catalog.json")
    plan = example_plan(cp.runtime.catalog, "cpu").model_dump(mode="json")
    plan["project_digest"] = project["sha256"]
    inputs = []
    for binding in bindings:
        artifact = binding.pop("artifact", project)
        inputs.append({**binding, "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"]})
    plan["input_manifest_digest"] = digest({"project_digest": project["sha256"], "inputs": inputs})
    plan["stages"][0]["argv"] = ["python", "main.py"]
    plan["stages"][0]["bindings"] = bindings
    return cp.preview(plan=plan, fields={"project_artifact_id": project["id"], "inputs": bindings})


@pytest.mark.parametrize("case", ["missing", "wrong_id", "wrong_mount", "not_archive", "duplicate"])
def test_python_selected_project_requires_exact_archive_binding(cp, case):
    project = project_archive(cp)
    binding = {"artifact_id": project["id"], "mount_path": "/workspace", "archive": True}
    bindings = [binding]
    if case == "missing":
        bindings = []
    elif case == "wrong_id":
        other = project_archive(cp, "other.py")
        binding.update(artifact_id=other["id"], artifact=other)
    elif case == "wrong_mount":
        binding["mount_path"] = "/workspace/project"
    elif case == "not_archive":
        binding.update(mount_path="/inputs/project.zip", archive=False)
    elif case == "duplicate":
        bindings.append(dict(binding))
    preview = project_preview(cp, project, bindings)
    assert preview["state"] == "failed", preview
    assert preview["error_code"] == "INVALID_PLAN"
    assert "selected project" in preview["error_message"].lower()
    assert "/workspace" in preview["error_message"]


def test_python_exact_selected_archive_binding_reaches_ready(cp):
    project = project_archive(cp)
    binding = {"artifact_id": project["id"], "mount_path": "/workspace", "archive": True}
    preview = project_preview(cp, project, [binding])
    assert preview["state"] == "ready", preview
    assert preview["plan"]["stages"][0]["bindings"] == [binding]


def test_python_without_selected_project_preserves_inline_examples(cp):
    cp.runtime.catalog = Catalog(Path(__file__).parents[1] / "campus/data/product-catalog.json")
    preview = cp.preview(plan=example_plan(cp.runtime.catalog, "cpu").model_dump(mode="json"))
    assert preview["state"] == "ready", preview


def test_unrelated_legacy_adapter_does_not_require_selected_project_mount(cp):
    project = project_archive(cp)
    plan = sample_plan(
        project=project["sha256"], manifest=digest({"project_digest": project["sha256"], "inputs": []})
    )
    preview = cp.preview(plan=plan, fields={"project_artifact_id": project["id"]})
    assert preview["state"] == "ready", preview
