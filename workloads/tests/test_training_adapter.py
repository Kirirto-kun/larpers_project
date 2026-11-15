import copy
import json
from pathlib import Path

import pytest


def test_real_training_independent_evaluator_rejects_fabricated_metrics(training_manifest, tmp_path):
    from workloads.training.adapter import train, evaluate, verify
    output = train(training_manifest, tmp_path / "outputs", tmp_path / "checkpoints")
    assert Path(output["weights_path"]).suffix == ".safetensors"
    assert output["step"] == 12
    metrics = evaluate(training_manifest, output["weights_path"])
    assert metrics["num_examples"] == 4
    assert metrics["accuracy"] >= .75
    assert verify(training_manifest, output, {})["accepted"]
    forged = copy.deepcopy(output)
    forged["metrics"]["loss"] += 1
    verdict = verify(training_manifest, forged, {})
    assert not verdict["accepted"] and "metrics" in " ".join(verdict["errors"])


def test_resume_restores_optimizer_scheduler_rng_and_sampler_exactly(training_manifest, tmp_path):
    from campus.checkpoint import load_checkpoint
    from workloads.training.adapter import train
    uninterrupted = train(training_manifest, tmp_path / "full", tmp_path / "cp-full")
    partial = train(training_manifest, tmp_path / "part", tmp_path / "cp-part", stop_after_steps=3)
    assert partial["status"] == "interrupted" and partial["step"] == 3
    files = load_checkpoint(partial["checkpoint_path"], partial["binding"])
    assert set(files) == {"state.json", "tensors.safetensors"}
    resumed = train(training_manifest, tmp_path / "resumed", tmp_path / "cp-resumed",
                    resume_from=partial["checkpoint_path"])
    assert resumed["step"] == uninterrupted["step"] == 12
    assert resumed["weights_digest"] == uninterrupted["weights_digest"]
    assert resumed["metrics"] == uninterrupted["metrics"]


def test_resume_rejects_environment_or_dataset_change(training_manifest, tmp_path, monkeypatch):
    from workloads.training.adapter import train
    partial = train(training_manifest, tmp_path / "part", tmp_path / "cp-part", stop_after_steps=1)
    monkeypatch.setenv("CAMPUS_RESUME_FROM", partial["checkpoint_path"])
    changed = copy.deepcopy(training_manifest)
    changed["environment_digest"] = "f" * 64
    with pytest.raises(ValueError, match="compatible"):
        train(changed, tmp_path / "changed", tmp_path / "cp-changed")
    (Path(partial["checkpoint_path"]) / "READY").unlink()
    with pytest.raises(ValueError, match="complete"):
        train(training_manifest, tmp_path / "incomplete", tmp_path / "cp-incomplete")


def test_dataset_must_have_explicit_disjoint_validation(training_manifest):
    from workloads.training.adapter import validate_input
    training_manifest["dataset"]["validation"] = "train.jsonl"
    with pytest.raises(ValueError, match="disjoint"):
        validate_input(training_manifest)


def test_training_cli_uses_real_manifest_and_safe_weights(training_manifest, tmp_path):
    import subprocess
    import sys
    training_manifest["trainer"]["epochs"] = 1
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(training_manifest))
    result = subprocess.run([sys.executable, "-m", "workloads.training.run", "--manifest", str(manifest_path),
                             "--output", str(tmp_path / "out"), "--checkpoints", str(tmp_path / "cp")],
                            capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    assert output["status"] == "completed" and Path(output["weights_path"]).is_file()


def test_training_quality_policy_enforces_actual_validation_floor(training_manifest, tmp_path):
    from workloads.training.adapter import train, verify
    training_manifest["trainer"]["epochs"] = 1
    output = train(training_manifest, tmp_path / "out", tmp_path / "cp")
    verdict = verify(training_manifest, output, {"minimum_accuracy": 0, "maximum_loss": 0})
    assert not verdict["accepted"] and "quality policy" in " ".join(verdict["errors"])
    assert not verify(training_manifest, output, {"minimum_accuracy": -1})["accepted"]


def test_dataset_changed_after_checkpoint_cannot_resume(training_manifest, tmp_path):
    from PIL import Image
    from workloads.training.adapter import train
    partial = train(training_manifest, tmp_path / "out", tmp_path / "cp", stop_after_steps=1)
    root = Path(training_manifest["dataset"]["root"])
    Image.new("RGB", (16, 16), (44, 11, 77)).save(root / "train-0.png")
    with pytest.raises(ValueError, match="compatible"):
        train(training_manifest, tmp_path / "resumed", tmp_path / "new-cp", resume_from=partial["checkpoint_path"])


def test_corrupt_safe_weights_are_rejected_as_validation_report(training_manifest, tmp_path):
    import hashlib
    from workloads.training.adapter import train, verify
    training_manifest["trainer"]["epochs"] = 1
    output = train(training_manifest, tmp_path / "out", tmp_path / "cp")
    Path(output["weights_path"]).write_bytes(b"not safetensors")
    output["weights_digest"] = hashlib.sha256(b"not safetensors").hexdigest()
    report = verify(training_manifest, output, {})
    assert not report["accepted"] and report["quality"] == "rejected"


def test_forging_completed_status_does_not_make_partial_training_eligible(training_manifest, tmp_path):
    from workloads.training.adapter import train, verify
    output = train(training_manifest, tmp_path / "out", tmp_path / "cp", stop_after_steps=1)
    output["status"] = "completed"
    report = verify(training_manifest, output, {})
    assert not report["accepted"] and "step" in " ".join(report["errors"])
