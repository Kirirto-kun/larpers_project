from uuid import UUID

import pytest
from sqlalchemy import select

from campus import models as m


@pytest.mark.parametrize(
    ("sequences", "gaps"),
    [
        ([0, 1], [False, False]),
        ([1, 2], [False, False]),
        ([2, 3], [True, False]),
        ([1, 3], [False, True]),
    ],
)
def test_http_attempt_logs_accept_zero_or_one_start_and_preserve_real_gaps(cp, sequences, gaps):
    job = cp.job()
    device, _ = cp.device()
    assignment = cp.claim(device)
    for sequence in sequences:
        extra = {"sequence": sequence, "text": f"worker line {sequence}\n"}
        cp.attempt(device, assignment, "logs", extra=extra, code=204)
        # A retried accepted chunk must not create another entry or manufacture a gap.
        cp.attempt(device, assignment, "logs", extra=extra, code=204)
    logs = cp.call("GET", f"/jobs/{job['id']}/logs")
    assert [entry["sequence"] for entry in logs["entries"]] == sequences
    assert [entry["gap"] for entry in logs["entries"]] == gaps
    assert not logs["truncated"]
    assert "".join(entry["text"] for entry in logs["entries"]) == "".join(
        f"worker line {sequence}\n" for sequence in sequences
    )
    cp.attempt(
        device, assignment, "logs", extra={"sequence": sequences[-1], "text": "changed"}, code=409
    )
    cp.attempt(
        device,
        assignment,
        "logs",
        extra={"sequence": sequences[-1] - 1, "text": "late missing line"},
        code=409,
    )


@pytest.mark.parametrize(("first", "expected_gap"), [(0, False), (1, False), (2, True)])
def test_http_telemetry_initial_sequence_has_same_zero_or_one_baseline(cp, first, expected_gap):
    device, session = cp.device()
    body = {"sequence": first, "phase": "idle", "cpu_percent": 5}
    path = f"/node-sessions/{session['id']}/telemetry"
    cp.call("POST", path, headers=device, body=body, code=204)
    cp.call("POST", path, headers=device, body=body, code=204)
    with cp.runtime.sessions.begin() as db:
        rows = list(db.scalars(select(m.Metric).where(m.Metric.stream_id == UUID(session["id"]))))
        assert len(rows) == 1
        assert rows[0].gap is expected_gap
