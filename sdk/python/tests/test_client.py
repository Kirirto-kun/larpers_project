from __future__ import annotations

import pytest

import campus
from conftest import Response


def ready_preview(api):
    api.respond("POST", "/job-previews", {"id": "00000000-0000-4000-8000-000000000004"}, status=202)
    api.respond("GET", "/job-previews/00000000-0000-4000-8000-000000000004", {"id": "00000000-0000-4000-8000-000000000004", "state": "ready", "plan_revision": 3})
    api.respond("POST", "/jobs", {"id": "00000000-0000-4000-8000-000000000002", "state": "queued"}, status=201)


def test_submit_runs_exact_ready_preview_and_reconnect_only_observes(api, new_client):
    ready_preview(api)
    request = {"prompt": "train on my selected dataset", "accelerator": "cuda"}
    client = new_client(api)
    handle = client.submit(request, idempotency_key="submit-key")

    assert isinstance(handle, campus.JobHandle)
    assert (handle.job_id, handle.preview_id, handle.plan_revision) == ("00000000-0000-4000-8000-000000000002", "00000000-0000-4000-8000-000000000004", 3)
    assert api.seen("POST", "/job-previews")[0].body == request
    run = api.seen("POST", "/jobs")[0]
    assert run.body == {"preview_id": "00000000-0000-4000-8000-000000000004", "plan_revision": 3}
    assert run.headers["Idempotency-Key"] == "submit-key"
    assert run.headers["Authorization"] == "Bearer test-token"
    assert api.requests[0].path == "/api/v1/protocol"

    polls = 0

    def poll(_):
        nonlocal polls
        polls += 1
        return Response(body={"id": "00000000-0000-4000-8000-000000000002", "state": "running" if polls == 1 else "succeeded", "artifacts": []})

    api.handle("GET", "/jobs/00000000-0000-4000-8000-000000000002", poll)
    reconnected = new_client(api)
    assert reconnected.wait(handle.job_id, timeout=2)["state"] == "succeeded"
    assert len(api.seen("POST", "/jobs")) == 1
    assert len(api.seen("POST", "/job-previews")) == 1


@pytest.mark.parametrize("revision", [1, 3])
def test_missing_answers_raise_with_exact_preview_revision(api, new_client, revision):
    ready_preview(api)
    questions = [{"question_id": "dataset", "prompt": "Which dataset?"}]
    api.respond("GET", "/job-previews/00000000-0000-4000-8000-000000000004", {
        "id": "00000000-0000-4000-8000-000000000004", "state": "needs_input", "plan_revision": revision, "question": questions[0],
    })
    with pytest.raises(campus.NeedsInputError) as caught:
        new_client(api).submit({"prompt": "train"})
    assert caught.value.preview_id == "00000000-0000-4000-8000-000000000004"
    assert caught.value.plan_revision == revision
    assert caught.value.questions == questions
    assert not api.seen("POST", "/jobs")
    assert not api.seen("POST", "/job-previews/00000000-0000-4000-8000-000000000004/answers")


def test_preview_polling_waits_for_ready_and_failed_preview_never_runs(api, new_client):
    ready_preview(api)
    states = iter(["planning", "failed"])
    api.handle("GET", "/job-previews/00000000-0000-4000-8000-000000000004", lambda _: Response(body={
        "id": "00000000-0000-4000-8000-000000000004", "state": next(states), "plan_revision": 4,
    }))
    with pytest.raises(campus.PreviewError) as caught:
        new_client(api).submit({"prompt": "train"})
    assert caught.value.state == "failed"
    assert not api.seen("POST", "/jobs")


def test_logs_preserve_numeric_cursor_and_cancel_is_explicit(api, new_client):
    api.respond("GET", "/jobs/00000000-0000-4000-8000-000000000002/logs", {"entries": [], "cursor": 19, "truncated": False})
    api.respond("POST", "/jobs/00000000-0000-4000-8000-000000000002/cancel", {"id": "00000000-0000-4000-8000-000000000002", "state": "cancelled"})
    client = new_client(api)
    assert client.logs("00000000-0000-4000-8000-000000000002", cursor=12)["cursor"] == 19
    assert api.seen("GET", "/jobs/00000000-0000-4000-8000-000000000002/logs")[0].query == {"cursor": ["12"]}
    assert not api.seen("POST", "/jobs/00000000-0000-4000-8000-000000000002/cancel")
    assert client.cancel("00000000-0000-4000-8000-000000000002")["state"] == "cancelled"
    assert api.seen("POST", "/jobs/00000000-0000-4000-8000-000000000002/cancel")[0].body is None


def test_wait_reports_failed_or_cancelled_job_and_timeout(api, new_client):
    api.respond("GET", "/jobs/00000000-0000-4000-8000-000000000002", {"id": "00000000-0000-4000-8000-000000000002", "state": "failed", "artifacts": []})
    client = new_client(api)
    with pytest.raises(campus.JobFailedError):
        client.wait("00000000-0000-4000-8000-000000000002", timeout=1)
    api.respond("GET", "/jobs/00000000-0000-4000-8000-000000000002", {"id": "00000000-0000-4000-8000-000000000002", "state": "cancelled", "artifacts": []})
    with pytest.raises(campus.JobCancelledError):
        client.wait("00000000-0000-4000-8000-000000000002", timeout=1)
    api.respond("GET", "/jobs/00000000-0000-4000-8000-000000000002", {"id": "00000000-0000-4000-8000-000000000002", "state": "running", "artifacts": []})
    with pytest.raises(TimeoutError):
        client.wait("00000000-0000-4000-8000-000000000002", timeout=0)


def test_incompatible_handshake_stops_before_preview(api, new_client):
    api.respond("GET", "/protocol", {"api_version": "1", "min_runner_version": "0.2.0", "max_runner_version": "0.2.99"})
    with pytest.raises(campus.ProtocolError):
        new_client(api).submit({"prompt": "train"})
    assert not api.seen("POST", "/job-previews")
