"""HTTP boundary regressions against unmodified published foundation fixtures.

The response data and Pydantic DTOs come from the pinned upstream commit, not
the runner's old local assumptions. No production coordinator is implied.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import campus
from campus.generated import api as dto
from conftest import Response

ROOT = Path(__file__).parent / "fixtures/foundation"
PREVIEW = "00000000-0000-4000-8000-000000000004"
JOB = "00000000-0000-4000-8000-000000000002"


def test_contract_snapshots_retain_published_source_hashes():
    source = json.loads((ROOT / "SOURCE.json").read_text(encoding="utf-8"))
    for name, digest in source["sha256"].items():
        if name == "contracts/openapi-v1.yaml":
            path = ROOT / "openapi-v1.json"
        elif name.startswith("contracts/fixtures/"):
            path = ROOT / Path(name).name
        else:
            continue
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name


def test_generated_optional_dtos_validate_all_published_scenarios():
    for path in ROOT.glob("*.json"):
        if path.name in {"SOURCE.json", "openapi-v1.json"}:
            continue
        responses = json.loads(path.read_text(encoding="utf-8"))["responses"]
        for response in responses.values():
            if response["schema"] is not None:
                model = getattr(dto, response["schema"])
                model.model_validate_json(json.dumps(response["body"]))


def scenario(api, name="ready"):
    responses = json.loads((ROOT / f"{name}.json").read_text(encoding="utf-8"))["responses"]
    for operation, method, path in [
        ("protocol", "GET", "/protocol"),
        ("create_preview", "POST", "/job-previews"),
        ("get_preview", "GET", "/job-previews/" + PREVIEW),
        ("answer_preview", "POST", "/job-previews/" + PREVIEW + "/answers"),
        ("create_job", "POST", "/jobs"),
        ("get_job", "GET", "/jobs/" + JOB),
        ("cancel_job", "POST", "/jobs/" + JOB + "/cancel"),
        ("job_logs", "GET", "/jobs/" + JOB + "/logs"),
    ]:
        reply = responses[operation]
        api.respond(method, path, reply["body"], status=reply["status"])
    return responses


def test_submit_reads_foundation_defaults_and_exact_preview_job_shapes(api, new_client):
    responses = scenario(api)
    handle = new_client(api).submit({"prompt": "Run selected project"}, idempotency_key="run-key")
    assert (handle.preview_id, handle.job_id, handle.plan_revision) == (PREVIEW, JOB, 1)
    preview, = api.seen("POST", "/job-previews")
    dto.PreviewRequest.model_validate_json(json.dumps(preview.body))
    job, = api.seen("POST", "/jobs")
    dto.JobCreate.model_validate_json(json.dumps(job.body))
    assert job.body == {"preview_id": PREVIEW, "plan_revision": 1}
    assert job.headers["Idempotency-Key"] == "run-key"
    assert job.headers["Authorization"] == "Bearer test-token"
    assert not any(name.lower() in {"x-node-session", "x-csrf-token"} for name in job.headers)
    assert handle.wait() == responses["get_job"]["body"]


def test_foundation_singular_question_and_answer_preserve_owner_text(api, new_client):
    responses = scenario(api, "needs_input")
    client = new_client(api)
    with pytest.raises(campus.NeedsInputError) as caught:
        client.submit({"prompt": "Run selected project"})
    error = caught.value
    assert error.questions == [responses["get_preview"]["body"]["question"]]
    answer = "selected/data.jsonl"
    receipt = client.answer_preview(PREVIEW, 1, [{"question_id": "dataset", "value": answer}])
    assert receipt == responses["answer_preview"]["body"]
    mutation, = api.seen("POST", "/job-previews/" + PREVIEW + "/answers")
    assert mutation.body == {"question_id": "dataset", "plan_revision": 1, "answer": answer}
    dto.PreviewAnswer.model_validate_json(json.dumps(mutation.body))
    assert not api.seen("POST", "/jobs")


def test_foundation_log_cursor_and_cancel_request(api, new_client):
    responses = scenario(api)
    client = new_client(api)
    assert client.logs(JOB, cursor=7) == responses["job_logs"]["body"]
    request, = api.seen("GET", "/jobs/" + JOB + "/logs")
    assert request.query == {"cursor": ["7"]}
    assert client.cancel(JOB) == responses["cancel_job"]["body"]
    cancel, = api.seen("POST", "/jobs/" + JOB + "/cancel")
    assert cancel.body is None


def test_foundation_artifact_names_and_sha256_publish_verified_output(api, new_client, tmp_path):
    responses = scenario(api)
    job = responses["get_job"]["body"]
    data = b"verified foundation result"
    artifact = job["artifacts"][0]
    artifact.update(name="nested/vectors.json", size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    dto.Job.model_validate_json(json.dumps(job))
    api.respond("GET", "/jobs/" + JOB, job)
    api.respond("GET", "/artifacts/" + artifact["id"] + "/content", data)
    path = new_client(api).download(JOB, tmp_path / "results")
    assert (path / "nested/vectors.json").read_bytes() == data


def test_unknown_job_ack_replays_exact_idempotent_post_without_unsupported_list_query(api, new_client):
    responses = scenario(api)
    calls = 0

    def create(request):
        nonlocal calls
        calls += 1
        dto.JobCreate.model_validate_json(json.dumps(request.body))
        return Response(disconnect=True) if calls == 1 else Response(201, responses["create_job"]["body"])

    api.handle("POST", "/jobs", create)
    handle = new_client(api).submit({"prompt": "Run selected project"}, idempotency_key="run-key")
    assert handle.job_id == JOB
    requests = api.seen("POST", "/jobs")
    assert [r.body for r in requests] == [{"preview_id": PREVIEW, "plan_revision": 1}] * 2
    assert [r.headers["Idempotency-Key"] for r in requests] == ["run-key"] * 2
    assert not api.seen("GET", "/jobs")


@pytest.mark.parametrize("operation", ["create_preview", "answer_preview"])
def test_non_idempotent_foundation_mutations_never_automatically_retry_unknown_ack(api, new_client, operation):
    scenario(api)
    route = "/job-previews" if operation == "create_preview" else "/job-previews/" + PREVIEW + "/answers"
    api.handle("POST", route, lambda _: Response(disconnect=True))
    with pytest.raises(campus.TransportError):
        if operation == "create_preview":
            new_client(api).submit({"prompt": "Run selected project"})
        else:
            new_client(api).answer_preview(PREVIEW, 1, [{"question_id": "dataset", "value": "data.jsonl"}])
    assert len(api.seen("POST", route)) == 1
    assert not api.seen("POST", "/jobs")


@pytest.mark.parametrize("operation", ["get", "cancel", "submit"])
def test_malformed_job_state_never_escapes_as_type_error_or_attaches_job(api, new_client, operation):
    responses = scenario(api)
    job = {**responses["get_job"]["body"], "state": []}
    method, route = {"get": ("GET", "/jobs/" + JOB), "cancel": ("POST", "/jobs/" + JOB + "/cancel"), "submit": ("POST", "/jobs")}[operation]
    api.respond(method, route, job)
    client = new_client(api)
    with pytest.raises(campus.SubmissionUnknownError if operation == "submit" else campus.ProtocolError):
        if operation == "get":
            client.get_job(JOB)
        elif operation == "cancel":
            client.cancel(JOB)
        else:
            client.submit({"prompt": "Run selected project"}, idempotency_key="run-key")
    assert len(api.seen(method, route)) == 1


def test_partial_foundation_job_is_terminal_and_never_publishes_outputs(api, new_client, tmp_path):
    scenario(api, "partial")
    client = new_client(api)
    with pytest.raises(campus.JobFailedError) as caught:
        client.wait(JOB)
    assert caught.value.state == "partial"
    with pytest.raises(campus.JobFailedError):
        client.download(JOB, tmp_path / "results")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("cursor", [-1, True, "12", None, 1 << 63])
def test_log_cursor_uses_foundation_integer_bounds_before_network(api, new_client, cursor):
    with pytest.raises(ValueError):
        new_client(api).logs(JOB, cursor=cursor)
    assert api.requests == []


@pytest.mark.parametrize("key", ["x" * 129, "control\n", "non-ascii-雪"])
def test_job_idempotency_key_uses_foundation_bounds_before_network(api, new_client, key):
    with pytest.raises(ValueError):
        new_client(api).submit({"prompt": "Run selected project"}, idempotency_key=key)
    assert api.requests == []


def test_legacy_preview_execution_fields_are_rejected_before_network(api, new_client):
    with pytest.raises(ValueError):
        new_client(api).submit({"prompt": "Run selected project", "resources": {"gpu": True}})
    assert api.requests == []
