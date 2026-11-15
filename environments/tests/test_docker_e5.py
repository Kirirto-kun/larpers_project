"""Opt-in actual cached E5 CPU smoke; no model/image download and no benchmark claim."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
_MODEL_FILES = {"config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
                "special_tokens_map.json", "sentencepiece.bpe.model"}

# No submitted source is run. Only this fixed trusted payload and the image's
# registered adapter/backend are executable; model/tokenizer custom code is off.
_WORKER = r'''
import copy, json, math, os, pathlib, socket, sys, time
sys.path.insert(0, "/opt/campus")
from workloads.adapter import dispatch
from workloads.embeddings.adapter import aggregate, create_reference, run, search
from workloads.embeddings.backend import E5Encoder
from workloads.common import write_json

payload = json.loads(sys.argv[1])
manifest = payload["manifest"]
started = time.monotonic()
status = dict(line.split(":", 1) for line in pathlib.Path("/proc/self/status").read_text().splitlines() if ":" in line)
assert os.getuid() == 65532 and os.getgid() == 65532
assert int(status["CapEff"].strip(), 16) == 0 and status["NoNewPrivs"].strip() == "1"
assert not pathlib.Path("/var/run/docker.sock").exists()
network_blocked = False
try:
    with socket.create_connection(("1.1.1.1", 443), timeout=0.5):
        pass
except OSError:
    network_blocked = True
assert network_blocked, "network-none container unexpectedly connected outbound"

def call(operation, **extra):
    response = dispatch({"schema_version":"1", "adapter":{"id":"embeddings.e5","version":"1"},
                         "operation":operation, "payload":{"manifest":manifest, **extra}})
    assert response["error"] is None, response
    return response["result"]

validated = call("validate_input")
assert validated["valid"] and validated["units"] == 4
assert validated["adapter_digest"] == payload["expected_adapter_digest"]
assert validated["adapter_digest_kind"] == "trusted_source_lf_sha256"
assert validated["model_kind"] == "pretrained_transformer"
encoder_started = time.monotonic()
encoder = E5Encoder(manifest["model"], device="cpu")
encoder_load_ms = (time.monotonic() - encoder_started) * 1000
assert encoder.device == "cpu" and not encoder.model.training
assert encoder.model.config.hidden_size == 384
reference = create_reference(manifest, "/outputs/reference.json", backend=encoder,
                             device="cpu", device_id="local-functional-docker-cpu")
output = run(manifest, {"microbatch_size":2}, backend=encoder, device="cpu")
policy = {"mode":"full_reference", "reference_path":"/outputs/reference.json"}
verdict = call("verify", outputs=output, policy=policy)
assert verdict["accepted"] and verdict["benchmark_eligible"]
assert verdict["coverage"] == "full_reference" and verdict["checked_items"] == 4
assert verdict["validation_scope"] == "entire_manifest" and verdict["manifest_items"] == 4
for key in ("manifest_digest", "model_digest", "adapter_digest"):
    assert verdict[key] == validated[key]
index = aggregate({"manifest":manifest, "outputs":[output], "policy":policy})
write_json("/outputs/index.json", index)
assert index["units"] == 4 and index["quality"] == "verified"
assert any("校园" in row["text"] for row in index["vectors"])
results = search(manifest, index, "How are checkpoints saved?", top_k=3, backend=encoder, device="cpu")
by_id = {row["item_id"]:row for row in index["vectors"]}
assert results and by_id[results[0]["item_id"]]["external_id"] == "checkpoints", results
assert results[0]["source"] == "documents.jsonl" and results[0]["line"] == 1
assert "checkpoint" in results[0]["excerpt"].lower()
assert len({row["item_id"] for row in index["vectors"]}) == 4
norm_error = max(abs(math.sqrt(sum(value*value for value in row["vector"])) - 1) for row in output["vectors"])
assert norm_error < 1e-5
assert all(len(row["vector"]) == 384 and all(math.isfinite(value) for value in row["vector"]) for row in output["vectors"])

bad = copy.deepcopy(output)
bad["vectors"][0]["vector"] = [1.0] + [0.0] * 383
structural = call("verify", outputs=bad, policy={"mode":"structural"})
assert structural["accepted"] and not structural["benchmark_eligible"]
rejected = call("verify", outputs=bad, policy=policy)
assert not rejected["accepted"] and not rejected["benchmark_eligible"]
assert "reference vector mismatch" in " ".join(rejected["errors"])
summary = {
  "validation_kind":"local_functional_only",
  "formal_registered_benchmark":False,
  "model_revision":manifest["model"]["revision"],
  "units":4, "dimensions":384, "norm_max_error":norm_error,
  "full_reference_accepted":True, "corrupted_unit_vector_rejected":True,
  "query_top_source":results[0]["source"], "query_top_line":results[0]["line"],
  "query_top_id":by_id[results[0]["item_id"]]["external_id"],
  "query_top_score":results[0]["score"], "query_excerpt":results[0]["excerpt"][:160],
  "manifest_digest":verdict["manifest_digest"], "model_digest":verdict["model_digest"],
  "adapter_digest":verdict["adapter_digest"],
  "uid":os.getuid(), "gid":os.getgid(), "cap_eff":status["CapEff"].strip(),
  "no_new_privs":1, "outbound_blocked":network_blocked,
  "encoder_load_ms":encoder_load_ms, "reference_duration_ms":reference["reference_duration_ms"],
  "map_execution_duration_ms":output["stats"]["execution_duration_ms"],
  "validation_duration_ms":verdict["validation_duration_ms"],
  "functional_duration_ms":(time.monotonic()-started)*1000
}
print(json.dumps(summary, sort_keys=True, ensure_ascii=False, allow_nan=False), flush=True)
'''


def _parent_code():
    # This is PID1 inside the test container. Its own bounded wait keeps the
    # fixed CPU child bounded if the host pytest/docker CLI disappears.
    return '''
import json, os, resource, signal, subprocess, sys
child = None
def stop_group():
    if child is not None and child.poll() is None:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
def terminate(signum, frame):
    stop_group()
    raise SystemExit(128 + signum)
signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
resource.setrlimit(resource.RLIMIT_CPU, (150, 155))
try:
    child = subprocess.Popen([sys.executable, "-I", "-u", "-c", ''' + repr(_WORKER) + ''', sys.argv[1]],
                             start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        output, error = child.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        stop_group()
        child.communicate(timeout=5)
        print("trusted E5 test parent hard timeout", file=sys.stderr, flush=True)
        raise SystemExit(124)
    if len(output) > 65536:
        print("trusted E5 test output limit exceeded", file=sys.stderr)
        raise SystemExit(125)
    sys.stdout.buffer.write(output)
    sys.stderr.buffer.write(error[-16384:])
    raise SystemExit(child.returncode)
finally:
    stop_group()
'''


def _docker(*args, timeout=30):
    return subprocess.run(["docker", *args], text=True, encoding="utf-8", capture_output=True,
                          timeout=timeout, check=False)


def _readonly_mount(source, destination):
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(["type=bind", f"source={source}", f"target={destination}", "readonly"])
    return buffer.getvalue()


def _configuration():
    profile_value, model_value = os.environ.get("CAMPUS_DOCKER_PROFILE"), os.environ.get("CAMPUS_E5_MODEL")
    if not profile_value or not model_value:
        pytest.skip("set both CAMPUS_DOCKER_PROFILE and CAMPUS_E5_MODEL for cached actual E5 Docker smoke; no autodownload")
    profile_path, model_path = Path(profile_value), Path(model_value)
    assert profile_path.is_absolute() and model_path.is_absolute(), "opt-in profile/model paths must be absolute"
    if not model_path.is_dir():
        pytest.skip("prepared E5 cache is unavailable; this opt-in test never downloads")
    assert not model_path.is_symlink()
    profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile_data["schema_version"] == "1"
    profiles = [profile for profile in profile_data["profiles"]
                if profile.get("accelerator") == "cpu" and profile.get("platform") == "linux/amd64"]
    assert len(profiles) == 1, "one pinned CPU linux/amd64 profile required"
    profile = profiles[0]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", profile["image_digest"])
    lock_path = model_path.parent / (model_path.name + ".model-lock.json")
    assert lock_path.is_file() and lock_path.stat().st_size <= 1 << 20, "explicitly prepared pinned model lock required"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["schema_version"] == "1" and lock["model_id"] == "intfloat/multilingual-e5-small"
    assert lock["revision"] == _REVISION and set(lock["files"]) == _MODEL_FILES
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in lock["files"].values())
    assert {path.name for path in model_path.iterdir()} == _MODEL_FILES
    documents = _ROOT / "workloads" / "examples" / "documents.jsonl"
    assert documents.is_file() and documents.stat().st_size <= 65536
    assert len([json.loads(line) for line in documents.read_text(encoding="utf-8").splitlines() if line.strip()]) == 4
    return profile, model_path.resolve(), documents.resolve(), lock


def test_docker_cpu_actual_e5_reference_index_search_and_corruption():
    profile, model_path, documents, lock = _configuration()
    assert shutil.which("docker"), "opt-in requested but Docker CLI unavailable"
    inspected = _docker("image", "inspect", profile["image_digest"])
    assert inspected.returncode == 0, inspected.stderr
    image = json.loads(inspected.stdout)[0]
    assert image["Id"] == profile["image_digest"]
    assert image["Os"] == "linux" and image["Architecture"] == "amd64"
    assert image["Config"]["Labels"]["io.campus.guard"] == "1"
    from workloads.provenance import adapter_digest
    expected_adapter = adapter_digest("embeddings.e5", "1")
    payload = {"expected_adapter_digest":expected_adapter, "manifest":{
        "schema_version":"1", "kind":"embeddings",
        "model":{"id":"intfloat/multilingual-e5-small", "revision":_REVISION,
                 "path":"/model", "files":lock["files"]},
        "inputs":[{"path":"/documents.jsonl", "format":"jsonl", "source":"documents.jsonl"}],
        "preprocessing":{"chunk_chars":1000,"overlap_chars":100}}}
    name = "campus-e5-functional-" + uuid.uuid4().hex[:12]
    create = ["create", "--rm", "--name", name, "--pull=never", "--platform=linux/amd64",
              "--label=io.campus.test=e5-local-functional", "--network=none", "--read-only", "--restart=no",
              "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--user=65532:65532",
              "--cpus=2", "--memory=2g", "--memory-swap=2g", "--pids-limit=128", "--stop-timeout=5",
              "--ulimit", "nofile=256:256", "--ulimit", "core=0:0",
              "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
              "--tmpfs", "/outputs:rw,nosuid,nodev,noexec,size=16m,uid=65532,gid=65532,mode=0700",
              "--mount", _readonly_mount(model_path, "/model"),
              "--mount", _readonly_mount(documents, "/documents.jsonl"),
              "--env=HOME=/tmp", "--env=HF_HOME=/tmp/hf", "--env=HF_HUB_OFFLINE=1",
              "--env=TRANSFORMERS_OFFLINE=1", "--env=HF_HUB_DISABLE_TELEMETRY=1",
              "--entrypoint=/opt/campus/.venv/bin/python", profile["image_digest"],
              "-I", "-u", "-c", _parent_code(), json.dumps(payload, ensure_ascii=False)]
    started = time.monotonic()
    try:
        created = _docker(*create, timeout=45)
        assert created.returncode == 0, created.stderr
        container = _docker("inspect", name)
        assert container.returncode == 0, container.stderr
        config = json.loads(container.stdout)[0]
        host = config["HostConfig"]
        assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
        assert "ALL" in host["CapDrop"] and "no-new-privileges:true" in host["SecurityOpt"]
        assert config["Config"]["User"] == "65532:65532"
        assert host["Memory"] == 2 << 30 and host["MemorySwap"] == 2 << 30
        assert host["PidsLimit"] == 128 and host["NanoCpus"] == 2_000_000_000
        assert host["RestartPolicy"]["Name"] == "no"
        assert len(config["Mounts"]) == 2
        assert {mount["Destination"] for mount in config["Mounts"]} == {"/model", "/documents.jsonl"}
        assert all(mount["Type"] == "bind" and not mount["RW"] for mount in config["Mounts"])
        assert set(host["Tmpfs"]) == {"/tmp", "/outputs"}
        result = _docker("start", "--attach", name, timeout=210)
        assert result.returncode == 0, result.stderr
        summary = json.loads(result.stdout)
        assert summary["validation_kind"] == "local_functional_only"
        assert not summary["formal_registered_benchmark"]
        assert summary["units"] == 4 and summary["dimensions"] == 384
        assert summary["full_reference_accepted"] and summary["corrupted_unit_vector_rejected"]
        assert summary["model_revision"] == _REVISION and summary["adapter_digest"] == expected_adapter
        assert summary["query_top_id"] == "checkpoints" and summary["outbound_blocked"]
        summary.update(image_digest=profile["image_digest"], platform=profile["platform"],
                       host_observed_duration_ms=(time.monotonic()-started)*1000)
        print("E5_DOCKER_FUNCTIONAL " + json.dumps(summary, sort_keys=True, ensure_ascii=False))
    finally:
        # Also cleans a stopped container if create succeeded but start/inspect
        # failed; PID1's independent timeout remains active on host CLI loss.
        cleanup = _docker("rm", "--force", name, timeout=20)
        assert cleanup.returncode == 0 or "No such container" in cleanup.stderr, cleanup.stderr
