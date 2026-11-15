"""Content identity of trusted runtime code, never of request-supplied paths.

The prepared image must make these sources immutable. LF normalization makes a
checkout's CRLF/LF convention irrelevant to the code/benchmark identity.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REGISTERED_VERSIONS = {"embeddings.e5": "1", "training.image-classifier": "1", "generic.project": "1"}

# Conservative: changing any registered runtime implementation invalidates the
# bundle identity for all adapters. This is a literal audited allowlist, not a
# manifest field, module discovery, glob or import of third-party/user code.
_TRUSTED_SOURCES = (
    "__init__.py", "adapter.py", "common.py", "provenance.py",
    "embeddings/__init__.py", "embeddings/adapter.py", "embeddings/backend.py",
    "embeddings/prepare.py", "embeddings/run.py",
    "training/__init__.py", "training/adapter.py", "training/runtime.py",
    "training/run.py", "training/state.py",
    "generic/__init__.py", "generic/adapter.py",
)


def adapter_digest(adapter_id, version):
    if REGISTERED_VERSIONS.get(adapter_id) != version or adapter_id not in REGISTERED_VERSIONS:
        raise ValueError("adapter id/version is not registered")
    root = Path(__file__).resolve().parent
    files = {}
    for relative in _TRUSTED_SOURCES:
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1 << 20:
            raise ValueError("trusted adapter source is missing, linked or oversized")
        raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        files[relative] = hashlib.sha256(raw).hexdigest()
    identity = {"schema_version": "1", "adapter": {"id": adapter_id, "version": version}, "files": files}
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def adapter_metadata(adapter_id, version):
    return {"adapter": {"id": adapter_id, "version": REGISTERED_VERSIONS[adapter_id]},
            "adapter_digest": adapter_digest(adapter_id, version), "adapter_digest_kind": "trusted_source_lf_sha256"}
