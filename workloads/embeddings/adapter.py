from __future__ import annotations

from datetime import datetime, timezone
import time
import unicodedata

import numpy as np

from workloads.common import (digest, file_digest, integer, manifest_header, no_links,
                              parse_json, read_json, regular_file, require_digest,
                              safe_relative, write_json)

ADAPTER = {"id": "embeddings.e5", "version": "1"}
TOLERANCES = {"atol": 0.0005, "cosine_min": 0.9999, "norm_atol": 0.001}
_FIXED = {"dimension": 384, "max_length": 512, "precision": "float32", "normalize": "l2",
          "passage_prefix": "passage: ", "query_prefix": "query: ", "unicode": "NFC"}


def _model(model):
    if not isinstance(model, dict) or model.get("id") != "intfloat/multilingual-e5-small":
        raise ValueError("only registered intfloat/multilingual-e5-small is supported")
    require_digest(model.get("revision"), 40)
    root = no_links(model["path"])
    if not root.is_dir():
        raise ValueError("local model directory is unavailable; prepare it before running offline")
    expected = model.get("files")
    if not isinstance(expected, dict) or not 3 <= len(expected) <= 100:
        raise ValueError("model requires a complete checksum file lock")
    actual = {}
    total = 0
    for path in sorted(root.rglob("*")):
        no_links(path)
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            safe_relative(relative)
            total += path.stat().st_size
            if total > 2 << 30:
                raise ValueError("model exceeds 2 GiB quota")
            actual[relative] = file_digest(path)
    if set(actual) != set(expected):
        raise ValueError("model file lock does not cover all local files")
    for name, value in expected.items():
        safe_relative(name)
        if require_digest(value) != actual[name]:
            raise ValueError(f"model digest mismatch: {name}")
    if "config.json" not in actual or "tokenizer_config.json" not in actual or not any(name.endswith(".safetensors") for name in actual):
        raise ValueError("model needs config/tokenizer and safe safetensors weights")
    if any(name.endswith((".bin", ".pt", ".pth", ".pkl", ".pickle", ".py")) for name in actual):
        raise ValueError("unsafe or executable model files are forbidden")
    config = read_json(root / "config.json")
    tokenizer = read_json(root / "tokenizer_config.json")
    if config.get("auto_map") or tokenizer.get("auto_map"):
        raise ValueError("HF custom remote code is forbidden")
    if config.get("model_type") != "bert" or config.get("hidden_size") != 384 or config.get("max_position_embeddings", 0) < 512:
        raise ValueError("model config must be BERT/E5 with 384 dimensions and at least 512 positions")
    return {"id": model["id"], "revision": model["revision"], "files": actual}


def _prepare(manifest):
    manifest_header(manifest, "embeddings")
    model = _model(manifest["model"])
    preprocessing = {**_FIXED, "chunk_chars": 1000, "overlap_chars": 100}
    supplied = manifest.get("preprocessing", {})
    if not isinstance(supplied, dict) or set(supplied) - set(preprocessing):
        raise ValueError("unknown preprocessing option")
    preprocessing.update(supplied)
    if any(preprocessing[key] != value for key, value in _FIXED.items()):
        raise ValueError("registered E5 preprocessing/precision cannot be changed")
    chunk = integer(preprocessing["chunk_chars"], "chunk_chars", maximum=20000)
    overlap = integer(preprocessing["overlap_chars"], "overlap_chars", minimum=0, maximum=chunk - 1)
    sources = manifest.get("inputs")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 1000:
        raise ValueError("inputs must contain 1..1000 local text files")
    items, source_locks, seen_sources = [], [], set()
    for entry in sources:
        path = regular_file(entry["path"])
        raw = path.read_bytes()
        if len(raw) > 64 << 20:
            raise ValueError("input exceeds quota")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("text inputs must be UTF-8") from exc
        source_hash = file_digest(path)
        if entry.get("sha256", source_hash) != source_hash:
            raise ValueError("input digest mismatch")
        source = entry.get("source", path.name)
        if not isinstance(source, str) or not source or len(source) > 1000 or source in seen_sources:
            raise ValueError("input source names must be unique nonempty strings")
        seen_sources.add(source)
        fmt = entry.get("format", path.suffix.lstrip("."))
        if fmt not in {"txt", "jsonl"}:
            raise ValueError("inputs support only txt/jsonl")
        source_locks.append({"source": source, "sha256": source_hash, "format": fmt})
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            external_id = None
            if fmt == "jsonl":
                record = parse_json(line)
                if not isinstance(record, dict) or not isinstance(record.get("text"), str):
                    raise ValueError("JSONL records require a text string")
                line = record["text"]
                external_id = record.get("id")
                if external_id is not None and (not isinstance(external_id, str) or len(external_id) > 1000):
                    raise ValueError("JSONL id must be a bounded string")
            line = unicodedata.normalize("NFC", line.replace("\r\n", "\n").replace("\r", "\n"))
            if not line.strip():
                continue
            for start in range(0, len(line), chunk - overlap):
                end = min(start + chunk, len(line))
                item = {"source": source, "source_digest": source_hash, "line": line_number,
                        "start": start, "end": end, "text": line[start:end], "external_id": external_id}
                items.append({"item_id": digest(item), **item})
                if len(items) > 100000:
                    raise ValueError("preprocessing exceeds 100000 item quota")
                if end == len(line):
                    break
    if not items:
        raise ValueError("inputs contain no nonempty text")
    binding = {"adapter": ADAPTER, "model": model, "preprocessing": preprocessing,
               "sources": source_locks, "items": items}
    return {"manifest_digest": digest(binding), "model_digest": digest(model),
            "preprocessing_digest": digest(preprocessing), "items": items, "preprocessing": preprocessing}


def validate_input(manifest):
    prepared = _prepare(manifest)
    return {"valid": True, "adapter": ADAPTER, "manifest_digest": prepared["manifest_digest"],
            "model_digest": prepared["model_digest"], "units": len(prepared["items"]),
            "model_kind": "pretrained_transformer", "model_id": "intfloat/multilingual-e5-small",
            "preprocessing_digest": prepared["preprocessing_digest"],
            "preprocessing": prepared["preprocessing"], "offline": True}


def split(manifest, batch_policy):
    prepared = _prepare(manifest)
    count = integer(batch_policy.get("items_per_task", 128), "items_per_task", maximum=100000)
    tasks = []
    for offset in range(0, len(prepared["items"]), count):
        items = prepared["items"][offset:offset + count]
        ids = [item["item_id"] for item in items]
        tasks.append({"schema_version": "1", "adapter": ADAPTER, "manifest_digest": prepared["manifest_digest"],
                      "task_id": digest({"manifest": prepared["manifest_digest"], "item_ids": ids}),
                      "item_ids": ids, "items": items})
    return tasks


def _encode(backend, texts, microbatch_size):
    rows, offset, retries = [], 0, 0
    size = integer(microbatch_size, "microbatch_size", maximum=4096)
    while offset < len(texts):
        batch = texts[offset:offset + size]
        try:
            vectors = np.asarray(backend.encode(batch), dtype=np.float32)
            if vectors.shape != (len(batch), 384) or not np.isfinite(vectors).all():
                raise ValueError("encoder returned nonfinite or wrong-dimensional vectors")
            if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=TOLERANCES["norm_atol"], rtol=0):
                raise ValueError("encoder returned vectors without unit normalization")
            rows.extend(vectors.tolist())
            offset += len(batch)
        except (RuntimeError, MemoryError) as exc:
            if not isinstance(exc, MemoryError) and not any(message in str(exc).lower() for message in
                    ("out of memory", "not enough memory", "can't allocate memory", "cannot allocate memory")):
                raise
            if size == 1:
                raise ValueError("out of memory even at microbatch size 1") from exc
            size = max(1, size // 2)
            retries += 1
            if hasattr(backend, "release_oom"):
                backend.release_oom()
    return rows, {"oom_retries": retries, "final_microbatch_size": size}


def run(manifest, batch_policy=None, *, backend=None, device="cpu", item_ids=None):
    start = time.monotonic()
    prepared = _prepare(manifest)
    items = prepared["items"]
    if item_ids is not None:
        if not isinstance(item_ids, list) or not item_ids or len(set(item_ids)) != len(item_ids):
            raise ValueError("task item_ids must be unique and nonempty")
        by_id = {item["item_id"]: item for item in items}
        if any(item_id not in by_id for item_id in item_ids):
            raise ValueError("task item_ids do not belong to manifest")
        items = [by_id[item_id] for item_id in item_ids]
    if backend is None:
        from .backend import E5Encoder
        backend = E5Encoder(manifest["model"], device=device)
    vectors, stats = _encode(backend, ["passage: " + item["text"] for item in items],
                             (batch_policy or {}).get("microbatch_size", 32))
    return {"schema_version": "1", "adapter": ADAPTER,
            **{key: prepared[key] for key in ("manifest_digest", "model_digest", "preprocessing_digest")},
            "vectors": [{"item_id": item["item_id"], "vector": vector} for item, vector in zip(items, vectors)],
            "stats": {**stats, "execution_duration_ms": (time.monotonic() - start) * 1000,
                      "units": len(items), "device": device}}


def create_reference(manifest, output_path, *, backend=None, device="cpu", device_id="local-reference"):
    started_at = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    result = run(manifest, backend=backend, device=device)
    reference = {**result, "type": "e5_full_reference", "tolerances": dict(TOLERANCES),
                 "reference_device_id": device_id, "reference_started_at": started_at,
                 "reference_duration_ms": (time.monotonic() - start) * 1000}
    write_json(output_path, reference)
    return reference


def _outputs(prepared, outputs, norm_atol, expected_ids=None):
    if not isinstance(outputs, list):
        outputs = [outputs]
    expected = {item["item_id"] for item in prepared["items"]}
    if expected_ids is not None:
        if (not isinstance(expected_ids, list) or not expected_ids or any(not isinstance(value, str) for value in expected_ids)
                or len(set(expected_ids)) != len(expected_ids) or not set(expected_ids).issubset(expected)):
            raise ValueError("assigned item_ids must be a unique nonempty subset of the current manifest")
        expected = set(expected_ids)
    rows = {}
    for output in outputs:
        if (not isinstance(output, dict) or output.get("schema_version") != "1" or output.get("adapter") != ADAPTER
                or any(output.get(key) != prepared[key] for key in ("manifest_digest", "model_digest", "preprocessing_digest"))):
            raise ValueError("output manifest/model/preprocessing binding mismatch")
        if not isinstance(output.get("vectors"), list):
            raise ValueError("output requires vectors")
        for row in output["vectors"]:
            item_id = row["item_id"]
            if item_id not in expected or item_id in rows:
                raise ValueError("duplicate or foreign item_id")
            values = row["vector"]
            if not isinstance(values, list) or len(values) != 384 or any(type(v) not in (float, int) for v in values):
                raise ValueError("vector must contain 384 numeric values")
            vector = np.asarray(values, dtype=np.float64)
            if not np.isfinite(vector).all() or abs(float(np.linalg.norm(vector)) - 1) > norm_atol:
                raise ValueError("nonfinite vector or invalid unit norm")
            rows[item_id] = vector
    if set(rows) != expected:
        raise ValueError("missing item coverage")
    return rows


def verify(inputs, outputs, policy=None):
    start = time.monotonic()
    policy = policy or {}
    report = {"accepted": False, "quality": "rejected", "benchmark_eligible": False,
              "coverage": "none", "checked_items": 0, "errors": []}
    try:
        if not isinstance(policy, dict) or set(policy) - {"mode", "reference_path", "tolerances", "item_ids"}:
            raise ValueError("unknown or invalid embedding validation policy")
        prepared = _prepare(inputs)
        report.update(manifest_digest=prepared["manifest_digest"], model_digest=prepared["model_digest"],
                      preprocessing_digest=prepared["preprocessing_digest"],
                      model_kind="pretrained_transformer", model_id="intfloat/multilingual-e5-small")
        mode = policy.get("mode", "structural")
        if mode not in {"structural", "full_reference"}:
            raise ValueError("validation mode must be structural or full_reference")
        tolerances = dict(TOLERANCES)
        reference = None
        if mode == "full_reference":
            reference = read_json(policy["reference_path"], max_bytes=2 << 30)
            if reference.get("type") != "e5_full_reference" or reference.get("tolerances") != TOLERANCES:
                raise ValueError("reference type/tolerance policy mismatch")
        if policy.get("tolerances", tolerances) != tolerances:
            raise ValueError("tolerances are fixed before the series and cannot be retargeted")
        actual = _outputs(prepared, outputs, tolerances["norm_atol"], policy.get("item_ids"))
        if reference is not None:
            expected = _outputs(prepared, reference, tolerances["norm_atol"])
            for item_id, vector in actual.items():
                gold = expected[item_id]
                cosine = float(np.dot(vector, gold) / (np.linalg.norm(vector) * np.linalg.norm(gold)))
                if not np.allclose(vector, gold, atol=tolerances["atol"], rtol=0) or cosine < tolerances["cosine_min"]:
                    raise ValueError(f"reference vector mismatch: {item_id}")
            report["reference_device_id"] = reference["reference_device_id"]
            report["reference_duration_ms"] = reference["reference_duration_ms"]
        report.update(accepted=True, quality="verified" if reference else "structural_only",
                      benchmark_eligible=reference is not None, coverage=mode,
                      checked_items=len(actual), manifest_digest=prepared["manifest_digest"],
                      validation_scope="assigned_items" if policy.get("item_ids") is not None else "entire_manifest",
                      manifest_items=len(prepared["items"]))
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as exc:
        report["errors"].append(str(exc))
    report["validation_duration_ms"] = (time.monotonic() - start) * 1000
    return report


def aggregate(accepted_outputs):
    manifest = accepted_outputs["manifest"]
    outputs = accepted_outputs["outputs"]
    if "item_ids" in (accepted_outputs.get("policy") or {}):
        raise ValueError("aggregate requires complete manifest coverage, without an assigned-item subset policy")
    report = verify(manifest, outputs, accepted_outputs.get("policy"))
    if not report["accepted"]:
        raise ValueError("; ".join(report["errors"]))
    prepared = _prepare(manifest)
    rows = _outputs(prepared, outputs, TOLERANCES["norm_atol"])
    return {"schema_version": "1", "adapter": ADAPTER, "type": "e5_search_index",
            **{key: prepared[key] for key in ("manifest_digest", "model_digest", "preprocessing_digest")},
            "vectors": [{"item_id": item["item_id"], "vector": rows[item["item_id"]].tolist(),
                         **{key: item[key] for key in ("source", "source_digest", "line", "start", "end", "text", "external_id")}}
                        for item in prepared["items"]],
            "quality": report["quality"], "benchmark_eligible": report["benchmark_eligible"],
            "validation": report, "units": len(rows)}


def search(manifest, index, query, top_k=5, *, backend=None, device="cpu"):
    if not isinstance(query, str) or not query.strip() or len(query) > 20000:
        raise ValueError("query must be a nonempty bounded string")
    top_k = integer(top_k, "top_k", maximum=1000)
    prepared = _prepare(manifest)
    vectors = _outputs(prepared, index, TOLERANCES["norm_atol"])
    if backend is None:
        from .backend import E5Encoder
        backend = E5Encoder(manifest["model"], device=device)
    query = unicodedata.normalize("NFC", query)
    encoded, _ = _encode(backend, ["query: " + query], 1)
    q = np.asarray(encoded[0])
    items = {item["item_id"]: item for item in prepared["items"]}
    ranked = sorted(((float(np.dot(vector, q)), item_id) for item_id, vector in vectors.items()),
                    key=lambda entry: (-entry[0], entry[1]))[:top_k]
    return [{"item_id": item_id, "score": score, "source": items[item_id]["source"],
             "line": items[item_id]["line"], "start": items[item_id]["start"],
             "end": items[item_id]["end"], "excerpt": items[item_id]["text"]} for score, item_id in ranked]
