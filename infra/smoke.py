"""Run a real, single-device Docker checksum workload against the HTTPS coordinator."""

import argparse
import hashlib
import json
import platform
import ssl
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

IMAGE = "python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df"
ADAPTER = {"id": "sha256", "version": "1"}
LIMITS = {
    "cpu_millis": 1000,
    "memory_bytes": 134_217_728,
    "disk_bytes": 67_108_864,
    "gpu_slots": 0,
    "vram_bytes": 0,
}
PROGRAM = """
import hashlib
from pathlib import Path
source = Path('/inputs/source.bin')
with source.open('rb') as file:
    digest = hashlib.file_digest(file, 'sha256').hexdigest()
Path('/outputs/checksum.txt').write_text(digest + '\\n')
"""


def canonical(data):
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def checksum(data):
    return hashlib.sha256(data).hexdigest()


class Client:
    def __init__(self, origin, ca, credentials):
        self.origin = origin.rstrip("/") + "/api/v1"
        self.tls = ssl.create_default_context(cafile=ca)
        self.headers = (
            {"Authorization": "Bearer " + credentials["token"]} if credentials else {}
        )

    def call(self, method, path, body=None, *, raw=None, headers=None, binary=False):
        headers = {**self.headers, **(headers or {})}
        if body is not None:
            raw = canonical(body)
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(
                Request(self.origin + path, method=method, data=raw, headers=headers),
                context=self.tls,
                timeout=35,
            ) as response:
                data = response.read()
                return data if binary else json.loads(data) if data else None
        except HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("code", "HTTP_ERROR")
            except ValueError:
                message = "HTTP_ERROR"
            raise RuntimeError(
                f"{method} {path} returned {exc.code} ({message})"
            ) from None

    def upload(self, name, data, kind="input", scope=None):
        sha = checksum(data)
        row = self.call(
            "POST",
            "/uploads",
            {
                "name": name,
                "size_bytes": len(data),
                "sha256": sha,
                "kind": kind,
                **(scope or {}),
            },
        )
        path = "/uploads/" + row["id"]
        self.call(
            "PUT",
            path + "/parts/0",
            raw=data,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Content-SHA256": sha,
            },
        )
        return self.call("POST", path + "/finalize")

    def poll(self, path, pending, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.call("GET", path)
            if record["state"] not in pending:
                return record
            time.sleep(0.2)
        raise TimeoutError(f"Timed out waiting for {path}")


def run(client, worker, text):
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "AMD64": "amd64"}[
        platform.machine()
    ]
    target = f"linux/{arch}"
    subprocess.run(
        ["docker", "image", "inspect", IMAGE], check=True, stdout=subprocess.DEVNULL
    )
    source = text.encode()
    artifact = client.upload("source.bin", source)
    binding = {
        "artifact_id": artifact["id"],
        "mount_path": "/inputs/source.bin",
        "archive": False,
    }
    manifest = {
        "project_digest": None,
        "inputs": [
            {
                **binding,
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
            },
        ],
    }
    preview = client.call(
        "POST",
        "/job-previews",
        {
            "recipe_id": "sha256",
            "inputs": [binding],
            "prepared_plan": {
                "plan_revision": 1,
                "input_manifest_digest": checksum(canonical(manifest)),
                "execution_kind": "single",
                "retry_policy": {},
                "job_deadline_at": (
                    datetime.now(UTC) + timedelta(minutes=5)
                ).isoformat(),
                "stages": [
                    {
                        "stage_id": "checksum",
                        "adapter": ADAPTER,
                        "variants": [
                            {
                                "variant_id": f"python-{arch}",
                                "image_digest": IMAGE,
                                "platform": target,
                                "accelerator": "cpu",
                                "resource_requirements": LIMITS,
                            }
                        ],
                        "argv": ["python", "-c", PROGRAM],
                        "working_dir": "/workspace",
                        "bindings": [binding],
                        "outputs": [{"path": "checksum.txt", "max_bytes": 65}],
                        "task_deadline_ms": 30_000,
                        "validation_policy": "full",
                        "resume_policy": "restart",
                    }
                ],
            },
        },
    )
    preview = client.poll(
        "/job-previews/" + preview["id"], {"inspecting", "planning", "validating"}, 15
    )
    if preview["state"] != "ready":
        raise RuntimeError(f"Preview failed: {preview['error_code']}")
    invitation = client.call(
        "POST", "/invitations", {"kind": "device", "name": "Docker smoke device"}
    )
    credential = worker.call(
        "POST",
        "/invitations/redeem",
        {
            "token": invitation["token"],
            "installation_id": str(uuid4()),
            "protocol_version": 1,
        },
    )
    worker.headers["Authorization"] = "Bearer " + credential["token"]
    session = worker.call(
        "POST",
        "/node-sessions",
        {
            "protocol_version": 1,
            "capabilities": {
                "os": {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}[
                    platform.system()
                ],
                "arch": arch,
                "cpu_logical": 1,
                "memory_available_bytes": LIMITS["memory_bytes"],
                "disk_available_bytes": LIMITS["disk_bytes"],
                "runtime": {
                    "type": "docker",
                    "healthy": True,
                    "version": subprocess.check_output(
                        ["docker", "version", "--format", "{{.Server.Version}}"],
                        text=True,
                    ).strip(),
                },
                "power_state": "unknown",
                "offered_budget": LIMITS,
                "preflight_revision": 1,
                "image_digests": [IMAGE],
            },
        },
    )
    worker.headers["X-Node-Session"] = session["id"]
    job = client.call(
        "POST",
        "/jobs",
        {"preview_id": preview["id"], "plan_revision": 1},
        headers={"Idempotency-Key": str(uuid4())},
    )
    work = worker.call("POST", "/work/next", {"wait_ms": 2000})
    if (
        not work
        or work["job_id"] != job["id"]
        or work["image_digest"] != IMAGE
        or work["argv"] != ["python", "-c", PROGRAM]
    ):
        raise RuntimeError("Expected the smoke assignment on this isolated test pool")
    proof = {key: work[key] for key in ("generation", "lease_token")}
    attempt = "/attempts/" + work["attempt_id"]
    worker.call("POST", attempt + "/started", proof)
    with tempfile.TemporaryDirectory(prefix="campus-smoke-") as directory:
        path = Path(directory)
        (path / "inputs").mkdir()
        (path / "outputs").mkdir()
        (path / "outputs").chmod(0o777)
        (path / "inputs/source.bin").write_bytes(
            worker.call("GET", f"/artifacts/{artifact['id']}/content", binary=True)
        )
        container = "campus-smoke-" + uuid4().hex[:12]
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    container,
                    "--pull=never",
                    "--platform",
                    target,
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--pids-limit=32",
                    "--memory=128m",
                    "--cpus=1",
                    "--user=65534:65534",
                    "--mount",
                    f"type=bind,src={path / 'inputs'},dst=/inputs,readonly",
                    "--mount",
                    f"type=bind,src={path / 'outputs'},dst=/outputs",
                    IMAGE,
                    *work["argv"],
                ],
                timeout=20,
                check=True,
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", container], capture_output=True, check=False
            )
        data = (path / "outputs/checksum.txt").read_bytes()
    output = worker.upload(
        "checksum.txt",
        data,
        kind="output",
        scope={"attempt_id": work["attempt_id"], "proof": proof},
    )
    completion = {
        **proof,
        "manifest": {
            "item_ids": [],
            "outputs": [
                {"path": "checksum.txt", "artifact_id": output["id"]},
            ],
        },
    }
    receipt = worker.call("POST", attempt + "/complete", completion)
    result = client.poll(
        "/jobs/" + job["id"], {"queued", "running", "waiting_for_capacity"}, 15
    )
    assert result["state"] == "succeeded", result
    assert result["accepted_units"] == 1
    assert worker.call("POST", attempt + "/complete", completion) == receipt
    assert (
        client.call("GET", f"/artifacts/{output['id']}/content", binary=True)
        == (checksum(source) + "\n").encode()
    )
    print(f"Job {job['id']} succeeded on node {session['node_id']}: {checksum(source)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://localhost:8443")
    parser.add_argument("--ca", required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--text", default="Campus Edge Fabric — проверка — 校园计算")
    args = parser.parse_args()
    credentials = json.loads(args.credential_file.read_text())
    run(
        Client(args.origin, args.ca, credentials),
        Client(args.origin, args.ca, None),
        args.text,
    )


if __name__ == "__main__":
    main()
