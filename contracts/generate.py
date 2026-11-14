"""Export the v1 contract and generate client DTOs from the committed contract."""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "openapi-v1.yaml"
TARGETS = {
    "portal": ROOT / "apps/portal/src/generated/api.ts",
    "console": ROOT / "apps/console/src/generated/api.ts",
    "go": ROOT / "runner/internal/generated/api.go",
    "python": ROOT / "sdk/python/campus/generated/api.py",
}
SCHEMAS = {
    "job-plan": "JobPlan", "assignment": "Assignment", "capabilities": "Capabilities",
    "events": "Event", "standalone-record": "StandaloneRecord", "benchmark-report": "BenchmarkReport",
    "adapter-request": "AdapterRequest", "adapter-response": "AdapterResponse",
    "result-manifest": "ResultManifest", "search-result": "SearchResult",
}


def dump(data):
    # JSON is a YAML 1.2 subset. This keeps the contract directly machine readable.
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def contract():
    sys.path.insert(0, str(ROOT / "server"))
    from campus.main import create_app

    api = create_app(enable_background=False).openapi()
    api["components"]["securitySchemes"] = {
        "Bearer": {"type": "http", "scheme": "bearer"},
        "BrowserSession": {"type": "apiKey", "in": "cookie", "name": "campus_session"},
    }
    from campus import dto

    for name in {*(SCHEMAS.values()), "Error"}:
        schema = getattr(dto, name).model_json_schema(ref_template="#/components/schemas/{model}")
        for key, value in schema.pop("$defs", {}).items():
            api["components"]["schemas"].setdefault(key, value)
        api["components"]["schemas"].setdefault(name, schema)
    for name in ("HTTPValidationError", "ValidationError"):
        api["components"]["schemas"].pop(name, None)
    public = {"health", "protocol", "redeem_device", "login"}
    device = {
        "join_node", "put_capabilities", "node_telemetry", "claim_work", "attempt_status", "attempt_started",
        "renew_attempt", "complete_attempt", "fail_attempt", "release_attempt", "publish_checkpoint",
        "append_log", "attempt_telemetry", "submit_standalone",
    }
    dual = {"create_upload", "upload_status", "put_upload_part", "finalize_upload", "download_artifact"}
    for path, methods in api["paths"].items():
        for verb, operation in methods.items():
            op = operation["operationId"]
            operation["security"] = [] if op in public else [{"Bearer": []}]
            if op not in device | public:
                operation["security"].append({"BrowserSession": []})
            parameters = operation.setdefault("parameters", [])
            if op in device | dual and op not in {"join_node", "put_capabilities", "node_telemetry", "submit_standalone"}:
                parameters.append({"name": "X-Node-Session", "in": "header", "required": op in device,
                                   "schema": {"type": "string", "format": "uuid"}})
            if op == "login":
                parameters.append({"name": "Origin", "in": "header", "required": True, "schema": {"type": "string"}})
            if verb in {"post", "put", "delete"} and op not in device | public:
                parameters.append({"name": "X-CSRF-Token", "in": "header", "required": False,
                                   "description": "Required for browser cookie authentication.",
                                   "schema": {"type": "string"}})
            if path.endswith("/events"):
                parameters.append({"name": "Last-Event-ID", "in": "header", "required": False,
                                   "schema": {"type": "integer", "minimum": 0}})
                operation["responses"]["200"]["content"]["text/event-stream"] = {"schema": {"type": "string"}}
            if op == "download_artifact":
                parameters.append({"name": "Range", "in": "header", "required": False, "schema": {"type": "string"}})
                for status in ("200", "206"):
                    operation["responses"][status] = {
                        "description": "Artifact bytes", "content": {"application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}}},
                    }
            operation["responses"]["422"] = {
                "description": "Validation error", "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Error"}}},
            }
            operation["responses"]["default"] = {
                "description": "API error", "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Error"}}},
            }
    return api


def schema_documents(api):
    components = api["components"]["schemas"]
    for slug, name in SCHEMAS.items():
        definitions = {}
        needed = {name}
        while needed:
            ref = needed.pop()
            if ref in definitions:
                continue
            encoded = json.dumps(components[ref], ensure_ascii=False)
            needed.update(re.findall(r"#/components/schemas/(\w+)", encoded))
            definitions[ref] = json.loads(encoded.replace("#/components/schemas/", "#/$defs/"))
        yield ROOT / "contracts/schemas" / f"{slug}.schema.json", dump({
            "$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": f"#/$defs/{name}",
            "$defs": definitions,
        })


def ts_type(schema):
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(map(json.dumps, schema["enum"]))
    if "anyOf" in schema:
        return " | ".join(ts_type(item) for item in schema["anyOf"])
    match schema.get("type"):
        case "array":
            return f"Array<{ts_type(schema.get('items', {}))}>"
        case "integer" | "number":
            return "number"
        case "object":
            if "properties" in schema:
                required = set(schema.get("required", ()))
                return "{ " + " ".join(
                    f"{json.dumps(key)}{'' if key in required else '?'}: {ts_type(value)};"
                    for key, value in schema["properties"].items()
                ) + " }"
            additional = schema.get("additionalProperties", {})
            return f"Record<string, {ts_type(additional) if isinstance(additional, dict) else 'unknown'}>"
        case "null" | "boolean" | "string":
            return schema["type"]
        case _:
            return "unknown"


def go_name(name):
    initials = {"id": "ID", "ids": "IDs", "os": "OS", "url": "URL", "cpu": "CPU", "gpu": "GPU",
                "gpus": "GPUs", "cuda": "CUDA", "vram": "VRAM", "sha256": "SHA256", "api": "API"}
    return "".join(initials.get(piece, piece.capitalize()) for piece in name.split("_"))


def go_type(schema):
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "anyOf" in schema:
        alternatives = [item for item in schema["anyOf"] if item.get("type") != "null"]
        return "*" + go_type(alternatives[0]) if len(alternatives) == 1 else "any"
    match schema.get("type"):
        case "array":
            return "[]" + go_type(schema.get("items", {}))
        case "object":
            additional = schema.get("additionalProperties", {})
            return "map[string]" + (go_type(additional) if isinstance(additional, dict) else "any")
        case "integer":
            return "int64"
        case "number":
            return "float64"
        case "boolean":
            return "bool"
        case "string":
            return "string"
        case _:
            return "any"


def generate_types(api, target):
    schemas = api["components"]["schemas"]
    if target == "python":
        names = ", ".join(schemas)
        # The SDK receives a self-contained copy of the exact Pydantic DTO definitions.
        content = (ROOT / "server/campus/dto.py").read_text()
        return f"# Generated by contracts/generate.py.\n{content}\n__all__ = {names.split(', ')!r}\n"
    if target in {"portal", "console"}:
        return "// Generated by contracts/generate.py.\n" + "\n".join(
            f"export type {name} = {ts_type(schema)};" for name, schema in schemas.items()
        ) + "\n"
    output = ["// Code generated by contracts/generate.py. DO NOT EDIT.", "package generated", ""]
    for name, schema in schemas.items():
        if "properties" not in schema:
            underlying = go_type(schema)
            output.extend((f"type {name} {underlying}", ""))
            for value in schema.get("enum", ()):
                output.append(f"const {name}{go_name(value)} {name} = {json.dumps(value)}")
            output.append("")
            continue
        required = set(schema.get("required", ()))
        output.append(f"type {name} struct {{")
        for key, value in schema["properties"].items():
            suffix = "" if key in required else ",omitempty"
            field_type = go_type(value)
            if suffix and not field_type.startswith(("*", "[]", "map[", "any")):
                field_type = "*" + field_type
            output.append(f'\t{go_name(key)} {field_type} `json:"{key}{suffix}"`')
        output.extend(("}", ""))
    source = "\n".join(output).rstrip() + "\n"
    if shutil.which("gofmt"):
        source = subprocess.run(["gofmt"], input=source, check=True, text=True, capture_output=True).stdout
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=[*TARGETS, "all"])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    if args.export:
        api = contract()
        CONTRACT.write_text(dump(api))
        for path, content in schema_documents(api):
            path.write_text(content)
    if args.target:
        api = json.loads(CONTRACT.read_text())
        targets = TARGETS if args.target == "all" else [args.target]
        if args.out and len(targets) != 1:
            parser.error("--out requires one target")
        for target in targets:
            path = args.out or TARGETS[target]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(generate_types(api, target))
    if not (args.export or args.target):
        parser.error("use --target or --export")


if __name__ == "__main__":
    main()
