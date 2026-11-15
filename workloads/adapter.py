"""One request/response JSON protocol for statically registered adapters.

Run in a prepared, resource-limited, no-network adapter container. The API process
must invoke this executable rather than import ML or user validator modules.
"""
from __future__ import annotations

import json
import sys

from workloads.common import parse_json, read_json, regular_file, require_digest
from workloads.provenance import REGISTERED_VERSIONS, adapter_metadata

_VERSIONS = REGISTERED_VERSIONS


def _registered(adapter):
    # Import targets are literal application code, never request paths or names.
    if adapter == "embeddings.e5":
        from workloads.embeddings import adapter as module
    elif adapter == "training.image-classifier":
        from workloads.training import adapter as module
    else:
        from workloads.generic import adapter as module
    return module


def dispatch(request):
    response = {"schema_version": "1", "result": None, "error": None}
    try:
        if not isinstance(request, dict) or request.get("schema_version") != "1":
            raise ValueError("unsupported request schema_version")
        adapter = request.get("adapter")
        if not isinstance(adapter, dict) or _VERSIONS.get(adapter.get("id")) != adapter.get("version") or adapter.get("id") not in _VERSIONS:
            response["error"] = {"code": "UNKNOWN_ADAPTER", "message": "adapter id/version is not registered"}
            return response
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        module = _registered(adapter["id"])
        operation = request.get("operation")
        if operation in {"verify", "aggregate"} and "output_paths" in payload:
            if "outputs" in payload:
                raise ValueError("choose inline outputs or prepared output_paths")
            paths = payload["output_paths"]
            if not isinstance(paths, list) or not 1 <= len(paths) <= 1024:
                raise ValueError("output_paths must contain 1..1024 local JSON artifacts")
            paths = [regular_file(path, max_bytes=256 << 20) for path in paths]
            if sum(path.stat().st_size for path in paths) > 2 << 30:
                raise ValueError("output artifact quota exceeded")
            payload = {**payload, "outputs": [read_json(path, max_bytes=256 << 20) for path in paths]}
        if operation == "validate_input":
            result = module.validate_input(payload["manifest"])
        elif operation == "split":
            result = module.split(payload["manifest"], payload.get("batch_policy", {}))
        elif operation == "verify":
            result = module.verify(payload["manifest"], payload["outputs"], payload.get("policy", {}))
        elif operation == "aggregate":
            result = module.aggregate(payload)
        elif operation == "run" and adapter["id"] == "embeddings.e5":
            result = module.run(payload["manifest"], payload.get("batch_policy", {}),
                                device=payload.get("device", "cpu"), item_ids=payload.get("item_ids"))
        elif operation == "run" and adapter["id"] == "training.image-classifier":
            result = module.train(payload["manifest"], payload.get("output_dir", "/outputs"),
                                  payload.get("checkpoint_dir", "/checkpoints"),
                                  resume_from=payload.get("resume_from"), device=payload.get("device", "cpu"))
        else:
            response["error"] = {"code": "UNSUPPORTED_OPERATION", "message": "operation is not registered for this adapter"}
            return response
        if operation in {"validate_input", "verify"}:
            if not isinstance(result, dict):
                raise ValueError("registered validator must return a report object")
            result = {**result, **adapter_metadata(adapter["id"], adapter["version"])}
            # Invalid inputs may have no computable identity. Successful reports
            # must supply identities computed by the registered adapter itself.
            result.setdefault("manifest_digest", "")
            result.setdefault("model_digest", "")
            if result.get("valid") or result.get("accepted"):
                require_digest(result["manifest_digest"])
                if adapter["id"] != "generic.project":
                    require_digest(result["model_digest"])
        response["result"] = result
    except (ValueError, KeyError, TypeError, OSError) as exc:
        response["error"] = {"code": "INVALID_INPUT", "message": str(exc)}
    except Exception as exc:
        response["error"] = {"code": "EXECUTION_FAILED", "message": f"{type(exc).__name__}: {exc}"}
    return response


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raw = sys.stdin.buffer.read((16 << 20) + 1)
        if len(raw) > 16 << 20:
            raise ValueError("adapter request exceeds 16 MiB quota")
        response = dispatch(parse_json(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        response = {"schema_version": "1", "result": None,
                    "error": {"code": "INVALID_REQUEST", "message": str(exc)}}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
