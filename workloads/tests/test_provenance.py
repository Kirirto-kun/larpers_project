import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


def _dispatch(adapter_id, operation, manifest, **payload):
    from workloads.adapter import dispatch
    response = dispatch({"schema_version": "1", "adapter": {"id": adapter_id, "version": "1"},
                         "operation": operation, "payload": {"manifest": manifest, **payload}})
    assert response["error"] is None, response
    return response["result"]


def test_registered_metadata_ignores_caller_digest_labels(embedding_manifest, tmp_path):
    manifest = copy.deepcopy(embedding_manifest)
    original = _dispatch("embeddings.e5", "validate_input", manifest)
    assert re.fullmatch("[0-9a-f]{64}", original["adapter_digest"])
    manifest.update(manifest_digest="a" * 64, model_digest="b" * 64, adapter_digest="c" * 64)
    manifest["model"]["digest"] = "d" * 64
    supplied_file = tmp_path / "fake.py"
    supplied_file.write_text("raise RuntimeError('must never import this file')")
    result = _dispatch("embeddings.e5", "validate_input", manifest, source_files=[str(supplied_file)])
    assert {key: result[key] for key in ("manifest_digest", "model_digest", "adapter_digest")} == {
        key: original[key] for key in ("manifest_digest", "model_digest", "adapter_digest")}


def test_new_dataset_cannot_replay_old_validated_provenance(embedding_manifest, tmp_path):
    from workloads.embeddings.adapter import create_reference, run
    from workloads.tests.test_embeddings import DeterministicBackend
    reference = tmp_path / "reference.json"
    create_reference(embedding_manifest, reference, backend=DeterministicBackend())
    old = _dispatch("embeddings.e5", "verify", embedding_manifest,
                    outputs=run(embedding_manifest, backend=DeterministicBackend()),
                    policy={"mode": "full_reference", "reference_path": str(reference)})
    assert old["accepted"] and old["benchmark_eligible"]
    path = Path(embedding_manifest["inputs"][0]["path"])
    path.write_text(path.read_text(encoding="utf-8") + '\n{"text":"fresh data"}', encoding="utf-8")
    embedding_manifest.update({key: old[key] for key in ("manifest_digest", "model_digest", "adapter_digest")})
    create_reference(embedding_manifest, reference, backend=DeterministicBackend())
    current = _dispatch("embeddings.e5", "verify", embedding_manifest,
                        outputs=run(embedding_manifest, backend=DeterministicBackend()),
                        policy={"mode": "full_reference", "reference_path": str(reference)})
    assert current["accepted"] and current["benchmark_eligible"]
    assert current["manifest_digest"] != old["manifest_digest"]
    assert current["model_digest"] == old["model_digest"]
    assert current["adapter_digest"] == old["adapter_digest"]


def test_new_model_config_cannot_replay_old_validated_model_digest(embedding_manifest, tmp_path):
    old = _dispatch("embeddings.e5", "validate_input", embedding_manifest)
    model_root = tmp_path / "model-copy"
    shutil.copytree(embedding_manifest["model"]["path"], model_root)
    config_path = model_root / "config.json"
    config = json.loads(config_path.read_text())
    config["layer_norm_eps"] = 1e-5
    config_path.write_text(json.dumps(config))
    embedding_manifest["model"]["path"] = str(model_root)
    embedding_manifest["model"]["files"]["config.json"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    embedding_manifest["model_digest"] = old["model_digest"]
    current = _dispatch("embeddings.e5", "validate_input", embedding_manifest)
    assert current["model_digest"] != old["model_digest"]
    assert current["manifest_digest"] != old["manifest_digest"]
    assert current["adapter_digest"] == old["adapter_digest"]


def test_adapter_code_digest_is_portable_and_changes_with_actual_trusted_source(tmp_path):
    root = tmp_path / "runtime"
    source = Path(__file__).parents[1]
    shutil.copytree(source, root / "workloads",
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", "tests", "uv.lock"))
    paths = list((root / "workloads").rglob("*.py"))
    for path in paths:
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    before = _isolated_generic_provenance(root)
    for path in paths:
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    crlf = _isolated_generic_provenance(root)
    assert crlf["adapter_digest"] == before["adapter_digest"]
    common = root / "workloads" / "common.py"
    common.write_bytes(common.read_bytes() + b"\r\n# trusted runtime revision\r\n")
    revised = _isolated_generic_provenance(root)
    assert revised["adapter_digest"] != before["adapter_digest"]
    assert revised["manifest_digest"] == before["manifest_digest"]


def _isolated_generic_provenance(root):
    code = '''
import json
from workloads.adapter import dispatch
response = dispatch({"schema_version":"1", "adapter":{"id":"generic.project","version":"1"},
                     "operation":"validate_input", "payload":{"manifest":{"schema_version":"1", "kind":"generic", "command":["python","train.py"]}}})
assert response["error"] is None, response
print(json.dumps(response["result"]))
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=root,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_training_provenance_is_actual_recipe_and_binding(training_manifest, tmp_path):
    from workloads.training.adapter import train
    training_manifest["trainer"]["epochs"] = 1
    training_manifest.update(manifest_digest="a" * 64, model_digest="b" * 64, adapter_digest="c" * 64)
    validated = _dispatch("training.image-classifier", "validate_input", training_manifest)
    assert validated["model_kind"] == "registered_training_recipe"
    assert validated["model_digest"] == validated["binding"]["trainer_digest"]
    output = train(training_manifest, tmp_path / "outputs", tmp_path / "checkpoints")
    verified = _dispatch("training.image-classifier", "verify", training_manifest, outputs=output)
    assert verified["accepted"] and verified["benchmark_eligible"]
    for key in ("manifest_digest", "model_digest", "adapter_digest"):
        assert verified[key] == validated[key]
    changed = copy.deepcopy(training_manifest)
    changed["trainer"]["learning_rate"] = 0.01
    replay = _dispatch("training.image-classifier", "verify", changed, outputs=output)
    assert not replay["accepted"]
    assert replay["manifest_digest"] != validated["manifest_digest"]
    assert replay["model_digest"] != validated["model_digest"]


def test_generic_provenance_has_no_model_and_remains_unverified():
    manifest = {"schema_version": "1", "kind": "generic", "command": ["python", "train.py"]}
    validated = _dispatch("generic.project", "validate_input", manifest)
    verdict = _dispatch("generic.project", "verify", manifest, outputs={"files": []})
    assert verdict["manifest_digest"] == validated["manifest_digest"]
    assert verdict["adapter_digest"] == validated["adapter_digest"]
    assert verdict["model_digest"] == validated["model_digest"] == ""
    assert verdict["model_kind"] == "none"
    assert not verdict["benchmark_eligible"] and verdict["quality"] == "unverified"
