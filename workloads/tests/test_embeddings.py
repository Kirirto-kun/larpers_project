import copy
import hashlib
import json
import socket

import numpy as np
import pytest


class DeterministicBackend:
    """Test-only encoder to force OOM without depending on real GPU memory."""
    def encode(self, texts):
        if len(texts) > 2:
            raise RuntimeError("CUDA out of memory")
        rows = []
        for text in texts:
            assert text.startswith(("passage: ", "query: "))
            content = text.split(": ", 1)[1]
            seed = int.from_bytes(hashlib.sha256(content.encode()).digest()[:8])
            vector = np.random.default_rng(seed).normal(size=384).astype(np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.asarray(rows)


def test_split_stable_ids_and_oom_retry_preserve_complete_coverage(embedding_manifest):
    from workloads.embeddings.adapter import split, run
    tasks = split(embedding_manifest, {"items_per_task": 2})
    ids = [item_id for task in tasks for item_id in task["item_ids"]]
    assert len(ids) == 5 and len(set(ids)) == 5
    assert ids == [item_id for task in split(copy.deepcopy(embedding_manifest), {"items_per_task": 3})
                   for item_id in task["item_ids"]]
    output = run(embedding_manifest, {"microbatch_size": 4}, backend=DeterministicBackend())
    assert [row["item_id"] for row in output["vectors"]] == ids
    assert output["stats"]["oom_retries"] == 1
    assert len(output["vectors"][0]["vector"]) == 384


def test_full_reference_rejects_stale_manifest_and_wrong_vectors(embedding_manifest, tmp_path):
    from workloads.embeddings.adapter import create_reference, run, verify
    reference_path = tmp_path / "trusted-reference.json"
    reference = create_reference(embedding_manifest, reference_path, backend=DeterministicBackend(), device_id="trusted-cpu")
    output = run(embedding_manifest, backend=DeterministicBackend())
    policy = {"mode": "full_reference", "reference_path": str(reference_path)}
    verdict = verify(embedding_manifest, output, policy)
    assert verdict["accepted"] and verdict["benchmark_eligible"]
    assert verdict["checked_items"] == 5 and verdict["coverage"] == "full_reference"
    assert reference["reference_device_id"] == "trusted-cpu"
    assert reference["reference_duration_ms"] >= 0
    wrong = copy.deepcopy(output)
    wrong["vectors"][0]["vector"] = [1.0] + [0.0] * 383
    assert not verify(embedding_manifest, wrong, policy)["accepted"]
    changed = copy.deepcopy(embedding_manifest)
    changed["preprocessing"]["chunk_chars"] = 5
    assert not verify(changed, output, policy)["accepted"]
    with open(embedding_manifest["inputs"][0]["path"], "a", encoding="utf-8") as f:
        f.write('\n{"text":"fresh data"}')
    assert not verify(embedding_manifest, output, policy)["accepted"]


@pytest.mark.parametrize("damage", ["duplicate", "missing", "nan", "dimension", "norm", "policy"])
def test_validation_rejects_incomplete_nonfinite_and_retargeted_policy(embedding_manifest, tmp_path, damage):
    from workloads.embeddings.adapter import create_reference, run, verify
    reference_path = tmp_path / "reference.json"
    create_reference(embedding_manifest, reference_path, backend=DeterministicBackend())
    output = run(embedding_manifest, backend=DeterministicBackend())
    policy = {"mode": "full_reference", "reference_path": str(reference_path)}
    if damage == "duplicate": output["vectors"].append(copy.deepcopy(output["vectors"][0]))
    if damage == "missing": output["vectors"].pop()
    if damage == "nan": output["vectors"][0]["vector"][0] = float("nan")
    if damage == "dimension": output["vectors"][0]["vector"].pop()
    if damage == "norm": output["vectors"][0]["vector"] = [0.0] * 384
    if damage == "policy": policy["tolerances"] = {"atol": 10, "cosine_min": -1, "norm_atol": 10}
    assert not verify(embedding_manifest, output, policy)["accepted"]


def test_aggregate_requires_coverage_and_search_returns_original_source(embedding_manifest):
    from workloads.embeddings.adapter import aggregate, run, search
    output = run(embedding_manifest, backend=DeterministicBackend())
    index = aggregate({"manifest": embedding_manifest, "outputs": [output]})
    assert index["quality"] == "structural_only" and not index["benchmark_eligible"]
    results = search(embedding_manifest, index, "campus pool", backend=DeterministicBackend())
    assert results[0]["excerpt"] == "campus pool"
    assert results[0]["source"] == "文档" and results[0]["line"] == 2
    output["vectors"].pop()
    with pytest.raises(ValueError, match="coverage|missing"):
        aggregate({"manifest": embedding_manifest, "outputs": [output]})


def test_preprocessing_txt_chunks_and_rejects_corruption(embedding_manifest, tmp_path):
    from workloads.embeddings.adapter import split, validate_input
    source = tmp_path / "lines.txt"
    source.write_text("abcdefghij\n你好世界\n", encoding="utf-8")
    embedding_manifest["inputs"] = [{"path": str(source), "format": "txt", "source": "text"}]
    embedding_manifest["preprocessing"] = {"chunk_chars": 5, "overlap_chars": 1}
    items = [item for task in split(embedding_manifest, {}) for item in task["items"]]
    assert [item["text"] for item in items] == ["abcde", "efghi", "ij", "你好世界"]
    source.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="UTF-8"):
        validate_input(embedding_manifest)


def test_real_local_transformer_is_offline_masked_and_normalized(embedding_manifest, monkeypatch):
    import torch
    from workloads.embeddings.adapter import run
    from workloads.embeddings.backend import mean_pool
    def no_network(*args, **kwargs):
        raise AssertionError("network access is forbidden")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    hidden = torch.tensor([[[1., 3.], [3., 5.], [99., 99.]]])
    pooled = mean_pool(hidden, torch.tensor([[1, 1, 0]]))
    assert torch.equal(pooled, torch.tensor([[2., 4.]]))
    output = run(embedding_manifest, {"microbatch_size": 2})
    rows = np.asarray([row["vector"] for row in output["vectors"]])
    assert rows.shape == (5, 384)
    assert np.isfinite(rows).all()
    assert np.allclose(np.linalg.norm(rows, axis=1), 1, atol=1e-6)


def test_model_locks_reject_tampered_config_and_remote_code(embedding_manifest, tmp_path):
    import shutil
    from workloads.embeddings.adapter import validate_input
    root = tmp_path / "model"
    shutil.copytree(embedding_manifest["model"]["path"], root)
    embedding_manifest["model"]["path"] = str(root)
    config = json.loads((root / "config.json").read_text())
    config["auto_map"] = {"AutoModel": "evil.Model"}
    (root / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="digest|remote code"):
        validate_input(embedding_manifest)


def test_task_validation_checks_assigned_subset_but_aggregate_still_requires_all(embedding_manifest, tmp_path):
    from workloads.embeddings.adapter import aggregate, create_reference, run, split, verify
    reference = tmp_path / "ref.json"
    create_reference(embedding_manifest, reference, backend=DeterministicBackend())
    task = split(embedding_manifest, {"items_per_task": 2})[0]
    output = run(embedding_manifest, backend=DeterministicBackend(), item_ids=task["item_ids"])
    policy = {"mode": "full_reference", "reference_path": str(reference), "item_ids": task["item_ids"]}
    report = verify(embedding_manifest, output, policy)
    assert report["accepted"] and report["checked_items"] == 2
    assert report["validation_scope"] == "assigned_items" and report["manifest_items"] == 5
    with pytest.raises(ValueError, match="coverage|complete"):
        aggregate({"manifest": embedding_manifest, "outputs": [output], "policy": policy})


def test_embedding_cli_produces_searchable_index_and_offline_reference(embedding_manifest, tmp_path):
    import subprocess
    import sys
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(embedding_manifest), encoding="utf-8")
    index = tmp_path / "index.json"
    result = subprocess.run([sys.executable, "-m", "workloads.embeddings.run", "--manifest", str(manifest_path),
                             "--output", str(index), "--microbatch-size", "2"],
                            text=True, encoding="utf-8", capture_output=True, check=True)
    assert json.loads(result.stdout)["units"] == 5
    document = json.loads(index.read_text(encoding="utf-8"))
    assert document["type"] == "e5_search_index"
    document["quality"] = "verified"
    document["benchmark_eligible"] = True
    index.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "workloads.embeddings.run", "--manifest", str(manifest_path),
                             "--search", str(index), "--query", "campus pool", "--top-k", "1"],
                            text=True, encoding="utf-8", capture_output=True, check=True)
    response = json.loads(result.stdout)
    assert len(response["results"]) == 1
    assert response["quality"] == "structural_only" and response["coverage"] == "structural"


def test_prepare_cli_locks_existing_local_model_without_network(embedding_manifest, tmp_path):
    import subprocess
    import sys
    path = tmp_path / "prepared.json"
    subprocess.run([sys.executable, "-m", "workloads.embeddings.prepare", "--model", embedding_manifest["model"]["path"],
                    "--revision", "a" * 40, "--input", embedding_manifest["inputs"][0]["path"],
                    "--output", str(path)], capture_output=True, check=True)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["model"]["files"] == embedding_manifest["model"]["files"]
    assert manifest["model"]["revision"] == "a" * 40


def test_cpu_allocator_oom_halves_without_losing_items(embedding_manifest):
    from workloads.embeddings.adapter import run
    class CPUAllocatorBackend(DeterministicBackend):
        def encode(self, texts):
            if len(texts) > 2:
                raise RuntimeError("DefaultCPUAllocator: not enough memory: you tried to allocate 999 bytes")
            return super().encode(texts)
    output = run(embedding_manifest, {"microbatch_size": 4}, backend=CPUAllocatorBackend())
    assert len(output["vectors"]) == 5 and output["stats"]["oom_retries"] == 1


def test_partial_validation_rejects_unknown_policy_fields(embedding_manifest):
    from workloads.embeddings.adapter import run, verify
    output = run(embedding_manifest, backend=DeterministicBackend())
    verdict = verify(embedding_manifest, output, {"skip_reference_check": True})
    assert not verdict["accepted"]
