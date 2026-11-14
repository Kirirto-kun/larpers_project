from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from campus import models as m
from campus.db import now


def expire(cp, assignment):
    with cp.runtime.sessions.begin() as db:
        row = db.get(m.Attempt, assignment["attempt_id"])
        row.lease_expires_at = now(db) - timedelta(seconds=1)


def test_concurrent_claim_reserves_device_and_task(cp):
    cp.job()
    first, _ = cp.device()
    second, _ = cp.device()
    with ThreadPoolExecutor(2) as workers:
        calls = [
            workers.submit(cp.client.post, "/api/v1/work/next", headers=h, json={"wait_ms": 0})
            for h in (first, second)
        ]
    assert sorted(response.result().status_code for response in calls) == [200, 204]
    assignment = next(
        response.result().json() for response in calls if response.result().status_code == 200
    )
    cp.job()
    chosen = (
        first
        if assignment["attempt_id"]
        in [
            r.json().get("attempt_id")
            for r in [cp.client.get(f"/api/v1/attempts/{assignment['attempt_id']}", headers=first)]
            if r.status_code == 200
        ]
        else second
    )
    cp.claim(chosen, 204)


def test_expiration_before_reaper_and_retry(cp):
    job = cp.job()
    node, _ = cp.device()
    assignment = cp.claim(node)
    expire(cp, assignment)
    assert cp.attempt(node, assignment, "renew", code=409)["code"] == "LEASE_EXPIRED"
    cp.maintenance()
    second = cp.claim(node)
    assert second["task_id"] == assignment["task_id"]
    assert second["generation"] == 2
    assert second["lease_token"] != assignment["lease_token"]
    assert cp.attempt(node, assignment, "renew", code=409)["code"] == "STALE_ATTEMPT"
    cp.complete(node, second)
    assert cp.call("GET", f"/jobs/{job['id']}")["accepted_units"] == 1


def test_replaced_session_is_fenced(cp):
    cp.job()
    node, _ = cp.device()
    assignment = cp.claim(node)
    new_session = cp.call("POST", "/node-sessions", headers=node, code=201, body={"protocol_version": 1})
    assert cp.attempt(node, assignment, "renew", code=409)["code"] == "SESSION_REPLACED"
    assert (
        cp.attempt({**node, "X-Node-Session": new_session["id"]}, assignment, "renew", code=409)["code"]
        == "STALE_ATTEMPT"
    )
