"""Pure CPU/CUDA preference from accepted throughput for reviewed auto projects.

Capacity, image compatibility, device consent and busy GPUs remain admission
checks in the scheduler. This helper only reorders the supplied candidates.
"""

import math


def _positive_rate(value):
    if isinstance(value, (bool, str, bytes)):
        return None
    try:
        rate = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def order_variants(stage, variants, rate_for_variant) -> list:
    """Explore one unsampled auto variant, then prefer accepted units/second.

    Ties and unknown candidates retain the legacy stable CUDA-first ordering.
    Once every compatible variant has an accepted sample, measured throughput
    determines preference. Explicit accelerator choices and other adapters retain
    legacy behavior and do not query throughput.
    """
    legacy = sorted(variants, key=lambda variant: variant.accelerator != "cuda")
    contract = stage.parameters.get("project_stage")
    if (
        stage.adapter.id not in {"campus.project-map", "campus.project-reduce"}
        or stage.adapter.version != "1"
        or not isinstance(contract, dict)
        or contract.get("accelerator") != "auto"
    ):
        return legacy

    known = []
    unknown = []
    for candidate in legacy:
        rate = _positive_rate(rate_for_variant(candidate))
        if rate is None:
            unknown.append(candidate)
        else:
            known.append((candidate, rate))
    known.sort(key=lambda item: item[1], reverse=True)
    return unknown[:1] + [candidate for candidate, _ in known] + unknown[1:]
