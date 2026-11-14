from __future__ import annotations

import math

import pytest

import campus
from conftest import APIServer, Response
from test_client import ready_preview


@pytest.mark.parametrize("state", ["expired", "rejected", "failed", "cancelled"])
@pytest.mark.parametrize("has_revision", [True, False])
def test_terminal_preview_exits_after_one_poll_without_submitting(api, new_client, state, has_revision):
    ready_preview(api)
    preview = {"preview_id": "preview-1", "state": state}
    if has_revision:
        preview["plan_revision"] = 3
    api.respond("GET", "/job-previews/preview-1", preview)
    with pytest.raises(campus.PreviewError) as caught:
        new_client(api).submit({"prompt": "train"}, timeout=0.1)
    assert caught.value.state == state
    assert caught.value.preview_id == "preview-1"
    assert caught.value.plan_revision == (3 if has_revision else None)
    assert len(api.seen("GET", "/job-previews/preview-1")) == 1
    assert not api.seen("POST", "/jobs")


@pytest.mark.parametrize("state", ["future-state", "", None, "succeeded"])
def test_unknown_preview_state_is_rejected_without_default_forever_poll(api, new_client, state):
    ready_preview(api)
    api.respond("GET", "/job-previews/preview-1", {"preview_id": "preview-1", "state": state, "plan_revision": 3})
    with pytest.raises(campus.ProtocolError):
        new_client(api).submit({"prompt": "train"}, timeout=0.1)
    assert len(api.seen("GET", "/job-previews/preview-1")) == 1
    assert not api.seen("POST", "/jobs")


@pytest.mark.parametrize("state", ["pending", "queued", "analyzing", "planning", "preparing"])
def test_recognized_interim_preview_states_continue_to_same_ready_preview(api, new_client, state):
    ready_preview(api)
    states = iter([state, "ready"])
    api.handle("GET", "/job-previews/preview-1", lambda _: Response(body={
        "preview_id": "preview-1", "state": next(states), "plan_revision": 4,
    }))
    handle = new_client(api).submit_preview("preview-1", idempotency_key="ready-key", timeout=1)
    assert handle.plan_revision == 4
    assert not api.seen("POST", "/job-previews")
    assert api.seen("POST", "/jobs")[0].body == {"preview_id": "preview-1", "plan_revision": 4}


def test_explicit_answers_continue_same_preview_and_run_only_new_ready_revision(api, new_client):
    ready_preview(api)
    answered = False
    planning_polls = 0

    def preview(_):
        nonlocal planning_polls
        if not answered:
            return Response(body={
                "preview_id": "preview-1", "state": "needs_input", "plan_revision": 3,
                "questions": [{"question_id": "dataset", "text": "Which dataset?"}, {"question_id": "epochs", "text": "How many epochs?"}],
            })
        planning_polls += 1
        return Response(body={"preview_id": "preview-1", "state": "planning" if planning_polls == 1 else "ready", "plan_revision": 4})

    def answer(_):
        nonlocal answered
        answered = True
        return Response(202, {"preview_id": "preview-1"})

    api.handle("GET", "/job-previews/preview-1", preview)
    api.handle("POST", "/job-previews/preview-1/answers", answer)
    client = new_client(api)
    with pytest.raises(campus.NeedsInputError) as caught:
        client.submit({"prompt": "train"}, idempotency_key="run-key")
    error = caught.value
    answers = [{"question_id": "dataset", "value": "dataset-2"}, {"question_id": "epochs", "value": 2}]
    assert not api.seen("POST", "/jobs")
    assert client.answer_preview(error.preview_id, error.plan_revision, answers) == {"preview_id": "preview-1"}
    handle = client.submit_preview(error.preview_id, idempotency_key="run-key", timeout=1)

    assert (handle.job_id, handle.preview_id, handle.plan_revision) == ("job-1", "preview-1", 4)
    assert len(api.seen("POST", "/job-previews")) == 1
    mutation, = api.seen("POST", "/job-previews/preview-1/answers")
    assert mutation.body == {"plan_revision": 3, "answers": answers}
    run, = api.seen("POST", "/jobs")
    assert run.body == {"preview_id": "preview-1", "plan_revision": 4}
    assert run.headers["Idempotency-Key"] == "run-key"


@pytest.mark.parametrize("receipt,status", [({}, 204), ({}, 202), ({"preview_id": "preview-1", "state": "planning", "plan_revision": "next"}, 202)])
def test_answer_receipts_may_be_minimal_and_never_submit_a_job(api, new_client, receipt, status):
    api.respond("POST", "/job-previews/preview-1/answers", receipt, status=status)
    result = new_client(api).answer_preview("preview-1", "current", [{"question_id": "dataset", "value": None}])
    assert result == receipt
    assert not api.seen("POST", "/job-previews")
    assert not api.seen("POST", "/jobs")


def test_answer_transient_retry_preserves_exact_body_and_caller_key(api, new_client):
    calls = 0

    def answer(_):
        nonlocal calls
        calls += 1
        return Response(503, {"code": "UNAVAILABLE"}) if calls == 1 else Response(202, {"preview_id": "preview-1"})

    api.handle("POST", "/job-previews/preview-1/answers", answer)
    new_client(api).answer_preview("preview-1", 3, [{"question_id": "config", "value": {"epochs": 2, "enabled": True}}], idempotency_key="answer-key")
    attempts = api.seen("POST", "/job-previews/preview-1/answers")
    assert len(attempts) == 2
    assert {request.headers["Idempotency-Key"] for request in attempts} == {"answer-key"}
    assert [request.body for request in attempts] == [{"plan_revision": 3, "answers": [{"question_id": "config", "value": {"epochs": 2, "enabled": True}}]}] * 2


def test_unknown_answer_ack_can_replay_same_batch_with_same_derived_key_after_reconnect(api, new_client):
    answers = [{"question_id": "dataset", "value": "chosen dataset"}]
    api.handle("POST", "/job-previews/preview-1/answers", lambda _: Response(disconnect=True))
    with pytest.raises(campus.TransportError):
        new_client(api, max_attempts=1).answer_preview("preview-1", 3, answers)
    api.respond("POST", "/job-previews/preview-1/answers", {"preview_id": "preview-1"}, status=202)
    new_client(api).answer_preview("preview-1", 3, answers)
    attempts = api.seen("POST", "/job-previews/preview-1/answers")
    assert len(attempts) == 2
    assert attempts[0].headers["Idempotency-Key"] == attempts[1].headers["Idempotency-Key"]
    assert attempts[0].headers["Idempotency-Key"].startswith("answers:")
    assert attempts[0].body == attempts[1].body


@pytest.mark.parametrize("answers", [
    [], {}, [{"question_id": "missing-value"}], [{"value": "missing-question"}],
    [{"question_id": "../unsafe", "value": "x"}], [{"question_id": "x" * 129, "value": "x"}],
    [{"question_id": "q", "value": 1}, {"question_id": "q", "value": 2}],
    [{"question_id": "q", "value": 1, "invented": True}],
    [{"question_id": f"q-{i}", "value": i} for i in range(101)],
    [{"question_id": "q", "value": "x" * 65536}],
    [{"question_id": "q", "value": math.nan}], [{"question_id": "q", "value": math.inf}],
    [{"question_id": "q", "value": {1: "ambiguous key"}}], [{"question_id": "q", "value": {1, 2}}],
])
def test_answers_are_bounded_validated_locally_and_never_invented(api, new_client, answers):
    with pytest.raises(ValueError):
        new_client(api).answer_preview("preview-1", 3, answers)
    assert api.requests == []


def test_answer_json_nesting_is_bounded_before_network(api, new_client):
    value = "deep"
    for _ in range(17):
        value = [value]
    with pytest.raises(ValueError):
        new_client(api).answer_preview("preview-1", 3, [{"question_id": "q", "value": value}])
    assert api.requests == []


@pytest.mark.parametrize("status,code,exception", [(401, "UNAUTHORIZED", campus.AuthenticationError), (403, "FORBIDDEN", campus.AuthenticationError), (503, "STALE_ATTEMPT", campus.StaleAttemptError)])
def test_answer_auth_and_fencing_errors_are_terminal(api, new_client, status, code, exception):
    api.respond("POST", "/job-previews/preview-1/answers", {"code": code, "retryable": True}, status=status)
    with pytest.raises(exception):
        new_client(api).answer_preview("preview-1", 3, [{"question_id": "q", "value": 1}])
    assert len(api.seen("POST", "/job-previews/preview-1/answers")) == 1
    assert not api.seen("POST", "/jobs")


def test_mismatched_answer_receipt_cannot_attach_another_preview(api, new_client):
    api.respond("POST", "/job-previews/preview-1/answers", {"preview_id": "other"}, status=202)
    with pytest.raises(campus.ProtocolError):
        new_client(api).answer_preview("preview-1", 3, [{"question_id": "q", "value": 1}])
    assert not api.seen("POST", "/jobs")


def test_unknown_job_ack_resume_keeps_frozen_revision_even_if_preview_advances(api, new_client):
    ready_preview(api)
    api.handle("POST", "/jobs", lambda _: Response(disconnect=True))
    api.respond("GET", "/jobs", {"code": "NOT_FOUND"}, status=404)
    with pytest.raises(campus.SubmissionUnknownError) as caught:
        new_client(api, max_attempts=1).submit({"prompt": "train"}, idempotency_key="run-key")
    api.respond("GET", "/job-previews/preview-1", {"preview_id": "preview-1", "state": "ready", "plan_revision": 4})
    api.respond("POST", "/jobs", {"job_id": "job-1"}, status=201)
    error = caught.value
    new_client(api).resume_submission(error.preview_id, error.plan_revision, error.idempotency_key)
    assert len(api.seen("GET", "/job-previews/preview-1")) == 1
    assert [request.body for request in api.seen("POST", "/jobs")] == [{"preview_id": "preview-1", "plan_revision": 3}] * 2
    assert not api.seen("POST", "/job-previews/preview-1/answers")


@pytest.mark.parametrize("records,expected", [
    ([{"text": "training", "sequence": 1}], [{"text": "training", "sequence": 1}]),
    ([{"message": "legacy", "sequence": 2}], [{"text": "legacy", "sequence": 2}]),
    (["legacy string"], [{"text": "legacy string"}]),
    ([{"text": "canonical", "message": "old"}], [{"text": "canonical"}]),
])
def test_log_records_use_one_canonical_text_dto(api, new_client, records, expected):
    api.respond("GET", "/jobs/job-1/logs", {"records": records, "next_cursor": "next"})
    assert new_client(api).logs("job-1") == {"records": expected, "next_cursor": "next"}


@pytest.mark.parametrize("record", [{}, {"text": 1}, 1, None])
def test_malformed_log_records_are_rejected(api, new_client, record):
    api.respond("GET", "/jobs/job-1/logs", {"records": [record], "next_cursor": None})
    with pytest.raises(campus.ProtocolError):
        new_client(api).logs("job-1")


@pytest.mark.parametrize("error_type", [BrokenPipeError, ConnectionResetError])
def test_fixture_only_suppresses_expected_peer_disconnect_noise(new_client, capsys, error_type):
    server = APIServer()

    def disconnected(_):
        raise error_type("expected peer disconnect")

    server.handle("GET", "/jobs/job-1", disconnected)
    try:
        with pytest.raises(campus.TransportError):
            new_client(server, max_attempts=1).wait("job-1")
    finally:
        server.close()
    assert capsys.readouterr().err == ""


def test_fixture_does_not_hide_handler_assertions(new_client):
    server = APIServer()
    failure = AssertionError("fixture handler assertion must fail the test")

    def broken_assertion(_):
        raise failure

    server.handle("GET", "/jobs/job-1", broken_assertion)
    try:
        with pytest.raises(campus.TransportError):
            new_client(server, max_attempts=1).wait("job-1")
        with pytest.raises(ExceptionGroup) as caught:
            server.close()
        assert caught.value.exceptions == (failure,)
    finally:
        server.close()
