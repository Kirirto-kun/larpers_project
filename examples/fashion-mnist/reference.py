"""Independent standalone reference: raw IDX means and direct squared distances.

This module deliberately imports neither the distributed worker nor its shard
preparation code. It reads the original official splits, not the prepared shards.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import struct
import time
from pathlib import Path

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"

import numpy as np


def _raw_split(raw_dir, prefix, expected_count):
    with gzip.open(raw_dir / f"{prefix}-images-idx3-ubyte.gz", "rb") as stream:
        image_header, image_data = stream.read(16), stream.read()
    with gzip.open(raw_dir / f"{prefix}-labels-idx1-ubyte.gz", "rb") as stream:
        label_header, label_data = stream.read(8), stream.read()
    if (len(image_header) != 16 or len(label_header) != 8
            or struct.unpack(">IIII", image_header) != (2051, expected_count, 28, 28)
            or struct.unpack(">II", label_header) != (2049, expected_count)
            or len(image_data) != expected_count * 784 or len(label_data) != expected_count):
        raise ValueError("invalid original Fashion-MNIST split")
    images = np.frombuffer(image_data, dtype=np.uint8).reshape(expected_count, 784)
    labels = np.frombuffer(label_data, dtype=np.uint8)
    if np.any(labels > 9):
        raise ValueError("invalid original class label")
    return images, labels


def compute_reference(raw_dir, train_count=60000, test_count=10000):
    begin = time.perf_counter()
    raw_dir = Path(raw_dir)
    train_images, train_labels = _raw_split(raw_dir, "train", train_count)
    test_images, test_labels = _raw_split(raw_dir, "t10k", test_count)
    loaded = time.perf_counter()
    counts = [int(np.count_nonzero(train_labels == label)) for label in range(10)]
    if 0 in counts:
        raise ValueError("reference requires every training class")
    centers = np.stack([train_images[train_labels == label].mean(axis=0, dtype=np.float64) for label in range(10)])
    sums = np.stack([train_images[train_labels == label].sum(axis=0, dtype=np.int64) for label in range(10)])
    trained = time.perf_counter()
    predictions = []
    for start in range(0, test_count, 128):
        batch = test_images[start:start + 128].astype(np.float64)
        distances = np.stack([np.square(batch - center).sum(axis=1) for center in centers], axis=1)
        predictions.extend(distances.argmin(axis=1).tolist())
    evaluated = time.perf_counter()
    confusion = np.zeros((10, 10), dtype=np.int64)
    np.add.at(confusion, (test_labels, np.asarray(predictions)), 1)
    correct = int(confusion.trace())
    prediction_bytes = np.asarray(predictions, dtype=np.uint8).tobytes()
    return {"schema_version": "1", "kind": "independent-reference", "train_count": train_count,
            "test_count": test_count, "class_counts": counts, "class_sums": sums.tolist(), "centroids": centers.tolist(),
            "item_ids": list(range(test_count)), "labels": test_labels.tolist(), "predictions": predictions,
            "predictions_sha256": hashlib.sha256(prediction_bytes).hexdigest(), "correct": correct,
            "accuracy": correct / test_count, "confusion_matrix": confusion.tolist(),
            "timing_seconds": {"load": loaded - begin, "train": trained - loaded,
                               "evaluate": evaluated - trained, "total": evaluated - begin}}


def check_result(expected, actual, model=None):
    for key in ("train_count", "test_count", "class_counts", "item_ids", "labels", "predictions", "correct", "accuracy", "confusion_matrix"):
        if expected.get(key) != actual.get(key):
            raise ValueError(f"distributed result differs from independent reference: {key}")
    if model is not None:
        for key in ("train_count", "class_counts", "class_sums"):
            if expected.get(key) != model.get(key):
                raise ValueError(f"global model differs from independent reference: {key}")
        if not np.array_equal(np.asarray(expected["centroids"]), np.asarray(model.get("centroids"))):
            raise ValueError("global model centroids differ from independent reference")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path(__file__).parent / "dataset" / "raw")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", type=Path)
    parser.add_argument("--model", type=Path)
    args = parser.parse_args()
    result = compute_reference(args.raw_dir)
    if args.check:
        check_result(result, json.loads(args.check.read_text(encoding="utf-8")),
                     json.loads(args.model.read_text(encoding="utf-8")) if args.model else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"accuracy": result["accuracy"], "correct": result["correct"],
                      "predictions_sha256": result["predictions_sha256"], "timing_seconds": result["timing_seconds"],
                      "matches_checked_result": bool(args.check)}))


if __name__ == "__main__":
    main()
