from __future__ import annotations

import pytest
import time

import campus
from conftest import APIServer, Response
from test_client import ready_preview


@pytest.mark.parametrize("endpoint", [
    "http://example.com", "ftp://127.0.0.1", "https://user:password@example.com",
    "https://example.com?token=secret", "https://example.com/#fragment", "https://example.com/prefix",
    "https://example.com:bad", "https://", "http://127.0.0.1.evil.example", "https://example.com/../api/v1",
])
def test_rejects_insecure_or_ambiguous_coordinator_endpoint(endpoint):
    with pytest.raises(ValueError):
        campus.Client(endpoint, "secret")


@pytest.mark.parametrize("credential", ["secret\r\nInjected: yes", "secret\x00", "secret\n"])
def test_credentials_cannot_inject_headers(credential):
    with pytest.raises(ValueError):
        campus.Client("https://example.com", credential)


@pytest.mark.parametrize("status,code,exception", [
    (401, "UNAUTHORIZED", "AuthenticationError"), (403, "FORBIDDEN", "AuthenticationError"),
    (503, "STALE_ATTEMPT", "StaleAttemptError"), (503, "LEASE_EXPIRED", "StaleAttemptError"),
    (503, "SESSION_REPLACED", "StaleAttemptError"), (400, "BAD_REQUEST", "APIError"),
])
def test_auth_fencing_and_bad_requests_are_terminal_and_redacted(api, new_client, status, code, exception):
    api.respond("GET", "/jobs/job-1", {"code": code, "message": "echo test-token", "retryable": True}, status=status)
    with pytest.raises(getattr(campus, exception)) as caught:
        new_client(api).wait("job-1")
    assert len(api.seen("GET", "/jobs/job-1")) == 1
    assert "test-token" not in str(caught.value)
    assert caught.value.status == status
    assert caught.value.code == code


def test_transient_retries_are_bounded(api, new_client):
    api.respond("GET", "/jobs/job-1", {"code": "UNAVAILABLE"}, status=503)
    with pytest.raises(campus.APIError):
        new_client(api, max_attempts=3).wait("job-1")
    assert len(api.seen("GET", "/jobs/job-1")) == 3


def test_transient_failure_can_recover_without_repeating_handshake(api, new_client):
    calls = 0

    def job(_):
        nonlocal calls
        calls += 1
        return Response(503, {"code": "UNAVAILABLE"}) if calls == 1 else Response(body={"job_id": "job-1", "state": "succeeded"})

    api.handle("GET", "/jobs/job-1", job)
    assert new_client(api).wait("job-1")["state"] == "succeeded"
    assert len(api.seen("GET", "/protocol")) == 1


def test_redirect_never_forwards_credentials_to_another_origin(api, new_client):
    other = APIServer()
    try:
        api.respond("GET", "/jobs/job-1", {}, status=307, headers={"Location": other.url + "/api/v1/jobs/job-1"})
        with pytest.raises(campus.APIError):
            new_client(api).wait("job-1")
        assert other.requests == []
        assert len(api.seen("GET", "/jobs/job-1")) == 1
    finally:
        other.close()


def test_lost_submission_ack_reconciles_existing_job_without_resubmission(api, new_client):
    ready_preview(api)
    api.handle("POST", "/jobs", lambda _: Response(disconnect=True))
    api.respond("GET", "/jobs", {"jobs": [{"job_id": "job-1", "state": "queued", "idempotency_key": "stable-key"}]})
    handle = new_client(api).submit({"prompt": "train"}, idempotency_key="stable-key")
    assert handle.job_id == "job-1"
    assert len(api.seen("POST", "/jobs")) == 1
    assert api.seen("GET", "/jobs")[0].query == {"idempotency_key": ["stable-key"]}


def test_unsupported_receipt_lookup_retries_only_original_post_and_key(api, new_client):
    ready_preview(api)
    calls = 0

    def run(_):
        nonlocal calls
        calls += 1
        return Response(disconnect=True) if calls == 1 else Response(body={"job_id": "job-1", "state": "queued"})

    api.handle("POST", "/jobs", run)
    api.respond("GET", "/jobs", {"code": "NOT_FOUND"}, status=404)
    assert new_client(api).submit({"prompt": "train"}, idempotency_key="stable-key").job_id == "job-1"
    attempts = api.seen("POST", "/jobs")
    assert len(attempts) == 2
    assert {r.headers["Idempotency-Key"] for r in attempts} == {"stable-key"}
    assert [r.body for r in attempts] == [{"preview_id": "preview-1", "plan_revision": 3}] * 2
    assert len(api.seen("POST", "/job-previews")) == 1


def test_unknown_ack_exposes_resume_coordinates_and_resume_never_replans(api, new_client):
    ready_preview(api)
    api.handle("POST", "/jobs", lambda _: Response(disconnect=True))
    api.respond("GET", "/jobs", {"code": "NOT_FOUND"}, status=404)
    with pytest.raises(campus.SubmissionUnknownError) as caught:
        new_client(api, max_attempts=2).submit({"prompt": "train"}, idempotency_key="stable-key")
    error = caught.value
    assert (error.preview_id, error.plan_revision, error.idempotency_key) == ("preview-1", 3, "stable-key")
    api.respond("GET", "/jobs", {"jobs": [{"job_id": "job-1", "state": "queued", "idempotency_key": "stable-key"}]})
    handle = new_client(api).resume_submission(error.preview_id, error.plan_revision, error.idempotency_key)
    assert handle.job_id == "job-1"
    assert len(api.seen("POST", "/job-previews")) == 1
    assert len(api.seen("POST", "/jobs")) == 2


def test_auth_failure_during_receipt_lookup_stops_submission_retry(api, new_client):
    ready_preview(api)
    api.handle("POST", "/jobs", lambda _: Response(disconnect=True))
    api.respond("GET", "/jobs", {"code": "UNAUTHORIZED"}, status=401)
    with pytest.raises(campus.AuthenticationError):
        new_client(api).submit({"prompt": "train"})
    assert len(api.seen("POST", "/jobs")) == 1


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"job_id":"job-1","state":"succeeded","padding":"' + b"a" * 200 + b'"}'])
def test_json_responses_are_bounded_objects(api, new_client, body):
    api.respond("GET", "/jobs/job-1", body)
    with pytest.raises(campus.ProtocolError):
        new_client(api, max_json_bytes=128).wait("job-1")
    assert len(api.seen("GET", "/jobs/job-1")) == 1


@pytest.mark.parametrize("job_id", ["../secret", "job/other", "job?query=1", "", "job\n"])
def test_job_identity_cannot_change_routes(api, new_client, job_id):
    with pytest.raises(ValueError):
        new_client(api).wait(job_id)
    assert api.requests == []


@pytest.mark.parametrize("update", [
    {"preview_id": "another-preview"}, {"plan_revision": None}, {"plan_revision": True}, {"plan_revision": -1},
])
def test_malformed_preview_cannot_be_run(api, new_client, update):
    ready_preview(api)
    preview = {"preview_id": "preview-1", "state": "ready", "plan_revision": 3, **update}
    api.respond("GET", "/job-previews/preview-1", preview)
    with pytest.raises(campus.ProtocolError):
        new_client(api).submit({"prompt": "train"})
    assert not api.seen("POST", "/jobs")


def test_questions_are_not_silently_ignored_even_in_ready_preview(api, new_client):
    ready_preview(api)
    api.respond("GET", "/job-previews/preview-1", {
        "preview_id": "preview-1", "state": "ready", "plan_revision": 3,
        "questions": [{"question_id": "dataset", "text": "Which dataset?"}],
    })
    with pytest.raises(campus.NeedsInputError):
        new_client(api).submit({"prompt": "train"})
    assert not api.seen("POST", "/jobs")


@pytest.mark.parametrize("body", [
    b'{"job_id":"job-1","state":"running","state":"succeeded"}',
    b'{"job_id":"job-1","state":"succeeded","metric":NaN}',
    b'{"job_id":"job-1","state":"succeeded","extra":' + b"[" * 1200 + b"0" + b"]" * 1200 + b"}",
])
def test_ambiguous_nonstandard_or_excessively_nested_json_is_rejected(api, new_client, body):
    api.respond("GET", "/jobs/job-1", body)
    with pytest.raises(campus.ProtocolError):
        new_client(api).wait("job-1")


def test_malformed_optional_lookup_never_loses_submission_resume_coordinates(api, new_client):
    ready_preview(api)
    api.handle("POST", "/jobs", lambda _: Response(disconnect=True))
    api.respond("GET", "/jobs", b"not-json")
    with pytest.raises(campus.SubmissionUnknownError) as caught:
        new_client(api, max_attempts=1).submit({"prompt": "train"}, idempotency_key="stable-key")
    assert caught.value.idempotency_key == "stable-key"


def test_unfiltered_lookup_cannot_attach_an_unrelated_job(api, new_client):
    ready_preview(api)
    calls = 0

    def run(_):
        nonlocal calls
        calls += 1
        return Response(disconnect=True) if calls == 1 else Response(body={"job_id": "job-1", "state": "queued"})

    api.handle("POST", "/jobs", run)
    api.respond("GET", "/jobs", {"jobs": [{"job_id": "unrelated-job", "state": "queued"}]})
    assert new_client(api).submit({"prompt": "train"}, idempotency_key="stable-key").job_id == "job-1"
    assert calls == 2


def test_cancel_rejects_receipt_for_a_different_job(api, new_client):
    api.respond("POST", "/jobs/job-1/cancel", {"job_id": "wrong-job", "state": "cancelled"})
    with pytest.raises(campus.ProtocolError):
        new_client(api).cancel("job-1")


def test_observation_timeout_bounds_a_stalled_job_request(api, new_client):
    def slow_job(_):
        time.sleep(1)
        return Response(body={"job_id": "job-1", "state": "succeeded"})

    api.handle("GET", "/jobs/job-1", slow_job)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        new_client(api).wait("job-1", timeout=0.1)
    assert time.monotonic() - started < 0.8
