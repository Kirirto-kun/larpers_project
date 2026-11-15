from types import SimpleNamespace

import pytest


def stage(accelerator="auto", adapter="campus.project-map", version="1"):
    return SimpleNamespace(
        adapter=SimpleNamespace(id=adapter, version=version),
        parameters={"project_stage": {"accelerator": accelerator}},
    )


def variant(name, accelerator):
    return SimpleNamespace(variant_id=name, accelerator=accelerator)


@pytest.mark.parametrize(
    "cpu_rate,cuda_rate,expected",
    [
        (2.0, 0.5, ["cpu", "cuda"]),
        (0.5, 2.0, ["cuda", "cpu"]),
        (2.0, 2.0, ["cuda", "cpu"]),
        (None, 2.0, ["cpu", "cuda"]),
        (2.0, None, ["cuda", "cpu"]),
        (None, None, ["cuda", "cpu"]),
        (0.0, 2.0, ["cpu", "cuda"]),
        (float("nan"), 2.0, ["cpu", "cuda"]),
        (float("inf"), 2.0, ["cpu", "cuda"]),
        (-1.0, 2.0, ["cpu", "cuda"]),
        (True, 2.0, ["cpu", "cuda"]),
    ],
)
def test_auto_project_selects_measured_throughput_and_explores_missing_samples(
    cpu_rate, cuda_rate, expected
):
    from campus.jobs.placement import order_variants

    candidates = [variant("cpu", "cpu"), variant("cuda", "cuda")]
    rates = {"cpu": cpu_rate, "cuda": cuda_rate}
    ordered = order_variants(stage(), candidates, lambda candidate: rates[candidate.variant_id])
    assert [candidate.variant_id for candidate in ordered] == expected
    assert [candidate.variant_id for candidate in candidates] == ["cpu", "cuda"]


def test_only_one_unknown_variant_precedes_the_fastest_measured_variant():
    from campus.jobs.placement import order_variants

    candidates = [
        variant("cpu-unknown", "cpu"),
        variant("cpu-fast", "cpu"),
        variant("cuda-unknown", "cuda"),
        variant("cpu-slow", "cpu"),
    ]
    rates = {"cpu-unknown": None, "cpu-fast": 4.0, "cuda-unknown": None, "cpu-slow": 1.0}
    calls = []

    def rate_for(candidate):
        calls.append(candidate.variant_id)
        return rates[candidate.variant_id]

    ordered = order_variants(stage(adapter="campus.project-reduce"), candidates, rate_for)
    assert [candidate.variant_id for candidate in ordered] == [
        "cuda-unknown",
        "cpu-fast",
        "cpu-slow",
        "cpu-unknown",
    ]
    assert sorted(calls) == ["cpu-fast", "cpu-slow", "cpu-unknown", "cuda-unknown"]


@pytest.mark.parametrize(
    "context",
    [
        stage(accelerator="cpu"),
        stage(accelerator="cuda"),
        stage(adapter="campus.python"),
        stage(version="2"),
        SimpleNamespace(adapter=SimpleNamespace(id="campus.project-map", version="1"), parameters={}),
    ],
)
def test_explicit_or_nonproject_stages_preserve_legacy_cuda_preference(context):
    from campus.jobs.placement import order_variants

    candidates = [variant("cpu", "cpu"), variant("cuda", "cuda")]

    def unexpected_rate_lookup(_):
        raise AssertionError("Explicit and legacy stages must not query automatic-placement rates")

    ordered = order_variants(context, candidates, unexpected_rate_lookup)
    assert [candidate.variant_id for candidate in ordered] == ["cuda", "cpu"]


def test_explicit_cuda_stage_never_introduces_a_cpu_variant():
    from campus.jobs.placement import order_variants

    candidates = [variant("cuda", "cuda")]
    ordered = order_variants(stage(accelerator="cuda"), candidates, lambda _: 0.5)
    assert ordered == candidates
    assert ordered is not candidates


def test_empty_variant_inventory_remains_empty():
    from campus.jobs.placement import order_variants

    assert order_variants(stage(), [], lambda _: 1.0) == []
