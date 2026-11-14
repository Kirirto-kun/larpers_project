"""Atomic, content-verified checkpoint envelopes. Never unpickles user data."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid

_BINDINGS = {"code_digest", "input_digest", "environment_digest", "trainer_digest"}


def safe_path(name: str) -> str:
    p = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or "\x00" in name or p.is_absolute()
            or str(p) != name or any(x in {".", ".."} or x.rstrip(" .") != x for x in p.parts)):
        raise ValueError(f"unsafe relative path: {name!r}")
    for part in p.parts:
        if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0]):
            raise ValueError("reserved path")
    return name


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _binding(binding):
    if set(binding) != _BINDINGS or not all(isinstance(x, str) and re.fullmatch("[0-9a-f]{64}", x) for x in binding.values()):
        raise ValueError("checkpoint requires code/input/environment/trainer SHA-256 binding")
    return dict(binding)


def _sync_dir(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def save_checkpoint(directory, name: str, files: dict[str, bytes], binding: dict,
                    *, max_bytes: int = 1 << 30) -> Path:
    """Write immutable payloads, manifest, then READY, then atomically rename.

    Callers must include every model/optimizer/scheduler/RNG/sampler state needed
    by their trainer. This function does not make an arbitrary script resumable.
    """
    safe_path(name)
    if "/" in name or not files or len(files) > 1000:
        raise ValueError("invalid checkpoint name or file count")
    binding = _binding(binding)
    seen = set()
    size = 0
    for name_, data in files.items():
        safe_path(name_)
        if name_ in {"manifest.json", "READY"} or name_.casefold() in seen or not isinstance(data, bytes):
            raise ValueError("invalid checkpoint payload")
        seen.add(name_.casefold())
        size += len(data)
    if size > max_bytes:
        raise ValueError("checkpoint quota exceeded")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    if destination.exists():
        raise FileExistsError(destination)
    staging = directory / (".staging-" + uuid.uuid4().hex)
    staging.mkdir(mode=0o700)
    try:
        manifest = {"schema_version": "1", "binding": binding, "files": []}
        for relative, data in sorted(files.items()):
            _write(staging / relative, data)
            manifest["files"].append({"path": relative, "size_bytes": len(data), "digest": hashlib.sha256(data).hexdigest()})
        raw = canonical(manifest)
        _write(staging / "manifest.json", raw)
        _write(staging / "READY", hashlib.sha256(raw).hexdigest().encode("ascii"))
        _sync_dir(staging)
        # Exclusive mkdir name is enforced above; concurrent writers use distinct names.
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(staging, destination)
        _sync_dir(directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def load_checkpoint(directory, binding, *, max_bytes: int = 1 << 30) -> dict[str, bytes]:
    binding = _binding(binding)
    directory = Path(directory)
    if directory.is_symlink() or not (directory / "READY").is_file():
        raise ValueError("checkpoint is not complete")
    root = directory.resolve(strict=True)

    def read(name, limit):
        p = directory / safe_path(name)
        for part in [p, *p.parents]:
            if part == directory.parent:
                break
            if part.is_symlink():
                raise ValueError("checkpoint symlink")
        if not p.resolve().is_relative_to(root) or not p.is_file() or p.stat().st_size > limit:
            raise ValueError("checkpoint quota/path violation")
        with p.open("rb") as f:
            raw = f.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("checkpoint quota exceeded")
        return raw

    raw = read("manifest.json", 1 << 20)
    if read("READY", 64) != hashlib.sha256(raw).hexdigest().encode("ascii"):
        raise ValueError("checkpoint manifest digest mismatch")
    manifest = json.loads(raw)
    if manifest.get("schema_version") != "1" or manifest.get("binding") != binding:
        raise ValueError("checkpoint is not compatible with this code/input/environment/trainer")
    if not isinstance(manifest.get("files"), list) or not 0 < len(manifest["files"]) <= 1000:
        raise ValueError("invalid checkpoint manifest")
    files = {}
    total = 0
    seen = set()
    for entry in manifest["files"]:
        name = safe_path(entry["path"])
        if name.casefold() in seen or name in {"READY", "manifest.json"}:
            raise ValueError("duplicate checkpoint path")
        seen.add(name.casefold())
        data = read(name, max_bytes - total)
        total += len(data)
        if len(data) != entry["size_bytes"] or hashlib.sha256(data).hexdigest() != entry["digest"]:
            raise ValueError("checkpoint payload digest mismatch")
        files[name] = data
    return files


def publish_checkpoint(directory, binding, transport):
    """Finalize every artifact before publishing the server's checkpoint pointer.

    transport.upload(path) must return an already finalized immutable artifact;
    transport.publish(manifest) performs the attempt-scoped checkpoint mutation.
    """
    files = load_checkpoint(directory, binding)
    manifest = json.loads((Path(directory) / "manifest.json").read_bytes())
    artifacts = []
    for name in [*sorted(files), "manifest.json", "READY"]:
        artifact = transport.upload(Path(directory) / name)
        artifacts.append({"path": name, **artifact})
    return transport.publish({"schema_version": "1", "binding": binding,
                              "files": artifacts,
                              "manifest_digest": hashlib.sha256(canonical(manifest)).hexdigest()})
