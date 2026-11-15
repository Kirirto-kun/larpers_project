from __future__ import annotations

import fnmatch
from pathlib import Path

from workloads.common import digest, file_digest, manifest_header, no_links

ADAPTER = {"id": "generic.project", "version": "1"}
_EXCLUDED_DIRS = {".git", ".campus-env", ".campus-site", ".venv", "venv", "node_modules", "__pycache__", ".ssh", ".aws", ".azure", ".config"}
_EXCLUDED_FILES = {".env", ".campusignore", "credentials", "credentials.json", "id_rsa", "id_ed25519"}


def validate_input(manifest):
    manifest_header(manifest, "generic")
    command = manifest.get("command")
    if (not isinstance(command, list) or not 1 <= len(command) <= 256
            or any(not isinstance(arg, str) or not arg or "\x00" in arg or len(arg) > 8192 for arg in command)):
        raise ValueError("generic command requires bounded argv strings")
    return {"valid": True, "adapter": ADAPTER, "manifest_digest": digest(manifest),
            "model_digest": "", "model_kind": "none",
            "quality": "unverified", "benchmark_eligible": False}


def split(manifest, batch_policy):
    report = validate_input(manifest)
    return [{"schema_version": "1", "adapter": ADAPTER, "task_id": report["manifest_digest"],
             "manifest_digest": report["manifest_digest"], "command": manifest["command"]}]


def verify(inputs, outputs, policy=None):
    report = validate_input(inputs)
    return {"accepted": True, "quality": "unverified", "benchmark_eligible": False,
            "manifest_digest": report["manifest_digest"], "model_digest": "", "model_kind": "none",
            "coverage": "none", "checked_items": 0,
            "message": "Execution completed; files saved; quality has not been verified."}


def aggregate(accepted_outputs):
    report = verify(accepted_outputs["manifest"], accepted_outputs["outputs"], accepted_outputs.get("policy"))
    return {"schema_version": "1", "adapter": ADAPTER, "outputs": accepted_outputs["outputs"], **report}


def snapshot(root, *, max_files=10000, max_bytes=1 << 30):
    root = no_links(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project root must be a directory")
    patterns = []
    ignore = root / ".campusignore"
    if ignore.exists():
        no_links(ignore)
        if ignore.stat().st_size > 1 << 20:
            raise ValueError("ignore file exceeds quota")
        patterns = [line.strip() for line in ignore.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
    result, total = {}, 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        parts = path.relative_to(root).parts
        if (any(part.lower() in _EXCLUDED_DIRS for part in parts)
                or path.name.lower() in _EXCLUDED_FILES or path.name.lower().startswith(".env.")
                or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
                or any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, pattern.rstrip("/") + "/*") for pattern in patterns)):
            continue
        no_links(path)
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if len(result) >= max_files or total > max_bytes:
            raise ValueError("output capture quota exceeded; choose explicit outputs or increase the quota")
        result[relative] = {"path": relative, "size_bytes": size, "digest": file_digest(path, max_bytes=max_bytes)}
    return result


def collect_outputs(root, before, *, max_files=10000, max_bytes=1 << 30):
    # Scan quota includes original files to bound traversal; output quota applies
    # to every changed file. Large projects should use the runner's packaging manifest.
    current = snapshot(root, max_files=max_files, max_bytes=max(1 << 30, max_bytes))
    changed = [entry for name, entry in current.items() if name not in before or before[name]["digest"] != entry["digest"]]
    if sum(entry["size_bytes"] for entry in changed) > max_bytes or len(changed) > max_files:
        raise ValueError("output capture quota exceeded; files were not silently discarded")
    return {"schema_version": "1", "adapter": ADAPTER, "files": changed,
            "quality": "unverified", "benchmark_eligible": False}
