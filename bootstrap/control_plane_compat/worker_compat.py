"""Opt-in diagnostic bridge for the already-installed campus/0.1.0 worker.

This is deliberately not general API compatibility. A trusted administrator
must allowlist the device, image, adapter and every resource quota. All normal
authentication, leases, uploads and result verification remain in campus.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from uuid import UUID

ENV = "CAMPUS_DIAGNOSTIC_WORKER_POLICY"
SANDBOX_KEYS = {"cpu_millis", "memory_bytes", "pids", "workspace_bytes",
                "output_bytes", "checkpoint_bytes", "tmp_bytes", "gpu_ids", "vram_bytes"}
DISK_KEYS = ("workspace_bytes", "output_bytes", "checkpoint_bytes", "tmp_bytes")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_policy(policy):
    if set(policy) != {"schema_version", "node_ids", "image_digest", "adapter", "max_task_ms", "sandbox"}:
        raise ValueError("unknown or missing diagnostic policy fields")
    if (type(policy["schema_version"]) is not int or policy["schema_version"] != 1
            or type(policy["max_task_ms"]) is not int or not 1000 <= policy["max_task_ms"] <= 60000):
        raise ValueError("invalid diagnostic policy version/deadline")
    if not isinstance(policy["node_ids"], list) or not 1 <= len(policy["node_ids"]) <= 8:
        raise ValueError("explicit diagnostic device allowlist required")
    for node in policy["node_ids"]:
        if str(UUID(node)) != node:
            raise ValueError("invalid diagnostic node identity")
    if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}", policy["image_digest"]):
        raise ValueError("immutable diagnostic image required")
    adapter = policy["adapter"]
    if set(adapter) != {"id", "version"} or any(not NAME.fullmatch(v) for v in adapter.values()):
        raise ValueError("explicit diagnostic adapter required")
    limits = policy["sandbox"]
    if set(limits) != SANDBOX_KEYS:
        raise ValueError("explicit diagnostic sandbox quotas required")
    if (type(limits["cpu_millis"]) is not int or limits["cpu_millis"] != 1000 or type(limits["memory_bytes"]) is not int
            or not 32 << 20 <= limits["memory_bytes"] <= 256 << 20
            or type(limits["pids"]) is not int or not 1 <= limits["pids"] <= 128
            or limits["gpu_ids"] != [] or type(limits["vram_bytes"]) is not int or limits["vram_bytes"] != 0
            or any(type(limits[k]) is not int or not 0 < limits[k] <= 64 << 20 for k in DISK_KEYS)):
        raise ValueError("invalid bounded CPU-only diagnostic sandbox")
    return policy


@lru_cache(maxsize=1)
def load_policy():
    filename = os.environ.get(ENV)
    if not filename:
        return None
    with Path(filename).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("diagnostic policy exceeds size limit")
    return validate_policy(json.loads(raw))


def policy_for(actor, user_agent, policy=None):
    policy = load_policy() if policy is None else policy
    credential = actor.credential
    if (policy is not None and user_agent == "campus/0.1.0"
            and credential.scope == "device"
            and str(credential.node_id) in policy["node_ids"]):
        return policy
    return None


def field(item, name):
    return item[name] if isinstance(item, dict) else getattr(item, name)


def supports(stage, variant, policy):
    if policy is None:
        configured = load_policy()
        if configured and (stage.adapter.id, stage.adapter.version) == (
                configured["adapter"]["id"], configured["adapter"]["version"]):
            return False  # The diagnostic adapter is reserved for its allowlist.
        return True
    limits = policy["sandbox"]
    resources = variant.resource_requirements
    outputs = stage.outputs
    return (
        stage.adapter.id == policy["adapter"]["id"]
        and stage.adapter.version == policy["adapter"]["version"]
        and variant.image_digest == policy["image_digest"]
        and variant.platform == "linux/arm64" and variant.accelerator == "cpu"
        and not stage.bindings and not stage.depends_on
        and stage.resume_policy in {"restart", "unsupported"}
        and 1000 <= stage.task_deadline_ms <= policy["max_task_ms"]
        and stage.working_dir == "/workspace" and stage.network_policy == "none"
        and resources.cpu_millis == limits["cpu_millis"]
        and resources.memory_bytes == limits["memory_bytes"]
        and resources.disk_bytes == sum(limits[k] for k in DISK_KEYS)
        and resources.gpu_slots == 0 and resources.vram_bytes == 0
        and len(outputs) == 1 and field(outputs[0], "required") is True
        and NAME.fullmatch(field(outputs[0], "path")) is not None
        and field(outputs[0], "path") not in {".", ".."}
        and 0 < field(outputs[0], "max_bytes") <= limits["output_bytes"]
    )


def assignment(value, policy, *, remaining_ms):
    if policy is None:
        return value
    if type(remaining_ms) is not int or not 0 < remaining_ms <= policy["max_task_ms"]:
        raise ValueError("server-computed relative task deadline required")
    result = deepcopy(value)
    if result["inputs"] or result["resume_checkpoint"] is not None:
        raise ValueError("diagnostic bridge does not support inputs or checkpoints")
    result["task_remaining_ms"] = remaining_ms
    result["resource_limits"] = deepcopy(policy["sandbox"])
    result["outputs"] = [item["path"] for item in result["outputs"]]
    result["validator_policy"] = {"mode": result["validator_policy"]}
    return result


def result_manifest(value, policy):
    if policy is None:
        return value
    result = deepcopy(value)
    if len(result["outputs"]) != 1:
        raise ValueError("diagnostic bridge requires exactly one output")
    for output in result["outputs"]:
        prefix, separator, name = output["path"].partition("/")
        if prefix != "workspace" or not separator or not NAME.fullmatch(name) or name in {".", ".."}:
            raise ValueError("unsupported legacy output namespace")
        output["path"] = name
    return result
