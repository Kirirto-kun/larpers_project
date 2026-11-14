"""One global nearest-centroid model, expressed as four ordinary project stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Keep the default one-core task comparable across local and container runs.
for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import numpy as np

ALGORITHM = "nearest-centroid-uint8-l2-v1"
CLASSES = 10
FEATURES = 784
MAX_JSON_BYTES = 1048576


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError(f"non-finite JSON number: {value}")


def read_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f"JSON exceeds {MAX_JSON_BYTES} bytes: {path.name}")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream, object_pairs_hook=_object, parse_constant=_nonfinite)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")  # noqa: TRY004 - invalid serialized input
    return value


def write_json(path, value):
    encoded = canonical_json(value) + b"\n"
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("output exceeds the declared 1 MiB limit")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def _integer(value, lower, upper, name):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"invalid {name}")
    return value


def _integer_array(value, shape, lower, upper, name):
    # Object conversion preserves booleans in mixed JSON lists instead of
    # silently promoting [false, 1] into the integer class labels [0, 1].
    array = np.asarray(value, dtype=object)
    if array.shape != shape or not all(type(item) is int for item in array.flat):
        raise ValueError(f"invalid {name} shape or integer type")
    if np.any(array < lower) or np.any(array > upper):
        raise ValueError(f"invalid {name} range")
    return array.astype(np.int64)


def load_manifest(path):
    manifest = read_json(path)
    if (manifest.get("schema_version") != "1" or manifest.get("dataset") != "fashion-mnist"
            or manifest.get("classes") != CLASSES or manifest.get("features") != FEATURES):
        raise ValueError("unsupported Fashion-MNIST manifest")
    _integer(manifest.get("train_count"), 1, 60000, "train_count")
    _integer(manifest.get("test_count"), 1, 10000, "test_count")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or not 2 <= len(shards) <= 512:
        raise ValueError("manifest must contain 2 to 512 shards")
    seen_ids, seen_paths = set(), set()
    for shard in shards:
        if not isinstance(shard, dict):
            raise ValueError("invalid shard metadata")  # noqa: TRY004 - invalid serialized input
        split, shard_id = shard.get("split"), shard.get("id")
        if split not in ("train", "test") or not isinstance(shard_id, str):
            raise ValueError("invalid split or shard id")
        if not re.fullmatch(rf"{split}-[0-9]{{5}}", shard_id):
            raise ValueError("invalid shard id")
        if shard_id in seen_ids or shard.get("path") in seen_paths:
            raise ValueError("duplicate shard")
        if shard.get("path") != f"dataset/{shard_id}.npz":
            raise ValueError("unsafe or inconsistent dataset path")
        if not isinstance(shard.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", shard["sha256"]):
            raise ValueError("invalid shard checksum")
        count = _integer(shard.get("count"), 1, manifest[f"{split}_count"], "shard count")
        start = _integer(shard.get("start"), 0, manifest[f"{split}_count"] - 1, "shard start")
        stop = _integer(shard.get("stop"), 1, manifest[f"{split}_count"], "shard stop")
        if stop - start != count:
            raise ValueError("shard count disagrees with item range")
        seen_ids.add(shard_id)
        seen_paths.add(shard["path"])
    for split in ("train", "test"):
        position = 0
        for shard in sorted((s for s in shards if s["split"] == split), key=lambda s: s["start"]):
            if shard["start"] != position:
                raise ValueError(f"{split} shards have a gap or overlap")
            position = shard["stop"]
        if position != manifest[f"{split}_count"]:
            raise ValueError(f"incomplete {split} coverage")
    return manifest, sha256(path)


def _source(shard):
    return {key: shard[key] for key in ("id", "split", "path", "sha256", "count", "start", "stop")} | {
        "item_ids": list(range(shard["start"], shard["stop"]))}


def _load_shard(path, manifest, split):
    path = Path(path)
    matches = [s for s in manifest["shards"] if s["split"] == split and Path(s["path"]).name == path.name]
    if len(matches) != 1:
        raise ValueError(f"input is not a declared {split} shard")
    shard = matches[0]
    if sha256(path) != shard["sha256"]:
        raise ValueError("input checksum differs from bound manifest")
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != {"images", "labels", "item_ids"}:
            raise ValueError("unexpected shard arrays")
        images, labels, ids = data["images"], data["labels"], data["item_ids"]
    count = shard["count"]
    if images.dtype != np.uint8 or images.shape != (count, FEATURES):
        raise ValueError("images must be uint8 vectors of 784 pixels")
    if labels.dtype != np.uint8 or labels.shape != (count,) or np.any(labels >= CLASSES):
        raise ValueError("invalid image labels")
    expected = np.arange(shard["start"], shard["stop"], dtype=np.int64)
    if ids.dtype != np.int64 or ids.shape != (count,) or not np.array_equal(ids, expected):
        raise ValueError("shard item ids differ from declared coverage")
    return images, labels, _source(shard)


def _base(kind, manifest_sha256):
    return {"schema_version": "1", "kind": kind, "algorithm": ALGORITHM, "manifest_sha256": manifest_sha256}


def _check_base(value, kind, manifest_sha256):
    if not isinstance(value, dict) or any(value.get(k) != v for k, v in _base(kind, manifest_sha256).items()):
        raise ValueError(f"invalid {kind} metadata or manifest binding")


def _check_source(source, expected):
    if not isinstance(source, dict) or canonical_json(source) != canonical_json(_source(expected)):
        raise ValueError("source checksum, count or item coverage differs from manifest")


def _ordered_parts(parts, manifest, split, kind, manifest_hash):
    expected = {s["id"]: s for s in manifest["shards"] if s["split"] == split}
    accepted = {}
    for part in parts:
        _check_base(part, kind, manifest_hash)
        source = part.get("source")
        if not isinstance(source, dict) or source.get("id") not in expected:
            raise ValueError("unknown input shard")
        shard_id = source["id"]
        if shard_id in accepted:
            raise ValueError("duplicate input shard")
        _check_source(source, expected[shard_id])
        if part.get("accelerator") not in ("cpu", "cuda"):
            raise ValueError("invalid accelerator metadata")
        accepted[shard_id] = part
    if set(accepted) != set(expected):
        raise ValueError(f"missing {split} shard outputs")
    return [accepted[s["id"]] for s in sorted(expected.values(), key=lambda s: s["start"])]


def _statistics(value, count):
    counts = _integer_array(value.get("class_counts"), (CLASSES,), 0, count, "class counts")
    sums = _integer_array(value.get("class_sums"), (CLASSES, FEATURES), 0, 255 * count, "class sums")
    if int(counts.sum()) != count or np.any(sums > counts[:, None] * 255):
        raise ValueError("class statistics disagree with input count")
    return counts, sums


def _torch_cuda():
    try:
        import torch
    except ImportError as error:
        raise ValueError("CUDA requires the project's PyTorch CUDA runtime") from error
    if not torch.cuda.is_available():
        raise ValueError("CUDA was selected but no CUDA device is available")
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    return torch


def train(input_path, manifest_path, accelerator):
    manifest, manifest_hash = load_manifest(manifest_path)
    images, labels, source = _load_shard(input_path, manifest, "train")
    counts = np.bincount(labels, minlength=CLASSES).astype(np.int64)
    if accelerator == "cpu":
        sums = np.stack([images[labels == label].sum(axis=0, dtype=np.int64) for label in range(CLASSES)])
    elif accelerator == "cuda":
        torch = _torch_cuda()
        device_images = torch.as_tensor(images, device="cuda")
        device_labels = torch.as_tensor(labels, device="cuda")
        sums = torch.stack([device_images[device_labels == label].sum(dim=0, dtype=torch.int64)
                            for label in range(CLASSES)]).cpu().numpy()
    else:
        raise ValueError("accelerator must be cpu or cuda")
    return _base("partial", manifest_hash) | {"source": source, "accelerator": accelerator,
                                             "class_counts": counts.tolist(), "class_sums": sums.tolist()}


def _model_id(model):
    keys = ("algorithm", "manifest_sha256", "train_count", "class_counts", "class_sums", "centroids", "sources")
    return hashlib.sha256(canonical_json({key: model[key] for key in keys})).hexdigest()


def merge(parts, manifest_path):
    manifest, manifest_hash = load_manifest(manifest_path)
    parts = _ordered_parts(parts, manifest, "train", "partial", manifest_hash)
    counts = np.zeros(CLASSES, dtype=np.int64)
    sums = np.zeros((CLASSES, FEATURES), dtype=np.int64)
    backends = {"cpu": 0, "cuda": 0}
    for part in parts:
        partial_counts, partial_sums = _statistics(part, part["source"]["count"])
        counts += partial_counts
        sums += partial_sums
        backends[part["accelerator"]] += part["source"]["count"]
    if np.any(counts == 0):
        raise ValueError("training data must contain every class")
    model = _base("model", manifest_hash) | {
        "train_count": manifest["train_count"], "class_counts": counts.tolist(), "class_sums": sums.tolist(),
        "centroids": (sums / counts[:, None]).tolist(), "sources": [p["source"] for p in parts],
        "training_accelerators": backends,
    }
    model["model_id"] = _model_id(model)
    return model


def _load_model(path, manifest, manifest_hash):
    model = read_json(path)
    _check_base(model, "model", manifest_hash)
    if model.get("train_count") != manifest["train_count"]:
        raise ValueError("model training count differs from manifest")
    sources = model.get("sources")
    expected = sorted((s for s in manifest["shards"] if s["split"] == "train"), key=lambda s: s["start"])
    if not isinstance(sources, list) or len(sources) != len(expected):
        raise ValueError("model has incomplete training provenance")
    for source, shard in zip(sources, expected):
        _check_source(source, shard)
    counts, sums = _statistics(model, manifest["train_count"])
    if np.any(counts == 0):
        raise ValueError("model contains an untrained class")
    centroids = np.asarray(model.get("centroids"), dtype=np.float64)
    if (centroids.shape != (CLASSES, FEATURES) or not np.isfinite(centroids).all()
            or not np.array_equal(centroids, sums / counts[:, None])):
        raise ValueError("model centroids disagree with exact training statistics")
    if model.get("model_id") != _model_id(model):
        raise ValueError("model identity does not match its contents")
    return model, sha256(path), centroids


def predict(images, centroids, accelerator):
    """Float64 L2 scores; the common image norm cancels for argmin."""
    predictions = []
    if accelerator == "cpu":
        norms = np.square(centroids).sum(axis=1)
        for start in range(0, len(images), 256):
            batch = images[start:start + 256].astype(np.float64)
            scores = norms[None, :] - 2.0 * (batch @ centroids.T)
            predictions.extend(np.argmin(scores, axis=1).tolist())
    elif accelerator == "cuda":
        torch = _torch_cuda()
        centers = torch.as_tensor(centroids, dtype=torch.float64, device="cuda")
        norms = centers.square().sum(dim=1)
        for start in range(0, len(images), 256):
            batch = torch.as_tensor(images[start:start + 256], dtype=torch.float64, device="cuda")
            scores = norms.unsqueeze(0) - 2.0 * (batch @ centers.T)
            predictions.extend(scores.argmin(dim=1).cpu().tolist())
    else:
        raise ValueError("accelerator must be cpu or cuda")
    return np.asarray(predictions, dtype=np.int64)


def evaluate(input_path, model_path, manifest_path, accelerator):
    manifest, manifest_hash = load_manifest(manifest_path)
    images, labels, source = _load_shard(input_path, manifest, "test")
    model, model_hash, centers = _load_model(model_path, manifest, manifest_hash)
    predictions = predict(images, centers, accelerator)
    return _base("predictions", manifest_hash) | {
        "source": source, "accelerator": accelerator, "model_id": model["model_id"], "model_sha256": model_hash,
        "train_count": model["train_count"], "class_counts": model["class_counts"],
        "labels": labels.tolist(), "predictions": predictions.tolist(), "correct": int((predictions == labels).sum()),
    }


def finalize(parts, model_path, manifest_path):
    manifest, manifest_hash = load_manifest(manifest_path)
    model, model_hash, _ = _load_model(model_path, manifest, manifest_hash)
    parts = _ordered_parts(parts, manifest, "test", "predictions", manifest_hash)
    item_ids, labels, predictions = [], [], []
    backends = {"cpu": 0, "cuda": 0}
    for part in parts:
        if (part.get("model_id") != model["model_id"] or part.get("model_sha256") != model_hash
                or part.get("train_count") != model["train_count"] or part.get("class_counts") != model["class_counts"]):
            raise ValueError("evaluation used a different model")
        count = part["source"]["count"]
        truth = _integer_array(part.get("labels"), (count,), 0, CLASSES - 1, "labels")
        prediction = _integer_array(part.get("predictions"), (count,), 0, CLASSES - 1, "predictions")
        correct = _integer(part.get("correct"), 0, count, "correct count")
        if correct != int((truth == prediction).sum()):
            raise ValueError("incorrect shard accuracy")
        item_ids.extend(part["source"]["item_ids"])
        labels.extend(truth.tolist())
        predictions.extend(prediction.tolist())
        backends[part["accelerator"]] += count
    if item_ids != list(range(manifest["test_count"])):
        raise ValueError("test items must be complete and unique")
    confusion = np.zeros((CLASSES, CLASSES), dtype=np.int64)
    np.add.at(confusion, (np.asarray(labels), np.asarray(predictions)), 1)
    correct = int(np.trace(confusion))
    return _base("result", manifest_hash) | {
        "model_id": model["model_id"], "model_sha256": model_hash, "train_count": model["train_count"],
        "class_counts": model["class_counts"], "training_sources": model["sources"],
        "test_sources": [p["source"] for p in parts], "training_accelerators": model["training_accelerators"],
        "evaluation_accelerators": backends, "test_count": len(item_ids), "item_ids": item_ids,
        "labels": labels, "predictions": predictions, "correct": correct, "accuracy": correct / len(item_ids),
        "confusion_matrix": confusion.tolist(),
    }


def _stage_files(stage_root, stage_id, output):
    paths = sorted((Path(stage_root) / stage_id).glob(f"*/{output}"))
    if not paths:
        raise ValueError(f"no accepted {stage_id}/{output} outputs")
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("train", "merge", "evaluate", "finalize"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    parser.add_argument("--stage-root", type=Path, default=Path("/inputs/stages"))
    parser.add_argument("--accelerator", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.stage in ("train", "evaluate") and args.input is None:
        parser.error("--input is required for map stages")
    outputs = {"train": "partial.json", "merge": "model.json", "evaluate": "predictions.json", "finalize": "result.json"}
    if args.stage in ("evaluate", "finalize") and args.model is None:
        models = _stage_files(args.stage_root, "model", "model.json")
        if len(models) != 1:
            raise ValueError("exactly one accepted global model is required")
        args.model = models[0]
    if args.stage == "train":
        value = train(args.input, args.manifest, args.accelerator)
    elif args.stage == "merge":
        value = merge([read_json(p) for p in _stage_files(args.stage_root, "fit", "partial.json")], args.manifest)
    elif args.stage == "evaluate":
        value = evaluate(args.input, args.model, args.manifest, args.accelerator)
    else:
        value = finalize([read_json(p) for p in _stage_files(args.stage_root, "evaluate", "predictions.json")],
                         args.model, args.manifest)
    write_json(args.output or Path(outputs[args.stage]), value)
    print(json.dumps({"stage": args.stage, "kind": value["kind"], "model_id": value.get("model_id"),
                      "accuracy": value.get("accuracy")}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"fashion-mnist: {error}", file=sys.stderr)
        sys.exit(1)
