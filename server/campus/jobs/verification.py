import hashlib
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select

from .. import dto
from .. import models as m
from ..db import now, payload, write_lock
from ..errors import Problem
from ..events import emit
from .adapters import exchange, read_json_result
from .scheduler import assignment_inputs
from .service import stage_for, update_finished


@dataclass(frozen=True)
class Candidate:
    attempt_id: UUID
    claim_id: UUID
    stage: dto.Stage
    result: dict
    inputs: list
    outputs: list
    item_ids: list
    purpose: str
    parameters: dict
    timeout_seconds: float


def claim(db, app):
    write_lock(db)
    current = now(db)
    expired = db.execute(
        select(m.Attempt, m.Task, m.Job)
        .join(m.Task, m.Attempt.task_id == m.Task.id)
        .join(m.Job, m.Task.job_id == m.Job.id)
        .where(m.Attempt.state == "verifying", m.Attempt.verification_deadline_at <= current)
        .limit(100)
    )
    for attempt, task, job in expired:
        reject(db, job, task, attempt, "VERIFICATION_TIMEOUT", current)
    row = db.execute(
        select(m.Attempt, m.Task, m.Job)
        .join(m.Task, m.Attempt.task_id == m.Task.id)
        .join(m.Job, m.Task.job_id == m.Job.id)
        .where(
            m.Attempt.state == "verifying",
            m.Attempt.verification_deadline_at > current,
            m.Job.cancelled_at.is_(None),
            (m.Attempt.verification_claim_expires_at.is_(None))
            | (m.Attempt.verification_claim_expires_at <= current),
        )
        .order_by(m.Attempt.verification_deadline_at)
        .limit(1)
    ).first()
    if not row:
        return None
    attempt, task, job = row
    if attempt.verification_claims >= 3:
        reject(db, job, task, attempt, "VERIFICATION_RETRIES_EXHAUSTED", current)
        return None
    claim_id = uuid4()
    attempt.verification_claims += 1
    attempt.verification_claim_id = claim_id
    timeout = max(0.05, min(20, (attempt.verification_deadline_at - current).total_seconds()))
    attempt.verification_claim_expires_at = min(
        attempt.verification_deadline_at, current + timedelta(seconds=timeout + 2)
    )
    stage = stage_for(job, task)
    inputs = [payload(item) for item in assignment_inputs(db, job, task, stage)]
    outputs = []
    for item in attempt.result["outputs"]:
        artifact = db.get(m.Artifact, item["artifact_id"])
        if artifact:
            outputs.append({**item, "sha256": artifact.sha256, "size_bytes": artifact.size_bytes})
    return Candidate(
        attempt.id,
        claim_id,
        stage,
        attempt.result,
        inputs,
        outputs,
        task.item_ids,
        job.purpose,
        {**stage.parameters, **task.parameters},
        timeout,
    )


def verify_result(app, candidate):
    if len(candidate.outputs) != len(candidate.result["outputs"]):
        raise Problem(
            "ARTIFACT_NOT_READY", "Published result could not be verified", 503, retryable=True
        )
    deadline = time.monotonic() + candidate.timeout_seconds
    for item in candidate.outputs:
        path = app.storage.blob(item["sha256"])
        try:
            with path.open("rb") as file:
                checksum = hashlib.sha256()
                for block in iter(lambda: file.read(1024 * 1024), b""):
                    if time.monotonic() >= deadline:
                        raise Problem(
                            "VERIFICATION_TIMEOUT",
                            "Verification exceeded its time budget",
                            503,
                            retryable=True,
                        )
                    checksum.update(block)
                actual, size = checksum.hexdigest(), os.fstat(file.fileno()).st_size
        except OSError as exc:
            raise Problem(
                "STORAGE_UNAVAILABLE", "Published result could not be read", 503, retryable=True
            ) from exc
        if actual != item["sha256"] or size != item["size_bytes"]:
            raise Problem("RESULT_CORRUPT", "Published result failed its checksum", 422)
    adapter = app.catalog.adapter(candidate.stage.adapter)
    if candidate.stage.validation_policy == "structural":
        if candidate.purpose in {"benchmark", "search", "probe"}:
            raise Problem("VALIDATOR_REQUIRED", "This job requires a registered semantic validator", 422)
        return {"level": "structural", "accepted": True}
    details = exchange(
        adapter,
        "verify",
        {
            "stage": payload(candidate.stage),
            "inputs": candidate.inputs,
            "outputs": candidate.outputs,
            "item_ids": candidate.item_ids,
            "parameters": candidate.parameters,
            "purpose": candidate.purpose,
        },
        timeout=max(0, deadline - time.monotonic()),
    )
    level = details.get("level")
    if details.get("accepted") is not True or level not in {"sampled", "full"}:
        raise Problem("VALIDATION_FAILED", "Result failed the registered validation policy", 422)
    if candidate.purpose == "benchmark" and level != "full":
        raise Problem("VALIDATION_FAILED", "A benchmark requires full validation", 422)
    report = {"level": level, "accepted": True}
    if candidate.purpose == "search":
        entry = next((o for o in candidate.outputs if o["path"] == "search.json"), None)
        if not entry:
            raise Problem("INVALID_RESULT", "Search result is missing", 422)
        try:
            result = dto.SearchResult.model_validate(
                read_json_result(app.storage.blob(entry["sha256"]), 262_144)
            )
        except ValidationError as exc:
            raise Problem("INVALID_RESULT", "Search result failed schema validation", 422) from exc
        if (
            result.index_digest != candidate.parameters["index_digest"]
            or result.model_digest != candidate.parameters["model_digest"]
            or len(result.matches) > candidate.parameters["top_k"]
        ):
            raise Problem(
                "INVALID_RESULT", "Search output does not match its index or query constraints", 422
            )
        report["search_result"] = payload(result)
    return report


def reject(db, job, task, attempt, code, current):
    attempt.state = task.state = "failed"
    attempt.error_code = task.error_code = code
    emit(
        db,
        job.pool_id,
        "job",
        job.id,
        "verification_failed",
        task_id=str(task.id),
        attempt_id=str(attempt.id),
        code=code,
    )
    update_finished(db, job, current)


def accept_result(db, app, candidate, report=None, error=None):
    write_lock(db)
    attempt = db.get(m.Attempt, candidate.attempt_id)
    task = db.get(m.Task, attempt.task_id)
    job = db.get(m.Job, task.job_id)
    current = now(db)
    if (
        job.cancelled_at
        or task.current_attempt_id != attempt.id
        or attempt.state != "verifying"
        or attempt.verification_claim_id != candidate.claim_id
    ):
        return
    if attempt.verification_deadline_at <= current:
        reject(db, job, task, attempt, "VERIFICATION_TIMEOUT", current)
        return
    if attempt.verification_claim_expires_at <= current:
        return
    if error:
        if error.retryable and attempt.verification_claims < 3:
            attempt.verification_claim_id = None
            attempt.verification_claim_expires_at = current + timedelta(
                seconds=0.5 * attempt.verification_claims
            )
        else:
            reject(db, job, task, attempt, error.code, current)
        return
    db.add(m.Acceptance(task_id=task.id, attempt_id=attempt.id, result=attempt.result, report=report))
    db.flush()
    if task.units and job.purpose == "work":
        adapter = app.catalog.adapter(candidate.stage.adapter)
        node = db.get(m.Node, attempt.node_id)
        db.add(
            m.Ledger(
                task_id=task.id,
                contributor_id=node.owner_id,
                consumer_id=job.owner_id,
                accepted_units=task.units,
                unit=adapter.unit,
                rate_version="1",
                amount=task.units * adapter.weight,
            )
        )
    if report.get("search_result"):
        job.search_result = report["search_result"]
    attempt.state = task.state = "succeeded"
    task.verification_level = report["level"]
    emit(
        db,
        job.pool_id,
        "job",
        job.id,
        "result_accepted",
        task_id=str(task.id),
        attempt_id=str(attempt.id),
        accepted_units=task.units,
        level=report["level"],
        node_id=str(attempt.node_id),
    )
    update_finished(db, job, current)
