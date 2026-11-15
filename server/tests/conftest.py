import hashlib
import json
import os
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from campus import models as m
from campus.auth import issue_credential
from campus.config import Settings
from campus.db import digest
from campus.main import create_app, maintenance, plan_once, verify_once

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "campus/test@sha256:" + "1" * 64
LIMITS = {
    "cpu_millis": 1000,
    "memory_bytes": 268435456,
    "disk_bytes": 1048576,
    "gpu_slots": 0,
    "vram_bytes": 0,
}
ADAPTER = {"id": "test", "version": "1"}
VARIANT = {
    "variant_id": "cpu",
    "image_digest": IMAGE,
    "platform": "linux/arm64",
    "accelerator": "cpu",
    "resource_requirements": LIMITS,
}
CAPS = {
    "os": "linux",
    "arch": "arm64",
    "cpu_logical": 2,
    "memory_available_bytes": 1073741824,
    "disk_available_bytes": 1073741824,
    "runtime": {"type": "docker", "version": "29.4", "healthy": True},
    "power_state": "ac",
    "offered_budget": LIMITS,
    "preflight_revision": 1,
    "image_digests": [IMAGE],
}
URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://campus:campus@127.0.0.1:5432/campus")


@pytest.fixture(scope="session")
def database():
    # Each run uses an independent PostgreSQL schema. Existing application data is untouched.
    engine = create_engine(URL, isolation_level="AUTOCOMMIT")
    schema = "test_" + uuid4().hex
    with engine.connect() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in URL else "?"
    url = URL + separator + f"options=-csearch_path%3D{schema}"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        with engine.connect() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


class Harness:
    def __init__(self, app, client, owner):
        self.app = app
        self.runtime = app.state.runtime
        self.client = client
        self.owner = owner

    def call(self, verb, path, *, body=None, headers=None, code=200, **kwargs):
        if headers is None:
            headers = self.owner
        response = self.client.request(verb, "/api/v1" + path, headers=headers, json=body, **kwargs)
        assert response.status_code == code, response.text
        return (
            response.json()
            if response.content and "application/json" in response.headers.get("content-type", "")
            else response
        )

    def device(self, caps=None):
        invite = self.call("POST", "/invitations", body={"kind": "device", "name": "Laptop"}, code=201)
        credential = self.call(
            "POST",
            "/invitations/redeem",
            body={
                "token": invite["token"],
                "installation_id": str(uuid4()),
                "protocol_version": 1,
            },
            headers={},
            code=201,
        )
        auth = {"Authorization": "Bearer " + credential["token"]}
        session = self.call(
            "POST",
            "/node-sessions",
            headers=auth,
            code=201,
            body={"protocol_version": 1, "capabilities": caps or CAPS},
        )
        return {**auth, "X-Node-Session": session["id"]}, session

    def preview(self, *, policy="structural", backoff=0, plan=None, fields=None):
        if plan is None:
            plan = sample_plan(policy=policy, backoff=backoff)
        view = self.call(
            "POST", "/job-previews", code=202, body={"prepared_plan": plan, **(fields or {})}
        )
        assert view["state"] == "inspecting"
        plan_once(self.runtime)
        return self.call("GET", f"/job-previews/{view['id']}")

    def job(self, **kwargs):
        view = self.preview(**kwargs)
        assert view["state"] == "ready", view
        body = {"preview_id": view["id"], "plan_revision": view["plan_revision"]}
        return self.call(
            "POST", "/jobs", body=body, headers={**self.owner, "Idempotency-Key": str(uuid4())}, code=201
        )

    def claim(self, device, code=200):
        return self.call("POST", "/work/next", headers=device, body={"wait_ms": 0}, code=code)

    def attempt(self, device, assignment, operation, *, extra=None, code=200):
        return self.call(
            "POST",
            f"/attempts/{assignment['attempt_id']}/{operation}",
            headers=device,
            body={**proof(assignment), **(extra or {})},
            code=code,
        )

    def upload(self, data, name, *, device=None, assignment=None, kind="input", part_size=8, code=200):
        parts = [data[n : n + part_size] for n in range(0, len(data), part_size)]
        body = {
            "name": name,
            "kind": kind,
            "size_bytes": len(data),
            "sha256": checksum(data),
            "part_size_bytes": part_size,
        }
        if assignment:
            body.update(attempt_id=assignment["attempt_id"], proof=proof(assignment))
        upload = self.call("POST", "/uploads", headers=device, body=body, code=201)
        for number, part in enumerate(parts):
            self.call(
                "PUT",
                f"/uploads/{upload['id']}/parts/{number}",
                content=part,
                headers={**(device or self.owner), "X-Content-SHA256": checksum(part)},
            )
        return self.call("POST", f"/uploads/{upload['id']}/finalize", headers=device, code=code)

    def complete(self, device, assignment, data=b"computed", *, verify=True):
        artifact = self.upload(data, "result.txt", device=device, assignment=assignment, kind="output")
        result = {
            "outputs": [{"path": "result.txt", "artifact_id": artifact["id"]}],
            "item_ids": assignment["item_ids"],
        }
        receipt = self.attempt(device, assignment, "complete", extra={"manifest": result}, code=202)
        if verify:
            verify_once(self.runtime)
        return SimpleNamespace(artifact=artifact, manifest=result, receipt=receipt)

    def maintenance(self):
        maintenance(self.runtime, 1)


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def proof(assignment):
    return {key: assignment[key] for key in ("generation", "lease_token")}


def sample_plan(
    *, policy="structural", backoff=0, stages=None, kind="single", manifest=None, project=None
):
    if stages is None:
        stages = [
            {
                "stage_id": "execute",
                "adapter": ADAPTER,
                "variants": [deepcopy(VARIANT)],
                "argv": ["test", "hello"],
                "working_dir": "/workspace",
                "outputs": [{"path": "result.txt", "max_bytes": 65536}],
                "task_deadline_ms": 30_000,
                "validation_policy": policy,
                "resume_policy": "restart",
            }
        ]
    return {
        "plan_revision": 1,
        "input_manifest_digest": manifest or digest({"project_digest": None, "inputs": []}),
        "project_digest": project,
        "execution_kind": kind,
        "stages": stages,
        "job_deadline_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "retry_policy": {"max_attempts": 3, "backoff_ms": backoff},
    }


@pytest.fixture
def setup(tmp_path, database):
    catalog_path = tmp_path / "catalog.json"
    adapter_path = tmp_path / "adapter.py"
    adapter_path.write_text("""
import json, sys
request = json.load(sys.stdin)
operation = request["operation"]
if operation == "validate_input":
    result = {"valid": True}
elif operation == "split":
    result = {"tasks": [{"item_ids": [str(i)]} for i in range(3)]}
elif operation == "verify":
    result = {"accepted": True, "level": "full"}
else:
    result = {}
json.dump({"result": result}, sys.stdout)
""")
    catalog_path.write_text(
        json.dumps(
            {
                "environments": [dict(id="cpu", name="CPU", **VARIANT)],
                "adapters": [
                    {
                        "ref": ADAPTER,
                        "image_ids": ["cpu"],
                        "policies": ["structural", "full"],
                        "command": [sys.executable, str(adapter_path)],
                        "parallel": True,
                        "checkpoints": True,
                        "checkpoint_files": ["trainer-state.json"],
                        "recipe": {
                            "id": "test",
                            "name": "Test",
                            "description": "Protocol tests",
                            "adapter": ADAPTER,
                        },
                    }
                ],
            }
        )
    )
    return Settings(
        database_url=database,
        storage_dir=tmp_path / "storage",
        catalog_file=catalog_path,
        public_origin="http://127.0.0.1:8000",
        secure_cookies=False,
        lease_ms=1000,
        heartbeat_interval_ms=250,
        long_poll_ms=0,
        verification_seconds=10,
    )


def open_harness(settings, **kwargs):
    app = create_app(settings, enable_background=False, **kwargs)
    with TestClient(app, base_url=settings.public_origin) as client:
        with app.state.runtime.sessions.begin() as db:
            pool = m.Pool(name="Tests")
            db.add(pool)
            db.flush()
            user = m.User(pool_id=pool.id, role="owner", name="Owner")
            db.add(user)
            db.flush()
            _, token, _ = issue_credential(db, user, "cli")
        yield Harness(app, client, {"Authorization": "Bearer " + token})


@pytest.fixture
def cp(setup):
    yield from open_harness(setup)
