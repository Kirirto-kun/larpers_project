from __future__ import annotations

import math

import pytest

import campus
from conftest import APIServer, Response
from test_client import ready_preview

PREVIEW = "00000000-0000-4000-8000-000000000004"
JOB = "00000000-0000-4000-8000-000000000002"
ATTEMPT = "00000000-0000-4000-8000-000000000009"


@pytest.mark.parametrize("state", ["failed", "cancelled"])
@pytest.mark.parametrize("has_revision", [True, False])
def test_terminal_preview_exits_after_one_poll_without_submitting(api, new_client, state, has_revision):
    ready_preview(api)
    preview = {"id": PREVIEW, "state": state}
    if has_revision:
        preview["plan_revision"] = 3
    api.respond("GET", "/job-previews/" + PREVIEW, preview)
    with pytest.raises(campus.PreviewError) as caught:
        new_client(api).submit({"prompt": "train"}, timeout=0.1)
    assert caught.value.state == state
    assert caught.value.preview_id == PREVIEW
    assert caught.value.plan_revision == (3 if has_revision else None)
    assert len(api.seen("GET", "/job-previews/" + PREVIEW)) == 1
    assert not api.seen("POST", "/jobs")


@pytest.mark.parametrize("state", ["future-state", "", None, "succeeded", "expired", "rejected", "pending", "queued", "analyzing", "preparing"])
def test_unknown_preview_state_is_rejected_without_forever_poll(api, new_client, state):
    ready_preview(api)
    api.respond("GET", "/job-previews/" + PREVIEW, {"id": PREVIEW, "state": state, "plan_revision": 3})
    with pytest.raises(campus.ProtocolError):
        new_client(api).submit({"prompt": "train"}, timeout=0.1)
    assert len(api.seen("GET", "/job-previews/" + PREVIEW)) == 1
    assert not api.seen("POST", "/jobs")


@pytest.mark.parametrize("state", ["inspecting", "planning", "validating"])
def test_foundation_interim_states_continue_to_same_ready_preview(api, new_client, state):
    ready_preview(api)
    states = iter([state, "ready"])
    api.handle("GET", "/job-previews/" + PREVIEW, lambda _: Response(body={
        "id": PREVIEW, "state": next(states), "plan_revision": 4,
    }))
    handle = new_client(api).submit_preview(PREVIEW, idempotency_key="ready-key", timeout=1)
    assert handle.plan_revision == 4
    assert not api.seen("POST", "/job-previews")
    assert api.seen("POST", "/jobs")[0].body == {"preview_id": PREVIEW, "plan_revision": 4}



def test_explicit_answer_continues_same_preview_and_runs_new_ready_revision(api, new_client):
    ready_preview(api)
    answered = False
    planning_polls = 0

    def preview(_):
        nonlocal planning_polls
        if not answered:
            return Response(body={
                "id": PREVIEW, "state": "needs_input", "plan_revision": 3,
                "question": {"question_id": "dataset", "prompt": "Which dataset?"},
            })
        planning_polls += 1
        return Response(body={"id": PREVIEW, "state": "planning" if planning_polls == 1 else "ready", "plan_revision": 4})

    def answer(_):
        nonlocal answered
        answered = True
        return Response(202, {"id": PREVIEW, "state": "planning", "plan_revision": 4})

    api.handle("GET", "/job-previews/" + PREVIEW, preview)
    api.handle("POST", "/job-previews/" + PREVIEW + "/answers", answer)
    client = new_client(api)
    with pytest.raises(campus.NeedsInputError) as caught:
        client.submit({"prompt": "train"}, idempotency_key="run-key")
    error = caught.value
    client.answer_preview(error.preview_id, error.plan_revision, [{"question_id": "dataset", "value": "dataset-2"}])
    handle = client.submit_preview(error.preview_id, idempotency_key="run-key", timeout=1)
    assert (handle.job_id, handle.preview_id, handle.plan_revision) == (JOB, PREVIEW, 4)
    assert len(api.seen("POST", "/job-previews")) == 1
    mutation, = api.seen("POST", "/job-previews/" + PREVIEW + "/answers")
    assert mutation.body == {"plan_revision": 3, "question_id": "dataset", "answer": "dataset-2"}
    run, = api.seen("POST", "/jobs")
    assert run.body == {"preview_id": PREVIEW, "plan_revision": 4}
    assert run.headers["Idempotency-Key"] == "run-key"


@pytest.mark.parametrize("receipt,status", [({}, 204), ({}, 202), ({"id": "other"}, 202)])
def test_answer_requires_receipt_for_same_preview_and_never_submits(api, new_client, receipt, status):
    api.respond("POST", "/job-previews/" + PREVIEW + "/answers", receipt, status=status)
    with pytest.raises(campus.ProtocolError):
        new_client(api).answer_preview(PREVIEW, 3, [{"question_id": "dataset", "value": "selected"}])
    assert not api.seen("POST", "/job-previews")
    assert not api.seen("POST", "/jobs")


def test_answer_transient_error_is_not_retried_without_contract_idempotency(api, new_client):
    api.respond("POST", "/job-previews/" + PREVIEW + "/answers", {"code": "UNAVAILABLE"}, status=503)
    with pytest.raises(campus.APIError):
        new_client(api).answer_preview(PREVIEW, 3, [{"question_id": "dataset", "value": "selected"}])
    assert len(api.seen("POST", "/job-previews/" + PREVIEW + "/answers")) == 1


def test_answer_cannot_claim_unsupported_idempotency_before_network(api, new_client):
    with pytest.raises(ValueError):
        new_client(api).answer_preview(PREVIEW, 3, [{"question_id": "dataset", "value": "selected"}], idempotency_key="key")
    assert api.requests == []


@pytest.mark.parametrize("answers", [
    [], {}, [{"question_id": "missing-value"}], [{"value": "missing-question"}],
    [{"question_id": "../unsafe", "value": "x"}], [{"question_id": "x" * 65, "value": "x"}],
    [{"question_id": "q", "value": "one"}, {"question_id": "q2", "value": "two"}],
    [{"question_id": "q", "value": "x", "invented": True}],
    [{"question_id": "q", "value": "x" * 2001}], [{"question_id": "q", "value": ""}],
    [{"question_id": "q", "value": math.nan}], [{"question_id": "q", "value": math.inf}],
    [{"question_id": "q", "value": {1: "ambiguous key"}}], [{"question_id": "q", "value": {1, 2}}],
    [{"question_id": "q", "value": None}], [{"question_id": "q", "value": 3}],
])
def test_answers_are_one_bounded_explicit_text_and_never_invented(api, new_client, answers):
    with pytest.raises(ValueError):
        new_client(api).answer_preview(PREVIEW, 3, answers)
    assert api.requests == []


@pytest.mark.parametrize("revision", [0, -1, True, "3", None])
def test_answer_revision_must_be_positive_integer_before_network(api, new_client, revision):
    with pytest.raises(ValueError):
        new_client(api).answer_preview(PREVIEW, revision, [{"question_id": "q", "value": "selected"}])
    assert api.requests == []


@pytest.mark.parametrize("status,code,exception", [(401, "UNAUTHORIZED", campus.AuthenticationError), (403, "FORBIDDEN", campus.AuthenticationError), (503, "STALE_ATTEMPT", campus.StaleAttemptError)])
def test_answer_auth_and_fencing_errors_are_terminal(api, new_client, status, code, exception):
    api.respond("POST", "/job-previews/" + PREVIEW + "/answers", {"code": code, "retryable": True}, status=status)
    with pytest.raises(exception):
        new_client(api).answer_preview(PREVIEW, 3, [{"question_id": "q", "value": "selected"}])
    assert len(api.seen("POST", "/job-previews/" + PREVIEW + "/answers")) == 1
    assert not api.seen("POST", "/jobs")


def test_unknown_job_ack_resume_keeps_frozen_revision_if_preview_advances(api, new_client):
    ready_preview(api)
    api.handle("POST", "/jobs", lambda _: Response(disconnect=True))
    with pytest.raises(campus.SubmissionUnknownError) as caught:
        new_client(api, max_attempts=1).submit({"prompt": "train"}, idempotency_key="run-key")
    api.respond("GET", "/job-previews/" + PREVIEW, {"id": PREVIEW, "state": "ready", "plan_revision": 4})
    api.respond("POST", "/jobs", {"id": JOB, "state": "queued"}, status=201)
    error = caught.value
    new_client(api).resume_submission(error.preview_id, error.plan_revision, error.idempotency_key)
    assert len(api.seen("GET", "/job-previews/" + PREVIEW)) == 1
    assert [request.body for request in api.seen("POST", "/jobs")] == [{"preview_id": PREVIEW, "plan_revision": 3}] * 2
    assert not api.seen("POST", "/job-previews/" + PREVIEW + "/answers")


def test_log_entries_preserve_foundation_metadata_and_text(api, new_client):
    entry = {"cursor": 7, "attempt_id": ATTEMPT, "sequence": 3, "occurred_at": "2026-11-14T06:00:00Z", "text": "training", "gap": True}
    page = {"entries": [entry], "cursor": 7, "truncated": True}
    api.respond("GET", "/jobs/" + JOB + "/logs", page)
    assert new_client(api).logs(JOB) == page


@pytest.mark.parametrize("record", [{}, {"text": 1}, 1, None, "legacy string", {"message": "legacy"}])
def test_legacy_or_malformed_log_records_are_rejected(api, new_client, record):
    api.respond("GET", "/jobs/" + JOB + "/logs", {"entries": [record], "cursor": 1, "truncated": False})
    with pytest.raises(campus.ProtocolError):
        new_client(api).logs(JOB)


@pytest.mark.parametrize("error_type", [BrokenPipeError, ConnectionResetError])
def test_fixture_only_suppresses_expected_peer_disconnect_noise(new_client, capsys, error_type):
    server = APIServer()

    def disconnected(_):
        raise error_type("expected peer disconnect")

    server.handle("GET", "/jobs/00000000-0000-4000-8000-000000000002", disconnected)
    try:
        with pytest.raises(campus.TransportError):
            new_client(server, max_attempts=1).wait("00000000-0000-4000-8000-000000000002")
    finally:
        server.close()
    assert capsys.readouterr().err == ""


def test_fixture_does_not_hide_handler_assertions(new_client):
    server = APIServer()
    failure = AssertionError("fixture handler assertion must fail the test")

    def broken_assertion(_):
        raise failure

    server.handle("GET", "/jobs/00000000-0000-4000-8000-000000000002", broken_assertion)
    try:
        with pytest.raises(campus.TransportError):
            new_client(server, max_attempts=1).wait("00000000-0000-4000-8000-000000000002")
        with pytest.raises(ExceptionGroup) as caught:
            server.close()
        assert caught.value.exceptions == (failure,)
    finally:
        server.close()
