"""Run the identical project stage processes serially on one machine.

Useful as a matched computational baseline; it does not include coordinator,
upload, download, or one-container-per-task startup overhead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from worker import load_manifest, write_json


def run(manifest_path, input_root, output_dir, accelerator):
    manifest_path, input_root, output_dir = Path(manifest_path).resolve(), Path(input_root).resolve(), Path(output_dir).resolve()
    manifest, manifest_hash = load_manifest(manifest_path)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output directory must be empty; previous task outputs cannot be reused")
    output_dir.mkdir(parents=True, exist_ok=True)
    stage_root = output_dir / "stages"
    tasks = []
    begin = time.perf_counter()

    def execute(stage, argv, output):
        started = time.perf_counter()
        command = [sys.executable, str(Path(__file__).with_name("worker.py")), *argv, "--manifest", str(manifest_path),
                   "--stage-root", str(stage_root), "--accelerator", accelerator, "--output", str(output)]
        subprocess.run(command, check=True)
        tasks.append({"stage": stage, "elapsed_seconds": time.perf_counter() - started,
                      "output_bytes": output.stat().st_size, "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest()})

    for shard in sorted((s for s in manifest["shards"] if s["split"] == "train"), key=lambda s: s["start"]):
        execute("fit", ["train", "--input", str(input_root / shard["path"])],
                stage_root / "fit" / shard["id"] / "partial.json")
    execute("model", ["merge"], output_dir / "model.json")
    # Preserve the same accepted predecessor mount layout used by pool tasks.
    model_mount = stage_root / "model" / "global"
    model_mount.mkdir(parents=True)
    (model_mount / "model.json").write_bytes((output_dir / "model.json").read_bytes())
    for shard in sorted((s for s in manifest["shards"] if s["split"] == "test"), key=lambda s: s["start"]):
        execute("evaluate", ["evaluate", "--input", str(input_root / shard["path"])],
                stage_root / "evaluate" / shard["id"] / "predictions.json")
    execute("aggregate", ["finalize"], output_dir / "result.json")
    elapsed = time.perf_counter() - begin
    result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    report = {"schema_version": "1", "kind": "standalone-stage-baseline", "manifest_sha256": manifest_hash,
              "accelerator": accelerator, "python": platform.python_version(), "platform": platform.platform(),
              "task_count": len(tasks), "tasks": tasks, "elapsed_seconds": elapsed,
              "train_count": manifest["train_count"], "test_count": manifest["test_count"],
              "items_per_second": (manifest["train_count"] + manifest["test_count"]) / elapsed,
              "model_id": result["model_id"], "correct": result["correct"], "accuracy": result["accuracy"],
              "excludes": ["dataset preparation", "data transfers", "coordinator queueing", "per-task container startup"]}
    write_json(output_dir / "run.json", report)
    print(json.dumps({key: report[key] for key in ("task_count", "elapsed_seconds", "accuracy", "model_id")}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    parser.add_argument("--input-root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--accelerator", choices=("cpu", "cuda"), required=True)
    args = parser.parse_args()
    run(args.manifest, args.input_root, args.output_dir, args.accelerator)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"fashion-mnist standalone: {error}", file=sys.stderr)
        sys.exit(1)
