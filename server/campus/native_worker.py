"""Negotiated native worker v2: explicit sandbox quotas and relative deadlines.

The public v1 assignment stays unchanged. The coordinator, not a laptop clock,
defines every hard runtime and disk bound for opt-in native clients.
"""

from copy import deepcopy
from types import SimpleNamespace

from .artifacts.storage import safe_relative, secret_path
from .errors import Problem


def resource_policy(stage, variant):
    resources = variant.resource_requirements
    disk = resources.disk_bytes
    output = sum(item.max_bytes for item in stage.outputs)
    tmp = max(4096, min(64 << 20, disk // 8))
    checkpoint = max(4096, min(64 << 20, disk // 8))
    # The pinned guard scans workspace under 16x its capture envelope. Reserve
    # explicit headroom so unchanged source files do not consume result limits.
    output = max(output, (disk - tmp - checkpoint + 16) // 17)
    workspace = disk - output - tmp - checkpoint
    if workspace <= 0 or resources.memory_bytes < 32 << 20:
        raise ValueError("Declared output and sandbox quotas exceed reserved resources")
    if variant.accelerator == "cuda":
        if resources.gpu_slots != 1 or resources.vram_bytes <= 0:
            raise ValueError("CUDA requires one explicitly reserved GPU")
    elif resources.gpu_slots or resources.vram_bytes:
        raise ValueError("CPU work cannot request a GPU")
    return {
        "cpu_millis": resources.cpu_millis,
        "memory_bytes": resources.memory_bytes,
        "pids": 128,
        "workspace_bytes": workspace,
        "output_bytes": output,
        "checkpoint_bytes": checkpoint,
        "tmp_bytes": tmp,
        "gpu_ids": [],
        "vram_bytes": resources.vram_bytes,
    }


def sandbox(stage, variant, node):
    result = resource_policy(stage, variant)
    if variant.accelerator == "cuda":
        candidates = [
            gpu
            for gpu in (node.capabilities or {}).get("gpus", [])
            if gpu.get("vendor", "").casefold() == "nvidia"
            and gpu.get("cuda_verified") is True
            and gpu.get("vram_bytes", 0) >= result["vram_bytes"]
        ]
        if not candidates:
            raise ValueError("No verified NVIDIA selector satisfies the reservation")
        result["gpu_ids"] = [candidates[0]["id"]]
    return result


def input_path(binding):
    if binding.archive:
        if binding.mount_path != "/workspace":
            raise ValueError("Native project archives must mount at /workspace")
        return "project.zip"
    if not binding.mount_path.startswith("/inputs/"):
        raise ValueError("Native file inputs must mount below /inputs")
    path = binding.mount_path.removeprefix("/inputs/")
    safe_relative(path)
    if path.casefold() == "project.zip":
        raise ValueError("project.zip is reserved for validated project archives")
    return path


def supports(stage, variant, node, *, gpu_busy=False):
    try:
        validate_command(stage)
        if stage.resume_policy == "checkpoint":
            return False  # Requires an explicit checkpoint binding protocol.
        if variant.accelerator == "cuda" and gpu_busy:
            return False  # Never select the same GPU for concurrent reservations.
        sandbox(stage, variant, node)
        paths = [input_path(binding) for binding in stage.bindings]
        if not distinct_paths(paths) or not distinct_paths([o.path for o in stage.outputs]):
            return False
        for output in stage.outputs:
            if secret_path(safe_relative(output.path)):
                return False
        return True
    except (ValueError, TypeError, KeyError, Problem):
        return False


def validate_command(stage):
    if sum(len(arg.encode("utf-8")) for arg in stage.argv) > 65_536:
        raise ValueError("Native command arguments exceed 65,536 bytes")


def distinct_paths(paths):
    folded = [p.casefold() for p in paths]
    return len(set(folded)) == len(folded) and not any(
        a.startswith(b + "/") for a in folded for b in folded if a != b
    )


def assignment(value, stage, variant, node, *, remaining_ms):
    if type(remaining_ms) is not int or not 0 < remaining_ms <= stage.task_deadline_ms:
        raise ValueError("A positive server-computed relative task deadline is required")
    result = deepcopy(value)
    result["task_remaining_ms"] = remaining_ms
    result["resource_limits"] = sandbox(stage, variant, node)
    flattened = []
    for binding in result["inputs"]:
        local = input_path(SimpleNamespace(**binding))
        artifact = deepcopy(binding["artifact"])
        artifact["name"] = local
        flattened.append(artifact)
    if len({a["name"].casefold() for a in flattened}) != len(flattened):
        raise ValueError("Input bindings collide on the native filesystem")
    result["inputs"] = flattened
    outputs = result["outputs"]
    result["outputs"] = [item["path"] for item in outputs if item.get("required", True)]
    result["optional_outputs"] = [item["path"] for item in outputs if not item.get("required", True)]
    result["validator_policy"] = {"mode": result["validator_policy"]}
    return result
