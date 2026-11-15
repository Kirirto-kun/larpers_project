from campus import dto
from campus.catalog import Catalog
from campus.planner.validation import validate_mount
from campus.web_product import example_plan


def test_examples_use_pinned_native_catalog_profiles():
    from pathlib import Path

    catalog = Catalog(Path(__file__).parents[1] / "campus/data/product-catalog.json")
    cpu = example_plan(catalog, "cpu")
    gpu = example_plan(catalog, "cuda")
    assert {v.platform for v in cpu.stages[0].variants} == {"linux/amd64", "linux/arm64"}
    assert all(v.accelerator == "cpu" for v in cpu.stages[0].variants)
    assert all(v.accelerator == "cuda" for v in gpu.stages[0].variants)
    assert cpu.stages[0].network_policy == "none"
    assert cpu.stages[0].outputs[0].path == "model.json"
    assert cpu.stages[0].adapter.id == "campus.python"
    assert gpu.stages[0].task_deadline_ms == 60_000


def test_product_hides_legacy_diagnostics_from_interactive_planning():
    from pathlib import Path

    catalog = Catalog(Path(__file__).parents[1] / "campus/data/product-catalog.json")
    assert list(catalog.recipes) == ["python-script"]
    assert {a["ref"]["id"] for a in catalog.planner_context()["adapters"]} == {"campus.python"}
    assert catalog.adapter(dto.AdapterRef(id="campus.diagnostic.cuda", version="1"))


def test_web_product_routes_require_identity_and_create_normal_preview(cp):
    from pathlib import Path

    cp.runtime.catalog = Catalog(Path(__file__).parents[1] / "campus/data/product-catalog.json")
    cp.call("GET", "/client-release", headers={}, code=401)
    assert {d["os"] for d in cp.call("GET", "/client-release")["downloads"]} >= {"windows", "darwin"}
    assert {e["id"] for e in cp.call("GET", "/examples")} == {"cpu", "cuda"}
    preview = cp.call("POST", "/examples/cpu/preview", body={}, code=202)
    assert preview["state"] == "inspecting"
    from campus.main import plan_once

    plan_once(cp.runtime)
    ready = cp.call("GET", "/job-previews/" + preview["id"])
    assert ready["state"] == "ready", ready
    assert ready["plan"]["stages"][0]["adapter"]["id"] == "campus.python"


def test_native_root_project_binding_still_requires_validated_project():
    from types import SimpleNamespace

    import pytest

    from campus.errors import Problem

    binding = dto.InputBinding(
        artifact_id="11111111-1111-4111-8111-111111111111", mount_path="/workspace", archive=True
    )
    validate_mount(binding, SimpleNamespace(kind="project"))
    with pytest.raises(Problem):
        validate_mount(binding, SimpleNamespace(kind="input"))
