"""Reproducible isolated-node and physical-pool service benchmarks.

Dry-run is the default and performs no filesystem or network operations. Live
execution requires an already approved, unchanged project and saved CLI login.
The two named managed nodes are the only resources this driver may control.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

CPU_NODE = "0a61b4dc-47dc-4349-84ba-de840e9b044d"
RTX_NODE = "ec6c1cbe-125d-43e4-bda6-a82b0fcdc34a"
CONTROLLED = {CPU_NODE, RTX_NODE}
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}
MAX_RESPONSE = 8 * 1024 * 1024


def utc():
    return datetime.now(UTC).isoformat()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path, limit=MAX_RESPONSE):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError("JSON input exceeds its bound")
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.chmod(0o600)
    temporary.replace(path)


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        raise ValueError("API redirects are forbidden; credentials stay on the explicit HTTPS origin")


class Api:
    def __init__(self, endpoint, token, ca_file):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in ("", "/api/v1")):
            raise ValueError("--endpoint must be an explicit HTTPS origin, optionally ending in /api/v1")
        self.base = endpoint.rstrip("/")
        if not self.base.endswith("/api/v1"):
            self.base += "/api/v1"
        self.token = token
        context = ssl.create_default_context(cafile=str(ca_file))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                 urllib.request.HTTPSHandler(context=context), NoRedirects())

    def request(self, path, body=None, method="GET", limit=MAX_RESPONSE):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API paths must remain on the configured origin")
        encoded = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=encoded, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(limit + 1)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"API {method} {path} failed with HTTP {error.code}") from None
        if len(data) > limit:
            raise ValueError("API response exceeds its declared bound")
        return data

    def json(self, path, body=None, method="GET"):
        return json.loads(self.request(path, body, method))

    def settings(self, node_id, mode, desired_state):
        if node_id not in CONTROLLED:
            raise ValueError("attempted to control a node outside the two authorized physical devices")
        return self.json(f"/nodes/{node_id}/settings", {"mode": mode, "desired_state": desired_state}, "PATCH")


def parse_job_id(stdout):
    ids = [line.strip() for line in stdout.splitlines()
           if re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", line.strip())]
    if len(ids) != 1:
        raise ValueError("CLI did not return exactly one unambiguous job receipt; do not replay automatically")
    return str(UUID(ids[0]))


def validate_completed(job, allowed_nodes, expected_stages):
    tasks = job.get("tasks", [])
    if (job.get("state") != "succeeded" or not tasks or job.get("failed_units") != 0
            or job.get("accepted_units") != job.get("total_units")):
        raise ValueError("job is partial, failed or lacks complete accepted work")
    if Counter(t.get("stage_id") for t in tasks) != Counter(expected_stages):
        raise ValueError("job task stages/counts differ from the immutable project")
    if len({t.get("id") for t in tasks}) != len(tasks):
        raise ValueError("duplicate accepted task identity")
    for task in tasks:
        if (task.get("state") != "succeeded" or task.get("node_id") not in allowed_nodes
                or not task.get("attempt_id") or task.get("verification_level") not in {"structural", "sampled", "full"}):
            raise ValueError("task is unverified, unfinished or accepted on an unintended node")


def find_artifact(artifacts, output):
    matches = [a for a in artifacts if a.get("name", "").split("/")[-1] == output]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one accepted {output} artifact")
    return matches[0]


def comparison(phases):
    rates = {p["name"]: p["correct_predictions"] / p["elapsed_seconds"] for p in phases}
    return {"cpu_correct_predictions_per_second": rates["cpu"], "rtx_correct_predictions_per_second": rates["rtx"],
            "pool_correct_predictions_per_second": rates["pool"],
            "efficiency": rates["pool"] / (rates["cpu"] + rates["rtx"]),
            "speedup": rates["pool"] / max(rates["cpu"], rates["rtx"]),
            "definition": "isolated single-node service baselines; correct predictions / complete batch window"}


def snapshot(api):
    nodes = api.json("/nodes")
    if not isinstance(nodes, list) or not CONTROLLED <= {n["id"] for n in nodes}:
        raise ValueError("the two authorized nodes are not visible")
    return nodes


def wait_idle(api, timeout, poll, require_no_jobs=True):
    deadline = time.monotonic() + timeout
    while True:
        nodes = snapshot(api)
        jobs = api.json("/jobs?limit=100") if require_no_jobs else []
        if not any(n["active_attempts"] for n in nodes) and not any(j["state"] not in TERMINAL for j in jobs):
            return nodes
        if time.monotonic() >= deadline:
            raise TimeoutError("pool did not become idle; no settings change is safe")
        time.sleep(poll)


def configure(api, originals, running, timeout, poll):
    wait_idle(api, timeout, poll)
    for node_id in sorted(CONTROLLED):
        api.settings(node_id, originals[node_id]["mode"], "running" if node_id in running else "paused")
    deadline = time.monotonic() + timeout
    while True:
        nodes = snapshot(api)
        controlled = {n["id"]: n for n in nodes if n["id"] in CONTROLLED}
        ready = True
        for node_id, node in controlled.items():
            expected = "running" if node_id in running else "paused"
            if node.get("settings_error"):
                raise RuntimeError("physical device reported a settings application error")
            ready &= (node["settings"]["desired_state"] == expected and node.get("actual_state") == expected
                      and node.get("applied_revision") == node["settings"]["revision"]
                      and node.get("applied_mode") == originals[node_id]["mode"]
                      and (expected != "running" or node["state"] == "ready"))
        if ready:
            if any(n["state"] == "ready" and n["id"] not in CONTROLLED for n in nodes):
                raise ValueError("another ready node could contaminate the isolated baseline")
            return controlled
        if time.monotonic() >= deadline:
            raise TimeoutError("physical device did not acknowledge the requested benchmark configuration")
        time.sleep(poll)


def inventory_identity(stdout, project):
    code, data = [], []
    for line in stdout.splitlines():
        match = re.fullmatch(r"  code (.+) \(([0-9]+) bytes\)", line)
        if match:
            relative, size = match.groups()
            path = (project / relative).resolve()
            if not path.is_relative_to(project):
                raise ValueError("unsafe code inventory path")
            if path.stat().st_size != int(size):
                raise ValueError("code size changed after the CLI snapshot")
            code.append({"path": relative, "size_bytes": int(size), "sha256": digest(path)})
        match = re.fullmatch(r"  data (.+) \(([0-9]+) bytes, sha256 ([0-9a-f]{64})\)", line)
        if match:
            data.append({"path": match[1], "size_bytes": int(match[2]), "sha256": match[3]})
    if not code or not data or not any(c["path"] == "manifest.json" for c in code):
        raise ValueError("CLI did not expose a complete code/data identity with bound manifest")
    return {"code": sorted(code, key=lambda x: x["path"]), "data": sorted(data, key=lambda x: x["path"])}


def validate_inventory(identity, manifest):
    expected = {(shard["path"], shard["sha256"]) for shard in manifest["shards"]}
    actual = {(item["path"], item["sha256"]) for item in identity["data"]}
    if actual != expected or len(identity["data"]) != len(expected):
        raise ValueError("CLI data inventory differs from the complete checksummed manifest")


def cli_environment(args, ca_file, inherited=None):
    child_env = dict(os.environ if inherited is None else inherited)
    child_env.pop("CAMPUS_TOKEN", None)
    for name in list(child_env):
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            child_env.pop(name)
    child_env.update(CAMPUS_STATE_DIR=str(args.state_dir.resolve()), CAMPUS_ENDPOINT=args.endpoint,
                     CAMPUS_CA_FILE=str(ca_file.resolve()))
    return child_env


def download_checked(api, artifact, destination):
    size, checksum = artifact.get("size_bytes"), artifact.get("sha256", "")
    if type(size) is not int or not 0 < size <= 1048576 or not re.fullmatch("[0-9a-f]{64}", checksum):
        raise ValueError("invalid artifact size/checksum metadata")
    artifact_id = str(UUID(artifact["id"]))
    data = api.request(f"/artifacts/{artifact_id}/content", limit=size)
    if len(data) != size or hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("downloaded artifact bytes do not match accepted metadata")
    destination.write_bytes(data)
    destination.chmod(0o600)
    return json.loads(data)


def verify_reference(reference, result, model, manifest_hash, model_hash):
    for key in ("train_count", "test_count", "class_counts", "item_ids", "labels", "predictions", "correct", "accuracy", "confusion_matrix"):
        if reference.get(key) != result.get(key):
            raise ValueError(f"result differs from independent reference: {key}")
    for key in ("train_count", "class_counts", "class_sums", "centroids"):
        if reference.get(key) != model.get(key):
            raise ValueError(f"model differs from independent reference: {key}")
    if (model.get("manifest_sha256") != manifest_hash or result.get("manifest_sha256") != manifest_hash
            or result.get("model_id") != model.get("model_id") or result.get("model_sha256") != model_hash):
        raise ValueError("result/model/manifest artifact bindings differ")
    prediction_hash = hashlib.sha256(bytes(result["predictions"])).hexdigest()
    if prediction_hash != reference["predictions_sha256"]:
        raise ValueError("prediction bytes differ from independent reference hash")
    return prediction_hash


def collect_events(api, job, destination):
    events, cursor, reset = [], 0, False
    for _ in range(100):
        page = api.json(f"/jobs/{job['id']}/events?cursor={cursor}")
        reset |= bool(page.get("reset"))
        batch = page.get("events", [])
        events.extend(batch)
        if not batch or page["cursor"] <= cursor:
            break
        cursor = page["cursor"]
    save(destination, {"events": events, "reset": reset, "cursor": cursor})
    times = {}
    for event in events:
        attempt = event.get("payload", {}).get("attempt_id")
        if attempt and event["type"] in ("task_leased", "result_accepted"):
            times.setdefault(attempt, {})[event["type"]] = event["occurred_at"]
    durations = []
    for task in job["tasks"]:
        timing = times.get(task["attempt_id"], {})
        if set(timing) == {"task_leased", "result_accepted"}:
            elapsed = (datetime.fromisoformat(timing["result_accepted"]) - datetime.fromisoformat(timing["task_leased"])).total_seconds()
            durations.append({"task_id": task["id"], "stage_id": task["stage_id"], "node_id": task["node_id"],
                              "lease_to_accept_seconds": elapsed, **timing})
    return {"source": "public job events", "reset": reset, "task_durations": durations,
            "includes": "transfer, startup, compute and verification after lease; not pure compute time"}


def execute(args):
    project = args.project.resolve()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("--out must be a fresh empty evidence directory")
    for path in (args.binary, project / "campus.json", project / "manifest.json", args.reference):
        if not path.is_file():
            raise ValueError(f"required explicit input does not exist: {path}")
    config = read_json(project / "campus.json")
    manifest = read_json(project / "manifest.json")
    reference = read_json(args.reference)
    expected_stages = {stage["id"]: (sum(any(fnmatch.fnmatchcase(s["path"], pattern) for pattern in stage["inputs"])
                                         for s in manifest["shards"]) if stage["kind"] == "map" else 1)
                       for stage in config["stages"]}
    if set(expected_stages) != {"fit", "model", "evaluate", "aggregate"}:
        raise ValueError("this correctness benchmark requires the four Fashion-MNIST stages")
    owner = read_json(args.owner_runtime / "owner-cli.json", 65536)
    token = owner.get("token")
    if not isinstance(token, str) or not 32 <= len(token) <= 160:
        raise ValueError("designated owner credential has no valid CLI token")
    ca_file = args.ca_file or args.owner_runtime / "campus-local-ca.pem"
    api = Api(args.endpoint, token, ca_file)
    out.mkdir(parents=True, mode=0o700, exist_ok=True)
    report = {"schema_version": "1", "started_at": utc(), "benchmark": "isolated single-node service baselines and physical pool",
              "binary_sha256": digest(args.binary), "config_sha256": digest(project / "campus.json"),
              "manifest_sha256": digest(project / "manifest.json"), "reference_sha256": digest(args.reference),
              "repetitions_per_window": args.repetitions, "controlled_node_ids": sorted(CONTROLLED), "phases": [],
              "utilization": {"available": False, "reason": "no public read telemetry/metrics route in current API; node offers are capacity, not measured use"},
              "limitations": ["cache state can differ; before/after node cache inventories are preserved",
                              "selected task image digest is not exposed by Job DTO; node image offers and exact CLI execution contract are preserved",
                              "23.698-second independent local serial debug run is unmatched and excluded from comparison"],
              "restoration": {"attempted": False, "succeeded": False}}
    originals, identity = {}, None
    try:
        initial = wait_idle(api, args.timeout_seconds, args.poll_seconds)
        if any(n["state"] == "ready" and n["id"] not in CONTROLLED for n in initial):
            raise ValueError("another ready physical node would contaminate the benchmark")
        report["initial_nodes"] = initial
        originals = {n["id"]: dict(n["settings"]) for n in initial if n["id"] in CONTROLLED}
        if any(not n.get("supports_web_controls") for n in initial if n["id"] in CONTROLLED):
            raise ValueError("both devices must support durable managed settings")
        save(out / "report.json", report)
        child_env = cli_environment(args, ca_file)
        for name, running in (("cpu", {CPU_NODE}), ("rtx", {RTX_NODE}), ("pool", CONTROLLED)):
            print(f"Preparing {name} service window", file=sys.stderr, flush=True)
            before = configure(api, originals, running, args.timeout_seconds, args.poll_seconds)
            phase = {"name": name, "nodes_before": before, "jobs": [], "observations": [], "started_at": utc()}
            report["phases"].append(phase)
            phase_dir = out / name
            phase_dir.mkdir(mode=0o700)
            begin = time.monotonic()
            phase["monotonic_start_seconds"] = begin
            job_ids = []
            for number in range(args.repetitions):
                print(f"Submitting {name} project {number + 1}/{args.repetitions}", file=sys.stderr, flush=True)
                started = time.monotonic()
                run = subprocess.run([str(args.binary.resolve()), "run", "--yes"], cwd=project, env=child_env,
                                     stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                     timeout=args.timeout_seconds, check=False)
                stdout, stderr = run.stdout.replace(token, "[REDACTED]"), run.stderr.replace(token, "[REDACTED]")
                (phase_dir / f"submission-{number + 1}.stdout.txt").write_text(stdout, encoding="utf-8")
                (phase_dir / f"submission-{number + 1}.stderr.txt").write_text(stderr, encoding="utf-8")
                if run.returncode:
                    raise RuntimeError(f"CLI submission failed (exit {run.returncode}); inspect evidence and reconcile its saved receipt before retrying")
                current_identity = inventory_identity(stdout, project)
                validate_inventory(current_identity, manifest)
                if identity is None:
                    identity = current_identity
                    report["immutable_inventory"] = identity
                elif identity != current_identity:
                    raise ValueError("code or input identity changed between benchmark submissions")
                job_id = parse_job_id(stdout)
                if job_id in job_ids or any(job_id == j["id"] for p in report["phases"] for j in p["jobs"]):
                    raise ValueError("CLI returned an existing job; refusing duplicate-work counting")
                job_ids.append(job_id)
                phase["jobs"].append({"id": job_id, "submission_seconds": time.monotonic() - started})
                save(out / "report.json", report)
            deadline = time.monotonic() + args.timeout_seconds
            previous = None
            while True:
                jobs = [api.json(f"/jobs/{job_id}") for job_id in job_ids]
                if any(j["state"] in TERMINAL - {"succeeded"} for j in jobs):
                    raise ValueError("a benchmark job failed or completed only partially")
                progress = [(j["id"], j["state"], j["accepted_units"]) for j in jobs]
                if progress != previous:
                    phase["observations"].append({"elapsed_seconds": time.monotonic() - begin, "jobs": progress})
                    previous = progress
                if all(j["state"] == "succeeded" for j in jobs):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("benchmark projects did not finish before the observation deadline")
                time.sleep(args.poll_seconds)
            phase["all_jobs_accepted_seconds"] = time.monotonic() - begin
            phase["correct_predictions"], phase["verified_test_images"] = 0, 0
            useful_nodes = []
            for index, job in enumerate(jobs):
                validate_completed(job, running, expected_stages)
                job_dir = phase_dir / job["id"]
                job_dir.mkdir(mode=0o700)
                save(job_dir / "job.json", job)
                model_meta = find_artifact(job["artifacts"], "model.json")
                result_meta = find_artifact(job["artifacts"], "result.json")
                model = download_checked(api, model_meta, job_dir / "model.json")
                result = download_checked(api, result_meta, job_dir / "result.json")
                prediction_hash = verify_reference(reference, result, model, report["manifest_sha256"], model_meta["sha256"])
                phase["correct_predictions"] += result["correct"]
                phase["verified_test_images"] += result["test_count"]
                useful_nodes.append({t["node_id"] for t in job["tasks"] if t["stage_id"] in {"fit", "evaluate"}})
                phase["jobs"][index].update({"accepted_tasks": job["tasks"], "model_artifact": model_meta,
                                             "result_artifact": result_meta, "predictions_sha256": prediction_hash,
                                             "correct": result["correct"], "test_count": result["test_count"],
                                             "training_accelerators": result["training_accelerators"],
                                             "evaluation_accelerators": result["evaluation_accelerators"],
                                             "timing": collect_events(api, job, job_dir / "events.json")})
            phase["elapsed_seconds"] = time.monotonic() - begin
            phase["monotonic_end_seconds"] = time.monotonic()
            phase["finished_at"] = utc()
            phase["nodes_after"] = {n["id"]: n for n in snapshot(api) if n["id"] in CONTROLLED}
            phase["accepted_tasks_by_node"] = dict(Counter(t["node_id"] for j in jobs for t in j["tasks"]))
            if name == "pool" and not any(nodes == CONTROLLED for nodes in useful_nodes):
                raise ValueError("no individual pool job accepted useful map work from both physical devices")
            save(out / "report.json", report)
        report["comparison"] = comparison(report["phases"])
        report["window_includes"] = ["all three CLI planning/submission calls", "API/queueing", "input transfers", "container startup", "compute", "verification", "result downloads and independent comparison"]
        report["passed"] = True
    except BaseException as error:
        report["passed"] = False
        report["error"] = f"{type(error).__name__}: {str(error).replace(token, '[REDACTED]')}"
        raise
    finally:
        if originals:
            report["restoration"]["attempted"] = True
            try:
                wait_idle(api, args.timeout_seconds, args.poll_seconds)
                for node_id in sorted(CONTROLLED):
                    original = originals[node_id]
                    api.settings(node_id, original["mode"], original["desired_state"])
                report["restoration"]["desired_settings_restored"] = originals
                report["restoration"]["succeeded"] = True
                report["restoration"]["final_nodes"] = snapshot(api)
            except BaseException as error:  # noqa: BLE001 - restoration must record interrupts and transport errors
                report["restoration"]["error"] = f"{type(error).__name__}: {str(error).replace(token, '[REDACTED]')}"
                report["passed"] = False
        report["finished_at"] = utc()
        save(out / "report.json", report)
    if not report["restoration"]["succeeded"]:
        raise RuntimeError("benchmark finished but restoring original desired settings failed; inspect report.json")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="explicitly permit live job submissions and the two node settings changes")
    for flag in ("binary", "project", "state-dir", "owner-runtime", "reference", "out"):
        parser.add_argument("--" + flag, required=True, type=Path)
    parser.add_argument("--endpoint", required=True, help="strict HTTPS coordinator origin")
    parser.add_argument("--ca-file", type=Path, help="default: OWNER_RUNTIME/campus-local-ca.pem")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args(argv)
    if not 1 <= args.repetitions <= 20 or not 1 <= args.poll_seconds <= 30 or args.timeout_seconds < 30:
        parser.error("repetitions must be 1–20, poll 1–30 seconds, and timeout at least 30 seconds")
    if not args.execute:
        print(json.dumps({"execute": False, "external_side_effects": "none; inputs and credentials are not read",
                          "phases": ["CPU node only", "RTX laptop only", "both physical nodes"],
                          "repetitions_per_phase": args.repetitions, "controlled_nodes": sorted(CONTROLLED),
                          "required_before_execution": "successful interactive CLI review of unchanged project and saved CLI login",
                          "restoration": "original desired mode/state in finally; no Docker or native process controls"}, indent=2))
        return 0
    try:
        report = execute(args)
    except Exception as error:  # noqa: BLE001 - sanitized CLI error boundary
        # Detailed sanitized diagnostics are retained in private evidence.
        print(f"Benchmark failed: {type(error).__name__}; inspect the explicit evidence directory", file=sys.stderr)
        return 1
    print(json.dumps(report["comparison"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
