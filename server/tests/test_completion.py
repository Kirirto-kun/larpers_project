from sqlalchemy import func, select
from test_leases import expire

from campus import models as m
from campus.jobs import verification


def test_acceptance_lost_ack_and_atomic_credit(cp):
    job = cp.job()
    node, _ = cp.device()
    assignment = cp.claim(node)
    cp.attempt(node, assignment, "started")
    result = cp.complete(node, assignment)
    expire(cp, assignment)
    replay = cp.attempt(node, assignment, "complete", extra={"manifest": result.manifest}, code=202)
    assert replay == result.receipt
    assert (
        cp.attempt(
            node,
            assignment,
            "complete",
            extra={"manifest": {**result.manifest, "compute_ms": 123}},
            code=409,
        )["code"]
        == "IDEMPOTENCY_CONFLICT"
    )
    state = cp.call("GET", f"/jobs/{job['id']}")
    assert state["state"] == "succeeded"
    assert state["accepted_units"] == 1
    assert state["tasks"][0]["verification_level"] == "structural"
    with cp.runtime.sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(m.Acceptance)
                .where(m.Acceptance.task_id == assignment["task_id"])
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(m.Ledger)
                .where(m.Ledger.task_id == assignment["task_id"])
            )
            == 1
        )
    content = cp.call(
        "GET",
        f"/artifacts/{result.artifact['id']}/content",
        headers={**cp.owner, "Range": "bytes=1-3"},
        code=206,
    )
    assert content.content == b"omp"


def test_cancel_after_complete_blocks_validator_commit(cp):
    job = cp.job()
    node, _ = cp.device()
    assignment = cp.claim(node)
    result = cp.complete(node, assignment, verify=False)
    with cp.runtime.sessions.begin() as db:
        candidate = verification.claim(db, cp.runtime)
    report = verification.verify_result(cp.runtime, candidate)
    cancelled = cp.call("POST", f"/jobs/{job['id']}/cancel")
    assert cancelled["state"] == "cancelled"
    with cp.runtime.sessions.begin() as db:
        verification.accept_result(db, cp.runtime, candidate, report)
    view = cp.call("GET", f"/jobs/{job['id']}")
    assert view["accepted_units"] == 0
    cp.call("GET", f"/artifacts/{result.artifact['id']}/content", code=404)
