"""Small data-only helpers shared by the trusted adapter processes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def require_digest(value, length=64):
    if not isinstance(value, str) or not re.fullmatch(f"[0-9a-f]{{{length}}}", value):
        raise ValueError(f"expected a pinned {length}-character hexadecimal digest")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json(raw):
    def invalid_constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid_constant)


def no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("symlink/junction paths are forbidden")
    return path


def regular_file(path, max_bytes=64 << 20) -> Path:
    path = no_links(path)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("file is missing, not regular, or exceeds quota")
    return path


def file_digest(path, max_bytes=2 << 30):
    path = regular_file(path, max_bytes)
    total = 0
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            total += len(block)
            if total > max_bytes:
                raise ValueError("file exceeds quota")
            h.update(block)
    return h.hexdigest()


def read_json(path, max_bytes=16 << 20):
    raw = regular_file(path, max_bytes).read_bytes()
    if len(raw) > max_bytes:
        raise ValueError("JSON file exceeds quota")
    try:
        return parse_json(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("input must be UTF-8") from exc


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError("unsafe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(p in {".", ".."} or p.rstrip(" .") != p for p in path.parts):
        raise ValueError("unsafe relative path")
    return value


def confined_file(root, relative, max_bytes=64 << 20):
    root = no_links(root).resolve(strict=True)
    path = regular_file(root / safe_relative(relative), max_bytes)
    if not path.resolve().is_relative_to(root):
        raise ValueError("file escapes dataset root")
    return path


def atomic_bytes(path, raw):
    path = no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def write_json(path, value):
    return atomic_bytes(path, canonical(value))


def manifest_header(manifest, kind):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1" or manifest.get("kind") != kind:
        raise ValueError(f"expected schema_version=1 {kind} manifest")
    if any(key in manifest for key in ("validator", "validator_path", "validator_module", "remote_code")):
        raise ValueError("user supplied validators/remote code are not accepted")


def integer(value, name, minimum=1, maximum=1_000_000):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value
