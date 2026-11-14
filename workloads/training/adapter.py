from __future__ import annotations

import math
import os
from pathlib import Path
import random
import time

import numpy as np
from PIL import Image, UnidentifiedImageError

from workloads.common import (atomic_bytes, confined_file, digest, file_digest,
                              integer, manifest_header, no_links, parse_json, regular_file,
                              require_digest, write_json)
from workloads.provenance import adapter_digest
from .runtime import configure_cublas

ADAPTER = {"id": "training.image-classifier", "version": "1"}
RECIPE = "tiny-conv-v1"


def _prepare(manifest):
    manifest_header(manifest, "training")
    runtime = {"cublas_workspace_config": configure_cublas()}
    environment = require_digest(manifest.get("environment_digest"))
    dataset = manifest.get("dataset", {})
    classes = dataset.get("classes")
    if (not isinstance(classes, list) or not 2 <= len(classes) <= 100 or len(set(classes)) != len(classes)
            or any(not isinstance(name, str) or not name or len(name) > 100 for name in classes)):
        raise ValueError("dataset classes must contain 2..100 unique names")
    root = no_links(dataset["root"]).resolve(strict=True)
    trainer = {"seed": 42, "epochs": 5, "batch_size": 8, "learning_rate": 0.01,
               "image_size": 32, "checkpoint_every": 10, "scheduler_step": 4, "scheduler_gamma": 0.8}
    supplied = manifest.get("trainer", {})
    if not isinstance(supplied, dict) or set(supplied) - set(trainer):
        raise ValueError("unknown trainer option; only registered recipe parameters are accepted")
    trainer.update(supplied)
    integer(trainer["seed"], "seed", minimum=0, maximum=2**32 - 1)
    integer(trainer["epochs"], "epochs", maximum=1000)
    integer(trainer["batch_size"], "batch_size", maximum=1024)
    integer(trainer["image_size"], "image_size", minimum=16, maximum=128)
    integer(trainer["checkpoint_every"], "checkpoint_every", maximum=100000)
    integer(trainer["scheduler_step"], "scheduler_step", maximum=100000)
    for key, low, high in [("learning_rate", 1e-7, 1), ("scheduler_gamma", 1e-4, 1)]:
        value = trainer[key]
        if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"invalid {key}")
    records = {}
    for split_name in ("train", "validation"):
        if not dataset.get(split_name):
            raise ValueError("explicit disjoint train and validation manifests are required")
        file = confined_file(root, dataset[split_name], 8 << 20)
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("dataset manifest must be UTF-8") from exc
        rows, seen = [], set()
        for line in lines:
            if not line.strip():
                continue
            record = parse_json(line)
            if not isinstance(record, dict) or record.get("label") not in classes:
                raise ValueError("image label does not belong to fixed classes")
            image = confined_file(root, record["path"], 16 << 20)
            image_digest = file_digest(image)
            if image_digest in seen:
                raise ValueError("duplicate image contents within dataset split")
            seen.add(image_digest)
            try:
                with Image.open(image) as decoded:
                    if decoded.width * decoded.height > 16_000_000:
                        raise ValueError("image pixel quota exceeded")
                    decoded.verify()
            except (UnidentifiedImageError, OSError) as exc:
                raise ValueError("invalid or corrupt dataset image") from exc
            rows.append({"path": record["path"], "label": record["label"], "sha256": image_digest})
            if len(rows) > 100000:
                raise ValueError("dataset example quota exceeded")
        if not rows:
            raise ValueError("both train and validation need labeled images")
        records[split_name] = rows
    if {row["sha256"] for row in records["train"]} & {row["sha256"] for row in records["validation"]}:
        raise ValueError("train and validation must be disjoint by image contents")
    if {row["label"] for row in records["train"]} != set(classes):
        raise ValueError("training split must contain every class")
    input_digest = digest({"classes": classes, "splits": records})
    trainer_digest = digest({"recipe": RECIPE, "parameters": trainer, "classes": classes, "runtime": runtime})
    code_digest = adapter_digest(ADAPTER["id"], ADAPTER["version"])
    binding = {"code_digest": code_digest, "input_digest": input_digest,
               "environment_digest": environment, "trainer_digest": trainer_digest}
    return {"root": root, "classes": classes, "trainer": trainer, "records": records, "runtime": runtime,
            "binding": binding, "manifest_digest": digest({"adapter": ADAPTER, "binding": binding}),
            "validation_digest": digest(records["validation"])}


def validate_input(manifest):
    prepared = _prepare(manifest)
    return {"valid": True, "adapter": ADAPTER, "manifest_digest": prepared["manifest_digest"],
            "binding": prepared["binding"], "recipe": RECIPE,
            "model_kind": "registered_training_recipe", "model_digest": prepared["binding"]["trainer_digest"],
            "runtime": prepared["runtime"],
            "train_examples": len(prepared["records"]["train"]),
            "validation_examples": len(prepared["records"]["validation"])}


def split(manifest, batch_policy):
    report = validate_input(manifest)
    return [{"schema_version": "1", "adapter": ADAPTER, "task_id": report["manifest_digest"],
             "manifest_digest": report["manifest_digest"], "binding": report["binding"],
             "kind": "single_trainer", "units": report["train_examples"]}]


def _model(num_classes):
    import torch.nn as nn
    return nn.Sequential(nn.Conv2d(3, 8, 3, padding=1), nn.ReLU(), nn.AvgPool2d(2),
                         nn.Conv2d(8, 16, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool2d(1),
                         nn.Flatten(), nn.Dropout(0.1), nn.Linear(16, num_classes))


def _batch(prepared, split_name, indices, device):
    import torch
    images, labels = [], []
    for index in indices:
        record = prepared["records"][split_name][index]
        path = confined_file(prepared["root"], record["path"], 16 << 20)
        if file_digest(path) != record["sha256"]:
            raise ValueError("dataset changed during execution")
        with Image.open(path) as image:
            image = image.convert("RGB").resize((prepared["trainer"]["image_size"],) * 2, Image.Resampling.BILINEAR)
            array = np.asarray(image, dtype=np.float32).copy() / 255
        images.append(torch.from_numpy(array).permute(2, 0, 1))
        labels.append(prepared["classes"].index(record["label"]))
    return torch.stack(images).to(device), torch.tensor(labels, dtype=torch.long, device=device)


def _evaluate(prepared, model, device):
    import torch
    model.eval()
    count, correct, loss = 0, 0, 0.
    size = prepared["trainer"]["batch_size"]
    total = len(prepared["records"]["validation"])
    with torch.inference_mode():
        for offset in range(0, total, size):
            inputs, targets = _batch(prepared, "validation", list(range(offset, min(total, offset + size))), device)
            prediction = model(inputs)
            loss += torch.nn.functional.cross_entropy(prediction, targets, reduction="sum").item()
            correct += (prediction.argmax(1) == targets).sum().item()
            count += len(targets)
    metrics = {"accuracy": correct / count, "loss": loss / count, "num_examples": count,
               "validation_digest": prepared["validation_digest"]}
    if not math.isfinite(metrics["loss"]):
        raise ValueError("model produces nonfinite validation loss")
    return metrics


def evaluate(manifest, weights_path, *, device="cpu"):
    configure_cublas()
    import torch
    from safetensors import SafetensorError
    from safetensors.torch import load_file
    prepared = _prepare(manifest)
    if device not in {"cpu", "cuda"} or (device == "cuda" and not torch.cuda.is_available()):
        raise ValueError("requested evaluator device unavailable")
    torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
    weights = regular_file(weights_path, 16 << 20)
    if weights.suffix != ".safetensors":
        raise ValueError("evaluator only accepts safetensors weights; pickle is forbidden")
    try:
        state = load_file(str(weights), device="cpu")
    except SafetensorError as exc:
        raise ValueError("invalid or corrupt safetensors weights") from exc
    if any(not torch.isfinite(tensor).all() for tensor in state.values()):
        raise ValueError("model weights must be finite")
    model = _model(len(prepared["classes"]))
    model.load_state_dict(state, strict=True)
    model.to(device)
    return _evaluate(prepared, model, device)


def _seed(seed):
    configure_cublas()
    import torch
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))


def train(manifest, output_dir="/outputs", checkpoint_dir="/checkpoints", *, resume_from=None,
          device="cpu", stop_after_steps=None):
    configure_cublas()
    import torch
    from safetensors.torch import save
    from campus.checkpoint import load_checkpoint, save_checkpoint
    from .state import decode_state, encode_state
    started = time.monotonic()
    prepared = _prepare(manifest)
    config = prepared["trainer"]
    if device not in {"cpu", "cuda"} or (device == "cuda" and not torch.cuda.is_available()):
        raise ValueError("requested training device unavailable")
    if stop_after_steps is not None:
        integer(stop_after_steps, "stop_after_steps", maximum=1_000_000)
    output_dir, checkpoint_dir = no_links(output_dir), no_links(checkpoint_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    _seed(config["seed"])
    model = _model(len(prepared["classes"])).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"], weight_decay=0.01)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=config["scheduler_step"], gamma=config["scheduler_gamma"])
    generator = torch.Generator().manual_seed(config["seed"] + 1)
    n = len(prepared["records"]["train"])
    sampler = {"order": torch.randperm(n, generator=generator).tolist(), "position": 0, "epoch": 0}
    step = 0
    resume_from = resume_from or os.environ.get("CAMPUS_RESUME_FROM")
    if resume_from:
        state = decode_state(load_checkpoint(resume_from, prepared["binding"], max_bytes=32 << 20))
        if set(state) != {"model", "optimizer", "scheduler", "step", "sampler", "sampler_rng", "rng"}:
            raise ValueError("checkpoint lacks full model/optimizer/scheduler/step/RNG/sampler state")
        model.load_state_dict(state["model"], strict=True)
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        sampler, step = state["sampler"], state["step"]
        if (sorted(sampler["order"]) != list(range(n)) or not 0 <= sampler["position"] <= n
                or not 0 <= sampler["epoch"] < config["epochs"] or type(step) is not int or step < 0):
            raise ValueError("invalid sampler/step state")
        generator.set_state(state["sampler_rng"])
        random.setstate(state["rng"]["python"])
        np.random.set_state(state["rng"]["numpy"])
        torch.set_rng_state(state["rng"]["torch_cpu"])
        if state["rng"]["torch_cuda"]:
            if not torch.cuda.is_available():
                raise ValueError("CUDA RNG checkpoint requires compatible CUDA runtime")
            torch.cuda.set_rng_state_all(state["rng"]["torch_cuda"])
    total_steps = config["epochs"] * math.ceil(n / config["batch_size"])
    checkpoint_path = None

    def checkpoint():
        state = {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict(),
                 "step": step, "sampler": sampler, "sampler_rng": generator.get_state(),
                 "rng": {"python": random.getstate(), "numpy": np.random.get_state(),
                         "torch_cpu": torch.get_rng_state(),
                         "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []}}
        # Names include a random suffix so a resumed attempt never overwrites a
        # prior immutable snapshot at the same training step.
        import uuid
        return save_checkpoint(checkpoint_dir, f"step-{step:08d}-{uuid.uuid4().hex[:8]}",
                               encode_state(state), prepared["binding"], max_bytes=32 << 20)

    while step < total_steps:
        if stop_after_steps is not None and step >= stop_after_steps:
            break
        if sampler["position"] == n:
            sampler = {"order": torch.randperm(n, generator=generator).tolist(),
                       "position": 0, "epoch": sampler["epoch"] + 1}
        indices = sampler["order"][sampler["position"]:sampler["position"] + config["batch_size"]]
        inputs, targets = _batch(prepared, "train", indices, device)
        model.train()
        optimizer.zero_grad(set_to_none=True)
        loss = torch.nn.functional.cross_entropy(model(inputs), targets)
        if not torch.isfinite(loss):
            raise ValueError("nonfinite training loss")
        loss.backward()
        optimizer.step()
        scheduler.step()
        sampler["position"] += len(indices)
        step += 1
        if step % config["checkpoint_every"] == 0:
            checkpoint_path = checkpoint()
    checkpoint_path = checkpoint()
    state_dict = {key: tensor.detach().cpu().contiguous() for key, tensor in model.state_dict().items()}
    weights_path = atomic_bytes(output_dir / "model.safetensors", save(state_dict))
    metrics = evaluate(manifest, weights_path)
    result = {"schema_version": "1", "adapter": ADAPTER, "manifest_digest": prepared["manifest_digest"],
              "binding": prepared["binding"], "recipe": RECIPE, "step": step,
              "model_kind": "registered_training_recipe", "model_digest": prepared["binding"]["trainer_digest"],
              "runtime": prepared["runtime"],
              "status": "completed" if step == total_steps else "interrupted", "weights_path": str(weights_path),
              "weights_digest": file_digest(weights_path), "metrics": metrics,
              "checkpoint_path": str(checkpoint_path), "seed": config["seed"], "parameters": config,
              "execution_duration_ms": (time.monotonic() - started) * 1000, "device": device,
              "quality": "pending_independent_verification", "benchmark_eligible": False}
    write_json(output_dir / "result.json", result)
    return result


def run(manifest, batch_policy=None, **kwargs):
    return train(manifest, **kwargs)


def verify(inputs, outputs, policy=None):
    start = time.monotonic()
    report = {"accepted": False, "quality": "rejected", "benchmark_eligible": False,
              "coverage": "fixed_validation_split", "errors": []}
    try:
        policy = policy or {}
        if set(policy) - {"minimum_accuracy", "maximum_loss"}:
            raise ValueError("unknown training quality policy option")
        minimum_accuracy = policy.get("minimum_accuracy", 0.)
        maximum_loss = policy.get("maximum_loss")
        if (type(minimum_accuracy) not in (float, int) or not math.isfinite(minimum_accuracy) or not 0 <= minimum_accuracy <= 1
                or (maximum_loss is not None and (type(maximum_loss) not in (float, int) or not math.isfinite(maximum_loss) or maximum_loss < 0))):
            raise ValueError("invalid training quality policy")
        prepared = _prepare(inputs)
        report.update(manifest_digest=prepared["manifest_digest"], binding=prepared["binding"], recipe=RECIPE,
                      model_kind="registered_training_recipe", model_digest=prepared["binding"]["trainer_digest"],
                      runtime=prepared["runtime"])
        if isinstance(outputs, list):
            if len(outputs) != 1:
                raise ValueError("single trainer requires exactly one output")
            outputs = outputs[0]
        if (outputs.get("adapter") != ADAPTER or outputs.get("schema_version") != "1"
                or outputs.get("manifest_digest") != prepared["manifest_digest"] or outputs.get("binding") != prepared["binding"]):
            raise ValueError("training manifest/checkpoint binding mismatch")
        if outputs.get("status") != "completed":
            raise ValueError("interrupted training is not a completed benchmark")
        expected_steps = prepared["trainer"]["epochs"] * math.ceil(len(prepared["records"]["train"]) / prepared["trainer"]["batch_size"])
        if (type(outputs.get("step")) is not int or outputs["step"] != expected_steps
                or outputs.get("parameters") != prepared["trainer"] or outputs.get("seed") != prepared["trainer"]["seed"]
                or outputs.get("recipe") != RECIPE):
            raise ValueError("training step/parameters/seed/recipe do not match the bound plan")
        if file_digest(outputs["weights_path"], 16 << 20) != outputs["weights_digest"]:
            raise ValueError("weights digest mismatch")
        # This constructs a fresh registered model and reads the safe file,
        # independently of the worker's in-memory model and claimed metrics.
        metrics = evaluate(inputs, outputs["weights_path"])
        claimed = outputs["metrics"]
        for key in ("accuracy", "loss"):
            if type(claimed[key]) not in (int, float) or not math.isfinite(claimed[key]) or abs(claimed[key] - metrics[key]) > 1e-6:
                raise ValueError("claimed training metrics do not match independent evaluation")
        if any(claimed.get(key) != metrics[key] for key in ("num_examples", "validation_digest")):
            raise ValueError("claimed metrics use different validation data")
        if metrics["accuracy"] < minimum_accuracy or (maximum_loss is not None and metrics["loss"] > maximum_loss):
            raise ValueError("independent validation metrics fail the fixed quality policy")
        report.update(accepted=True, quality="verified", benchmark_eligible=True,
                      metrics=metrics, checked_items=metrics["num_examples"], manifest_digest=prepared["manifest_digest"],
                      quality_policy_digest=digest({"minimum_accuracy": minimum_accuracy, "maximum_loss": maximum_loss}))
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        report["errors"].append(str(exc))
    report["validation_duration_ms"] = (time.monotonic() - start) * 1000
    return report


def aggregate(accepted_outputs):
    outputs = accepted_outputs["outputs"]
    report = verify(accepted_outputs["manifest"], outputs, accepted_outputs.get("policy"))
    if not report["accepted"]:
        raise ValueError("; ".join(report["errors"]))
    output = outputs[0] if isinstance(outputs, list) else outputs
    return {**output, "quality": "verified", "benchmark_eligible": True, "validation": report}
