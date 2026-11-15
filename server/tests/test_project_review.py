import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import sample_plan
from pydantic import ValidationError

from campus.catalog import Catalog
from campus.errors import Problem
from campus.planner.provider import Proposal, parse_response


def test_project_review_is_a_distinct_bounded_action_not_an_executable_plan():
    proposal = Proposal.model_validate(
        {
            "project_review": {
                "approved": True,
                "summary": "Independent file shards merge into one model before evaluation.",
                "warnings": ["Dataset must keep the documented array schema."],
                "evidence": [{"source": "worker.py", "detail": "Map writes class sums and counts."}],
            }
        }
    )
    assert proposal.project_review.approved
    assert proposal.plan is None
    with pytest.raises(ValidationError):
        Proposal.model_validate(
            {"plan": sample_plan(), "project_review": {"approved": True, "summary": "two actions"}}
        )
    for review in (
        {"approved": "yes", "summary": "coercion"},
        {"approved": True, "summary": "x" * 4097},
        {"approved": True, "summary": "bounded", "warnings": ["warning"] * 25},
        {"approved": True, "summary": "bounded", "command": ["unreviewed"]},
    ):
        with pytest.raises(ValidationError):
            Proposal.model_validate({"project_review": review})


def test_actual_provider_parser_accepts_project_review_and_still_rejects_truncation():
    settings = SimpleNamespace(llm_provider="deepseek", llm_model="test-model")
    review = {"project_review": {"approved": True, "summary": "Map/reduce contract is supported."}}
    raw = {"choices": [{"message": {"content": json.dumps(review)}, "finish_reason": "stop"}]}
    result = parse_response(settings, json.dumps(raw).encode(), {"project_config": {}})
    assert result.project_review.summary == "Map/reduce contract is supported."
    raw["choices"][0]["finish_reason"] = "length"
    with pytest.raises(Problem) as exc:
        parse_response(settings, json.dumps(raw).encode(), {})
    assert exc.value.code == "PLANNER_INCOMPLETE"


def test_builtin_project_catalog_registers_only_the_two_trusted_metadata_adapters(tmp_path):
    source = Path(__file__).parents[1] / "campus/data/product-catalog.json"
    value = json.loads(source.read_text())
    value["adapters"] = [
        a
        for a in value["adapters"]
        if a["ref"]["id"] not in {"campus.project-map", "campus.project-reduce"}
    ]
    value["adapters"].append(
        {
            "ref": {"id": "campus.project-map", "version": "1"},
            "image_ids": ["python-cpu-amd64"],
            "policies": ["structural"],
            "parallel": True,
            "builtin": "project",
        }
    )
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(value))
    assert Catalog(target).adapters[("campus.project-map", "1")].parallel
    value["adapters"][-1]["ref"]["id"] = "user.arbitrary-runtime"
    target.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        Catalog(target)
