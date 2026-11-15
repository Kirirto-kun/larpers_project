"""Small hand-checked fixtures exercise the actual project stages."""

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]


def load_module(name: str):
    path = PROJECT / f"{name}.py"
    if not path.is_file():
        raise AssertionError(f"{name}.py has not been implemented")
    spec = importlib.util.spec_from_file_location(f"fashion_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pixels(values):
    images = np.zeros((len(values), 784), dtype=np.uint8)
    images[:, 0] = values
    return images


class WorkloadTests(unittest.TestCase):
    def setUp(self):
        self.worker = load_module("worker")
        self.prepare = load_module("prepare")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        train_values = [value for label in range(10) for value in (label * 10, label * 10 + 2)]
        self.train_images = pixels(train_values)
        self.train_labels = np.repeat(np.arange(10, dtype=np.uint8), 2)
        self.test_images = pixels([1, 11, 89, 91])
        self.test_labels = np.array([0, 1, 8, 9], dtype=np.uint8)
        train = self.prepare.partition_dataset(self.train_images, self.train_labels, "train", self.dataset, 7)
        test = self.prepare.partition_dataset(self.test_images, self.test_labels, "test", self.dataset, 3)
        self.manifest = {
            "schema_version": "1", "dataset": "fashion-mnist", "features": 784,
            "classes": 10, "train_count": 20, "test_count": 4,
            "shards": train + test,
        }
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def train_partials(self):
        return [self.worker.train(self.root / shard["path"], self.manifest_path, "cpu")
                for shard in self.manifest["shards"] if shard["split"] == "train"]

    def model(self):
        return self.worker.merge(self.train_partials(), self.manifest_path)

    def model_file(self):
        path = self.root / "model.json"
        self.worker.write_json(path, self.model())
        return path

    def prediction_parts(self, model_path):
        return [self.worker.evaluate(self.root / shard["path"], model_path, self.manifest_path, "cpu")
                for shard in self.manifest["shards"] if shard["split"] == "test"]

    def test_partition_preserves_every_item_once_and_is_reproducible(self):
        """Dropping the remainder or renumbering a shard would fail this test."""
        train = [shard for shard in self.manifest["shards"] if shard["split"] == "train"]
        self.assertEqual([shard["count"] for shard in train], [7, 7, 6])
        ids, images, labels = [], [], []
        for shard in train:
            path = self.root / shard["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), shard["sha256"])
            with np.load(path, allow_pickle=False) as data:
                ids.extend(data["item_ids"].tolist())
                images.extend(data["images"].tolist())
                labels.extend(data["labels"].tolist())
        self.assertEqual(ids, list(range(20)))
        np.testing.assert_array_equal(images, self.train_images)
        np.testing.assert_array_equal(labels, self.train_labels)
        again = self.prepare.partition_dataset(self.train_images, self.train_labels, "train", self.dataset, 7)
        self.assertEqual(again, train)

    def test_partition_rejects_empty_bad_shape_labels_and_shard_size(self):
        for images, labels, size in [
            (np.empty((0, 784), dtype=np.uint8), np.empty(0, dtype=np.uint8), 3),
            (np.zeros((2, 783), dtype=np.uint8), np.array([0, 1], dtype=np.uint8), 3),
            (pixels([1, 2]), np.array([0, 10], dtype=np.uint8), 3),
            (pixels([1, 2]), np.array([0, 1], dtype=np.uint8), 0),
            (pixels([1, 2]).astype(np.float64), np.array([0, 1], dtype=np.uint8), 3),
        ]:
            with self.subTest(shape=images.shape, size=size), self.assertRaises(ValueError):
                self.prepare.partition_dataset(images, labels, "train", self.dataset, size)

    def test_idx_parser_enforces_magic_counts_and_complete_payload(self):
        image_bytes = struct.pack(">IIII", 2051, 2, 28, 28) + pixels([10, 20]).tobytes()
        label_bytes = struct.pack(">II", 2049, 2) + bytes([0, 1])
        images, labels = self.prepare.read_idx(image_bytes, label_bytes, 2)
        self.assertEqual(images[:, 0].tolist(), [10, 20])
        self.assertEqual(labels.tolist(), [0, 1])
        for bad_images, bad_labels, count in [
            (image_bytes[:-1], label_bytes, 2), (image_bytes, label_bytes + b"x", 2),
            (b"\0\0\0\0" + image_bytes[4:], label_bytes, 2), (image_bytes, label_bytes, 3),
        ]:
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.prepare.read_idx(bad_images, bad_labels, count)

    def test_checksum_and_https_rejection_happen_before_using_download(self):
        archive = self.root / "source.gz"
        archive.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.prepare.verify_archive(archive, "0" * 32)
        with self.assertRaises(ValueError):
            self.prepare.download_archive("http://example.invalid/data.gz", archive, "0" * 32)

    def test_invalid_preparation_size_fails_before_download_or_new_files(self):
        destination = self.root / "new-project"
        # Network is the external dependency. Invalid arguments must be rejected
        # before crossing that boundary or creating local dataset files.
        with patch.object(self.prepare, "download_archive", side_effect=AssertionError("unexpected download")):
            for size in (0, -1, True, 1):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    self.prepare.prepare_project(destination, size, 1000)
        self.assertFalse(destination.exists())

    def test_stale_preparation_shards_are_rejected_before_overwriting_data(self):
        stale = self.dataset / "train-99999.npz"
        stale.write_bytes(b"old partition")
        old_bytes = (self.dataset / "train-00000.npz").read_bytes()
        with (patch.object(self.prepare, "download_archive", side_effect=AssertionError("unexpected download")),
              self.assertRaises(ValueError)):
            self.prepare.prepare_project(self.root)
        self.assertEqual((self.dataset / "train-00000.npz").read_bytes(), old_bytes)
        self.assertEqual(stale.read_bytes(), b"old partition")

    def test_train_and_merge_are_exact_integer_sufficient_statistics(self):
        parts = self.train_partials()
        self.assertEqual(parts[0]["class_counts"], [2, 2, 2, 1, 0, 0, 0, 0, 0, 0])
        self.assertEqual([row[0] for row in parts[0]["class_sums"]], [2, 22, 42, 30, 0, 0, 0, 0, 0, 0])
        self.assertEqual(parts[0]["source"]["item_ids"], list(range(7)))
        model = self.worker.merge(list(reversed(parts)), self.manifest_path)
        self.assertEqual(model["class_counts"], [2] * 10)
        self.assertEqual([row[0] for row in model["class_sums"]], [2, 22, 42, 62, 82, 102, 122, 142, 162, 182])
        self.assertEqual([row[0] for row in model["centroids"]], [1., 11., 21., 31., 41., 51., 61., 71., 81., 91.])
        self.assertEqual(model["train_count"], 20)
        self.assertEqual(model["model_id"], self.model()["model_id"])

    def test_merge_rejects_duplicate_missing_or_corrupt_partial(self):
        parts = self.train_partials()
        corrupt = copy.deepcopy(parts)
        corrupt[0]["class_sums"][0][0] = -1
        wrong_ids = copy.deepcopy(parts)
        wrong_ids[0]["source"]["item_ids"][0] = 1
        wrong_hash = copy.deepcopy(parts)
        wrong_hash[0]["source"]["sha256"] = "0" * 64
        wrong_counts = copy.deepcopy(parts)
        wrong_counts[0]["class_counts"][0] = 0
        wrong_manifest = copy.deepcopy(parts)
        wrong_manifest[0]["manifest_sha256"] = "0" * 64
        for invalid in [[], parts[:-1], parts + parts[:1], corrupt, wrong_ids, wrong_hash, wrong_counts, wrong_manifest]:
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                self.worker.merge(invalid, self.manifest_path)

    def test_boolean_item_ids_and_statistics_are_not_accepted_as_integers(self):
        parts = self.train_partials()
        bad_source = copy.deepcopy(parts)
        bad_source[0]["source"]["item_ids"][0] = False
        bad_sum = copy.deepcopy(parts)
        bad_sum[0]["class_sums"][0][1] = False
        for invalid in (bad_source, bad_sum):
            with self.subTest(source=invalid[0]["source"]["id"]), self.assertRaises(ValueError):
                self.worker.merge(invalid, self.manifest_path)

    def test_boolean_prediction_is_not_accepted_as_class_zero(self):
        model_path = self.model_file()
        parts = self.prediction_parts(model_path)
        parts[0]["predictions"][0] = False
        with self.assertRaises(ValueError):
            self.worker.finalize(parts, model_path, self.manifest_path)

    def test_train_rejects_changed_input_bytes(self):
        shard = self.root / self.manifest["shards"][0]["path"]
        with shard.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(ValueError):
            self.worker.train(shard, self.manifest_path, "cpu")

    def test_manifest_rejects_gaps_overlaps_and_unsafe_paths(self):
        for key, value in [("start", 1), ("stop", 8), ("path", "../outside.npz"), ("sha256", "no")]:
            invalid = copy.deepcopy(self.manifest)
            invalid["shards"][0][key] = value
            self.manifest_path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.worker.load_manifest(self.manifest_path)

    def test_predictions_use_global_model_and_aggregate_unique_coverage(self):
        model_path = self.model_file()
        parts = self.prediction_parts(model_path)
        self.assertEqual(parts[0]["predictions"], [0, 1, 9])
        self.assertEqual(parts[0]["model_sha256"], hashlib.sha256(model_path.read_bytes()).hexdigest())
        result = self.worker.finalize(list(reversed(parts)), model_path, self.manifest_path)
        self.assertEqual(result["item_ids"], [0, 1, 2, 3])
        self.assertEqual(result["predictions"], [0, 1, 9, 9])
        self.assertEqual(result["labels"], [0, 1, 8, 9])
        self.assertEqual(result["correct"], 3)
        self.assertEqual(result["accuracy"], 0.75)
        self.assertEqual(result["confusion_matrix"][8][9], 1)

    def test_aggregate_rejects_missing_duplicate_wrong_model_and_invalid_labels(self):
        model_path = self.model_file()
        parts = self.prediction_parts(model_path)
        bad_model = copy.deepcopy(parts)
        bad_model[0]["model_sha256"] = "0" * 64
        bad_label = copy.deepcopy(parts)
        bad_label[0]["predictions"][0] = 10
        bad_count = copy.deepcopy(parts)
        bad_count[0]["correct"] = 0
        bad_ids = copy.deepcopy(parts)
        bad_ids[0]["source"]["item_ids"][1] = 0
        for invalid in [[], parts[:-1], parts + parts[:1], bad_model, bad_label, bad_count, bad_ids]:
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                self.worker.finalize(invalid, model_path, self.manifest_path)

    def test_invalid_model_centroids_are_rejected(self):
        model_path = self.model_file()
        model = json.loads(model_path.read_text())
        model["centroids"][0][0] = float("nan")
        model_path.write_text(json.dumps(model), encoding="utf-8")
        shard = next(shard for shard in self.manifest["shards"] if shard["split"] == "test")
        with self.assertRaises(ValueError):
            self.worker.evaluate(self.root / shard["path"], model_path, self.manifest_path, "cpu")

    def test_nearest_centroid_ties_choose_the_lower_class(self):
        centroids = np.zeros((10, 784), dtype=np.float64)
        centroids[:, 0] = np.arange(10) * 10
        self.assertEqual(self.worker.predict(pixels([5, 15, 90]), centroids, "cpu").tolist(), [0, 1, 9])

    def test_worker_cli_uses_stage_mount_layout_and_emits_final_result(self):
        stage_root = self.root / "stages"
        commands = []
        for index, shard in enumerate(self.manifest["shards"]):
            if shard["split"] == "train":
                output = stage_root / "fit" / f"task-{index}" / "partial.json"
                commands.append(["train", "--input", str(self.root / shard["path"]), "--output", str(output)])
        model_path = stage_root / "model" / "task-model" / "model.json"
        commands.append(["merge", "--output", str(model_path)])
        for index, shard in enumerate(self.manifest["shards"]):
            if shard["split"] == "test":
                output = stage_root / "evaluate" / f"task-{index}" / "predictions.json"
                commands.append(["evaluate", "--input", str(self.root / shard["path"]), "--output", str(output)])
        result_path = self.root / "result.json"
        commands.append(["finalize", "--output", str(result_path)])
        for command in commands:
            run = subprocess.run([sys.executable, str(PROJECT / "worker.py"), *command,
                                  "--manifest", str(self.manifest_path), "--stage-root", str(stage_root),
                                  "--accelerator", "cpu"], capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(result_path.read_text())["accuracy"], 0.75)

    def test_reference_independently_reads_idx_and_agrees_with_global_model(self):
        reference = load_module("reference")
        raw_dir = self.root / "raw"
        raw_dir.mkdir()
        for prefix, images, labels in [("train", self.train_images, self.train_labels),
                                        ("t10k", self.test_images, self.test_labels)]:
            (raw_dir / f"{prefix}-images-idx3-ubyte.gz").write_bytes(
                gzip.compress(struct.pack(">IIII", 2051, len(images), 28, 28) + images.tobytes()))
            (raw_dir / f"{prefix}-labels-idx1-ubyte.gz").write_bytes(
                gzip.compress(struct.pack(">II", 2049, len(labels)) + labels.tobytes()))
        expected = reference.compute_reference(raw_dir, 20, 4)
        model_path = self.model_file()
        actual = self.worker.finalize(self.prediction_parts(model_path), model_path, self.manifest_path)
        self.assertEqual(expected["predictions"], [0, 1, 9, 9])
        self.assertEqual(expected["class_counts"], [2] * 10)
        self.assertEqual(expected["accuracy"], 0.75)
        reference.check_result(expected, actual)
        actual["predictions"][0] = 1
        with self.assertRaises(ValueError):
            reference.check_result(expected, actual)

    def test_standalone_runs_the_same_stage_processes_and_refuses_stale_outputs(self):
        output = self.root / "standalone"
        command = [sys.executable, str(PROJECT / "standalone.py"), "--manifest", str(self.manifest_path),
                   "--input-root", str(self.root), "--output-dir", str(output), "--accelerator", "cpu"]
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads((output / "result.json").read_text())
        self.assertEqual(result["accuracy"], 0.75)
        report = json.loads((output / "run.json").read_text())
        self.assertEqual(report["task_count"], 7)
        self.assertEqual([task["stage"] for task in report["tasks"]],
                         ["fit", "fit", "fit", "model", "evaluate", "evaluate", "aggregate"])
        second = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(json.loads((output / "result.json").read_text())["accuracy"], 0.75)

    def test_cuda_statistics_and_predictions_match_cpu_when_cuda_is_available(self):
        if importlib.util.find_spec("torch") is None:
            self.skipTest("PyTorch CUDA runtime is not installed")
        import torch
        if not torch.cuda.is_available():
            self.skipTest("CUDA device is not available")
        for shard in [s for s in self.manifest["shards"] if s["split"] == "train"]:
            cpu = self.worker.train(self.root / shard["path"], self.manifest_path, "cpu")
            cuda = self.worker.train(self.root / shard["path"], self.manifest_path, "cuda")
            self.assertEqual(cuda["class_counts"], cpu["class_counts"])
            self.assertEqual(cuda["class_sums"], cpu["class_sums"])
        model_path = self.model_file()
        for shard in [s for s in self.manifest["shards"] if s["split"] == "test"]:
            cpu = self.worker.evaluate(self.root / shard["path"], model_path, self.manifest_path, "cpu")
            cuda = self.worker.evaluate(self.root / shard["path"], model_path, self.manifest_path, "cuda")
            self.assertEqual(cuda["predictions"], cpu["predictions"])


if __name__ == "__main__":
    unittest.main()
