"""Bounded JSON protocol for trusted project metadata validation and splitting.

This module never executes or imports a user's project. The coordinator's
direct pure helper path is preferred; the registered runtime remains usable
when the ordinary adapter exchange protocol is required.
"""

import json
import sys

from pydantic import ValidationError

from . import dto
from .errors import Problem
from .project_execution import prepare_stage_tasks

MAX_MESSAGE_BYTES = 4 * 1024 * 1024


def handle_request(request) -> dto.AdapterResponse:
    try:
        request = dto.AdapterRequest.model_validate(request)
        if request.operation not in {"validate_input", "split"}:
            raise ValueError("Only metadata validation and splitting are supported")
        if (
            len(json.dumps(request.model_dump(mode="json"), ensure_ascii=False).encode())
            > MAX_MESSAGE_BYTES
        ):
            raise ValueError("Adapter request exceeds its metadata bound")
        data = request.payload
        if set(data) - {"stage", "inputs", "max_tasks", "batch_policy"}:
            raise ValueError("Unexpected adapter payload fields")
        stage = dto.Stage.model_validate(data["stage"])
        tasks = prepare_stage_tasks(stage, data["inputs"], data["max_tasks"])
        return dto.AdapterResponse(
            result={"valid": True} if request.operation == "validate_input" else {"tasks": tasks}
        )
    except (ValidationError, ValueError, TypeError, KeyError, Problem):
        return dto.AdapterResponse(error="The trusted project adapter rejected the metadata request")


def main():
    raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
    try:
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("Adapter request exceeds its metadata bound")
        response = handle_request(json.loads(raw))
    except (ValueError, UnicodeError):
        response = dto.AdapterResponse(error="The project adapter requires bounded valid JSON metadata")
    encoded = response.model_dump_json()
    if len(encoded.encode()) > MAX_MESSAGE_BYTES:
        encoded = dto.AdapterResponse(
            error="Project split exceeded its response bound"
        ).model_dump_json()
    sys.stdout.write(encoded + "\n")


if __name__ == "__main__":
    main()
