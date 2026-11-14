import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from pydantic import ValidationError

from .. import dto
from ..db import payload
from ..errors import Problem


def exchange(adapter, operation, data, *, timeout):
    if not adapter.command:
        raise Problem("ADAPTER_UNAVAILABLE", "The adapter runtime is not configured", 503)
    request = dto.AdapterRequest(operation=operation, payload=data).model_dump_json().encode()
    deadline = time.monotonic() + timeout
    env = {"PATH": os.defpath + ":/usr/local/bin:/opt/homebrew/bin", "LANG": "C.UTF-8"}
    try:
        with tempfile.TemporaryFile() as stdin:
            stdin.write(request)
            stdin.seek(0)
            with (
                subprocess.Popen(
                    adapter.command,
                    stdin=stdin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    cwd=Path(adapter.command[0]).parent
                    if Path(adapter.command[0]).is_absolute()
                    else "/",
                    start_new_session=True,
                ) as process,
                selectors.DefaultSelector() as selector,
            ):
                try:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    output = bytearray()
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise TimeoutError
                        chunk = os.read(process.stdout.fileno(), 65_536)
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > 4 * 1024 * 1024:
                            raise ValueError("Adapter response exceeded its limit")
                    if process.wait(timeout=max(0, deadline - time.monotonic())):
                        raise ValueError("Adapter exited unsuccessfully")
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        reply = dto.AdapterResponse.model_validate_json(output)
        if reply.error or reply.result is None:
            raise Problem("ADAPTER_REJECTED", "The registered adapter rejected the request", 422)
        return reply.result
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        raise Problem(
            "ADAPTER_TIMEOUT", "The adapter exceeded its deadline", 503, retryable=True
        ) from exc
    except (OSError, ValueError, ValidationError) as exc:
        raise Problem(
            "ADAPTER_ERROR",
            "The registered adapter did not return a valid response",
            503,
            retryable=True,
        ) from exc


def prepare(plan, inputs, catalog, max_tasks, *, batch_policy=None, budget_seconds=50):
    deadline = time.monotonic() + budget_seconds

    def timeout(maximum):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Problem("PLANNER_TIMEOUT", "Plan validation exceeded its deadline", 503)
        return min(maximum, remaining)

    result = {}
    for stage in plan.stages:
        adapter = catalog.adapter(stage.adapter)
        data = {
            "stage": payload(stage),
            "inputs": inputs,
            "batch_policy": batch_policy
            or {
                "target_duration_ms": 10_000,
                "target_units": adapter.initial_batch_units,
                "max_units": adapter.max_batch_units,
            },
            "max_tasks": max_tasks,
        }
        if adapter.command:
            checked = exchange(adapter, "validate_input", data, timeout=timeout(20))
            if checked.get("valid") is not True:
                raise Problem("INVALID_INPUT", "The registered adapter rejected the inputs", 422)
        if adapter.parallel and not stage.depends_on and plan.execution_kind in {"map", "dag"}:
            specs = exchange(adapter, "split", data, timeout=timeout(30)).get("tasks")
            result[stage.stage_id] = validate_split(specs, max_tasks)
        else:
            result[stage.stage_id] = [
                {"item_ids": [], "parameters": {}, "units": int(not stage.depends_on)}
            ]
    if sum(map(len, result.values())) > max_tasks:
        raise Problem("QUOTA_EXCEEDED", "The plan exceeds the task limit", 422)
    return result


def validate_split(specs, max_tasks):
    if not isinstance(specs, list) or not 0 < len(specs) <= max_tasks:
        raise Problem("INVALID_SPLIT", "Adapter returned an invalid task count", 422)
    seen = set()
    checked = []
    for spec in specs:
        if not isinstance(spec, dict):
            raise Problem("INVALID_SPLIT", "Adapter returned an invalid task", 422)
        ids = spec.get("item_ids")
        parameters = spec.get("parameters", {})
        if (
            not isinstance(ids, list)
            or not 0 < len(ids) <= 10_000
            or any(not isinstance(x, str) or not 0 < len(x) <= 256 or "\x00" in x for x in ids)
            or len(set(ids)) != len(ids)
            or seen.intersection(ids)
            or not isinstance(parameters, dict)
            or len(parameters) > 32
        ):
            raise Problem("INVALID_SPLIT", "Adapter returned invalid or repeated item IDs", 422)
        seen.update(ids)
        checked.append({"item_ids": ids, "parameters": parameters, "units": len(ids)})
    return checked


def read_json_result(path, maximum):
    with path.open("rb") as file:
        raw = file.read(maximum + 1)
    if len(raw) > maximum:
        raise Problem("INVALID_RESULT", "Structured result exceeds its limit", 422)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise Problem("INVALID_RESULT", "Structured result is not valid JSON", 422) from exc
