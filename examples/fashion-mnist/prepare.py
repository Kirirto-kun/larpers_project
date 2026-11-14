"""Download the official checked Fashion-MNIST release and make immutable shards."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import struct
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

REVISION = "b2617bb6d3ffa2e429640350f613e3291e10b141"
REPOSITORY = "https://github.com/zalandoresearch/fashion-mnist"
BASE_URL = f"https://raw.githubusercontent.com/zalandoresearch/fashion-mnist/{REVISION}/data/fashion/"
# Compressed-file MD5 values published by the official dataset maintainer.
ARCHIVES = {
    "train-images-idx3-ubyte.gz": "8d4fb7e6c68d591d4c3dfef9ec88bf0d",
    "train-labels-idx1-ubyte.gz": "25c81989df183df01b3e8a0aad5dffbe",
    "t10k-images-idx3-ubyte.gz": "bef4ecab320f06d8554ea6380940ec79",
    "t10k-labels-idx1-ubyte.gz": "bb300cfdad3c16e7a12a480ee83cd310",
}
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


def verify_archive(path, expected_md5):
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("official archive is too large")
    # MD5 is only the upstream identity check; SHA256 binds our generated inputs.
    if hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest() != expected_md5:
        raise ValueError(f"official checksum mismatch: {path.name}")


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        if urlparse(new_url).scheme != "https":
            raise ValueError("dataset download redirected away from HTTPS")
        return super().redirect_request(request, file, code, message, headers, new_url)


def download_archive(url, path, expected_md5):
    if urlparse(url).scheme != "https":
        raise ValueError("dataset downloads require HTTPS")
    path = Path(path)
    if path.exists():
        verify_archive(path, expected_md5)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    try:
        opener = urllib.request.build_opener(_HttpsRedirects())
        request = urllib.request.Request(url, headers={"User-Agent": "campus-fashion-mnist-example/1"})
        with opener.open(request, timeout=60) as response, temporary.open("wb") as output:
            if urlparse(response.url).scheme != "https":
                raise ValueError("dataset download requires HTTPS")
            size = 0
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_ARCHIVE_BYTES:
                    raise ValueError("official archive is too large")
                output.write(block)
        verify_archive(temporary, expected_md5)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_idx(image_bytes, label_bytes, expected_count):
    if len(image_bytes) < 16 or len(label_bytes) < 8:
        raise ValueError("truncated IDX header")
    image_magic, image_count, rows, columns = struct.unpack(">IIII", image_bytes[:16])
    label_magic, label_count = struct.unpack(">II", label_bytes[:8])
    if (image_magic, label_magic, rows, columns) != (2051, 2049, 28, 28):
        raise ValueError("unsupported Fashion-MNIST IDX header")
    if image_count != expected_count or label_count != expected_count:
        raise ValueError("IDX count differs from official split size")
    if len(image_bytes) != 16 + expected_count * 784 or len(label_bytes) != 8 + expected_count:
        raise ValueError("truncated or trailing IDX data")
    images = np.frombuffer(image_bytes, dtype=np.uint8, offset=16).reshape(expected_count, 784).copy()
    labels = np.frombuffer(label_bytes, dtype=np.uint8, offset=8).copy()
    if np.any(labels >= 10):
        raise ValueError("IDX contains an invalid class label")
    return images, labels


def partition_dataset(images, labels, split, output_dir, shard_size):
    if type(shard_size) is not int or shard_size < 1 or split not in ("train", "test"):
        raise ValueError("invalid split or shard size")
    if (images.dtype != np.uint8 or images.ndim != 2 or images.shape[1:] != (784,) or len(images) == 0
            or labels.dtype != np.uint8 or labels.shape != (len(images),) or np.any(labels >= 10)):
        raise ValueError("expected nonempty uint8 Fashion-MNIST images and labels")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shards = []
    for number, start in enumerate(range(0, len(images), shard_size)):
        stop = min(start + shard_size, len(images))
        shard_id = f"{split}-{number:05d}"
        path = output_dir / f"{shard_id}.npz"
        np.savez_compressed(path, images=images[start:stop], labels=labels[start:stop],
                            item_ids=np.arange(start, stop, dtype=np.int64))
        shards.append({"id": shard_id, "split": split, "path": f"dataset/{path.name}", "start": start,
                       "stop": stop, "count": stop - start, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return shards


def prepare_project(project_dir, train_shard_size=5000, test_shard_size=1000):
    project_dir = Path(project_dir)
    dataset = project_dir / "dataset"
    raw = dataset / "raw"
    expected_names = set()
    for split, count, size in (("train", 60000, train_shard_size), ("test", 10000, test_shard_size)):
        if type(size) is not int or size < 1:
            raise ValueError("shard sizes must be positive integers")
        shard_count = (count + size - 1) // size
        if shard_count > 512:
            raise ValueError("the project permits at most 512 data files; increase the shard size")
        expected_names.update(f"{split}-{number:05d}.npz" for number in range(shard_count))
    if len(expected_names) > 512:
        raise ValueError("the project permits at most 512 data files; increase the shard size")
    stale = [p.name for p in dataset.glob("*.npz") if p.name not in expected_names]
    if stale:
        raise ValueError(f"dataset contains stale shards from another partition; move these files before preparing: {stale}")
    sources = []
    for name, md5 in ARCHIVES.items():
        path = raw / name
        url = BASE_URL + name
        download_archive(url, path, md5)
        sources.append({"name": name, "url": url, "md5": md5,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    all_shards = []
    for split, prefix, count, size in (("train", "train", 60000, train_shard_size), ("test", "t10k", 10000, test_shard_size)):
        images, labels = read_idx(gzip.decompress((raw / f"{prefix}-images-idx3-ubyte.gz").read_bytes()),
                                 gzip.decompress((raw / f"{prefix}-labels-idx1-ubyte.gz").read_bytes()), count)
        all_shards.extend(partition_dataset(images, labels, split, dataset, size))
    manifest = {"schema_version": "1", "dataset": "fashion-mnist", "features": 784, "classes": 10,
                "train_count": 60000, "test_count": 10000, "source": {"repository": REPOSITORY,
                "revision": REVISION, "files": sources}, "shards": all_shards}
    manifest_path = project_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"train_count": 60000, "test_count": 10000, "shards": len(all_shards),
                      "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).parent)
    parser.add_argument("--train-shard-size", type=int, default=5000)
    parser.add_argument("--test-shard-size", type=int, default=1000)
    args = parser.parse_args()
    prepare_project(args.project_dir, args.train_shard_size, args.test_shard_size)


if __name__ == "__main__":
    main()
