"""Bounded snapshot and export primitives for the trusted container supervisor."""
from __future__ import annotations
import hashlib
import fnmatch
import json
import os
from pathlib import Path
import stat
import zipfile
from campus.checkpoint import safe_path, canonical


def secret(name):
    for part in name.lower().split("/"):
        if (part in {".git", ".campus-env", ".campus-site", ".venv", "venv", "node_modules", "__pycache__", ".ssh", ".aws", ".azure", ".config", ".campus", ".env", ".netrc", ".npmrc", "credentials", "credentials.json"}
                or part.startswith((".env.", "id_rsa", "id_ed25519")) or part.endswith((".pem", ".key", ".p12"))):
            return True
    return False


def read_regular(root, relative, max_bytes):
    """On Linux, walk directory descriptors: no symlink or rename can escape root."""
    safe_path(relative)
    if os.name == "posix":
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = relative.split("/")
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:
            os.close(fd)
        f = os.fdopen(file_fd, "rb")
    else:
        p = Path(root) / relative
        if p.is_symlink() or not p.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("symlink output")
        f = p.open("rb")
    with f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            raise ValueError("file quota/type violation")
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("file quota exceeded")
    return data


def scan(root, max_files, max_bytes, *, exclude_secrets=True):
    root = Path(root)
    result = {}
    total = 0
    if not root.exists():
        return result
    seen = set()
    patterns=[]
    if exclude_secrets and (root / ".campusignore").is_file():
        patterns=read_regular(root,".campusignore",65536).decode("utf-8").splitlines()
    def excluded(name):
        if not exclude_secrets: return False
        if secret(name): return True
        skip=False
        for pattern in patterns:
            pattern=pattern.strip()
            if not pattern or pattern.startswith("#"): continue
            negate=pattern.startswith("!")
            pattern=pattern.lstrip("!").lstrip("/")
            directory=pattern.endswith("/"); pattern=pattern.rstrip("/")
            match=(fnmatch.fnmatchcase(name,pattern) or directory and (name==pattern or name.startswith(pattern+"/"))
                   or "/" not in pattern and any(fnmatch.fnmatchcase(part,pattern) for part in name.split("/")))
            if match: skip=not negate
        return skip
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            p = Path(parent) / name
            rel = p.relative_to(root).as_posix()
            if excluded(rel):
                dirs.remove(name)
            elif p.is_symlink():
                raise ValueError("symlink directory")
        for name in sorted(files):
            p = Path(parent) / name
            relative = p.relative_to(root).as_posix()
            if excluded(relative):
                continue
            safe_path(relative)
            if relative.casefold() in seen or len(result) >= max_files:
                raise ValueError("file count/collision quota")
            seen.add(relative.casefold())
            data = read_regular(root, relative, max_bytes - total)
            total += len(data)
            result[relative] = {"path": relative, "size_bytes": len(data), "digest": hashlib.sha256(data).hexdigest()}
    return result


def extract_project(archive, destination, *, max_bytes, max_files=10000):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) > max_files:
            raise ValueError("archive file quota exceeded")
        total = 0
        seen = set()
        for entry in z.infolist():
            name = entry.filename.rstrip("/")
            safe_path(name)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError("archive special file")
            if name.casefold() in seen:
                raise ValueError("duplicate archive path")
            seen.add(name.casefold())
            total += entry.file_size
            if total > max_bytes:
                raise ValueError("archive byte quota exceeded")
        destination.mkdir(parents=True, exist_ok=True)
        written = 0
        for entry in z.infolist():
            p = destination / entry.filename.rstrip("/")
            if entry.is_dir():
                p.mkdir(parents=True, exist_ok=True)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as source, p.open("xb") as out:
                while data := source.read(min(1 << 20, max_bytes - written + 1)):
                    written += len(data)
                    if written > max_bytes:
                        raise ValueError("archive quota exceeded")
                    out.write(data)
            p.chmod(0o640)


def capture(workspace, outputs, destination, before, declared, *, max_bytes, max_files=10000):
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    current = scan(workspace, max_files, max_bytes * 16)
    explicit = scan(outputs, max_files, max_bytes)
    for name in declared:
        safe_path(name)
        if not any(p == name or p.startswith(name + "/") for p in current):
            raise ValueError(f"declared output is missing: {name}")
        if secret(name):
            raise ValueError("declared output is excluded by secret policy")
    candidates = [("outputs", Path(outputs), name, entry) for name, entry in explicit.items()]
    candidates += [("workspace", Path(workspace), name, entry) for name, entry in current.items()
                   if before.get(name, {}).get("digest") != entry["digest"] or any(name == p or name.startswith(p + "/") for p in declared)]
    files = []
    total = 0
    for prefix, root, name, expected in sorted(candidates, key=lambda x: (x[0], x[2])):
        if len(files) >= max_files:
            raise ValueError("output file quota exceeded")
        data = read_regular(root, name, max_bytes - total)
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected["digest"]:
            raise ValueError("output changed during capture")
        total += len(data)
        relative = prefix + "/" + name
        p = destination / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("xb") as f:
            f.write(data)
            f.flush(); os.fsync(f.fileno())
        files.append({"path": relative, "digest": digest, "size_bytes": len(data)})
    result = {"schema_version": "1", "files": files, "total_bytes": total,
              "quality": "unverified", "benchmark_eligible": False}
    (destination / "manifest.json").write_bytes(canonical(result))
    return result
