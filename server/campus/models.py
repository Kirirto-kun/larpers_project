from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {datetime: DateTime(timezone=True)}


class Identified:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class Created:
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())


class Pool(Identified, Base):
    __tablename__ = "pools"
    name: Mapped[str] = mapped_column(String(256))


class User(Identified, Base):
    __tablename__ = "users"
    pool_id: Mapped[UUID] = mapped_column(ForeignKey("pools.id"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))
    last_dispatched_at: Mapped[datetime | None]


class Node(Identified, Created, Base):
    __tablename__ = "nodes"
    __table_args__ = (UniqueConstraint("pool_id", "installation_id"),)
    pool_id: Mapped[UUID] = mapped_column(ForeignKey("pools.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    installation_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(256))
    mode: Mapped[str] = mapped_column(String(16), default="ready")
    last_seen_at: Mapped[datetime | None]
    capabilities: Mapped[dict | None] = mapped_column(JSONB)
    current_session_id: Mapped[UUID | None]


class Invite(Identified, Created, Base):
    __tablename__ = "invitations"
    pool_id: Mapped[UUID] = mapped_column(ForeignKey("pools.id"), index=True)
    issuer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    existing_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]


class Credential(Identified, Created, Base):
    __tablename__ = "credentials"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    node_id: Mapped[UUID | None] = mapped_column(ForeignKey("nodes.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class NodeSession(Identified, Created, Base):
    __tablename__ = "node_sessions"
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"), index=True)
    credential_id: Mapped[UUID] = mapped_column(ForeignKey("credentials.id"))


class Preview(Identified, Created, Base):
    __tablename__ = "previews"
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    state: Mapped[str] = mapped_column(String(16), index=True)
    revision: Mapped[int] = mapped_column(default=1)
    request: Mapped[dict] = mapped_column(JSONB)
    answer: Mapped[dict | None] = mapped_column(JSONB)
    question: Mapped[dict | None] = mapped_column(JSONB)
    plan: Mapped[dict | None] = mapped_column(JSONB)
    plan_digest: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    claim_id: Mapped[UUID | None]
    claim_expires_at: Mapped[datetime | None]
    deadline_at: Mapped[datetime]
    prepared: Mapped[dict | None] = mapped_column(JSONB)
    probe_job_id: Mapped[UUID | None]


class Job(Identified, Created, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key"),)
    pool_id: Mapped[UUID] = mapped_column(ForeignKey("pools.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    preview_id: Mapped[UUID | None] = mapped_column(ForeignKey("previews.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_digest: Mapped[str | None] = mapped_column(String(64))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    plan: Mapped[dict] = mapped_column(JSONB)
    purpose: Mapped[str] = mapped_column(String(16))
    deadline_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None]
    node_ids: Mapped[list | None] = mapped_column(JSONB)
    concurrency: Mapped[int | None]
    search_result: Mapped[dict | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None]


class Task(Identified, Created, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index(
            "ix_tasks_dispatch",
            "state",
            "available_at",
            "created_at",
            postgresql_where=text("state IN ('queued','waiting_for_capacity','retry_wait')"),
        ),
    )
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    stage_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    item_ids: Mapped[list] = mapped_column(JSONB, default=list)
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    units: Mapped[int] = mapped_column(default=0)
    generation: Mapped[int] = mapped_column(default=0)
    current_attempt_id: Mapped[UUID | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    verification_level: Mapped[str | None] = mapped_column(String(32))
    available_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())


class Attempt(Identified, Created, Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "generation"),
        Index(
            "ix_attempts_compute_lease",
            "lease_expires_at",
            postgresql_where=text("state IN ('leased','running')"),
        ),
    )
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), index=True)
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"), index=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("node_sessions.id"), index=True)
    generation: Mapped[int]
    token_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    variant: Mapped[dict] = mapped_column(JSONB)
    deadline_at: Mapped[datetime]
    lease_expires_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    completion_digest: Mapped[str | None] = mapped_column(String(64))
    receipt: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    verification_deadline_at: Mapped[datetime | None]
    verification_claim_id: Mapped[UUID | None]
    verification_claim_expires_at: Mapped[datetime | None]
    verification_claims: Mapped[int] = mapped_column(default=0)
    log_bytes: Mapped[int] = mapped_column(default=0)
    log_truncated: Mapped[bool] = mapped_column(Boolean, default=False)


class Upload(Identified, Created, Base):
    __tablename__ = "uploads"
    pool_id: Mapped[UUID] = mapped_column(ForeignKey("pools.id"))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("attempts.id"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    part_size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(16), index=True)
    expires_at: Mapped[datetime]


class UploadPart(Base):
    __tablename__ = "upload_parts"
    upload_id: Mapped[UUID] = mapped_column(ForeignKey("uploads.id"), primary_key=True)
    number: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]


class Artifact(Identified, Created, Base):
    __tablename__ = "artifacts"
    upload_id: Mapped[UUID] = mapped_column(ForeignKey("uploads.id"), unique=True)
    pool_id: Mapped[UUID] = mapped_column(ForeignKey("pools.id"))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("attempts.id"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    media_type: Mapped[str] = mapped_column(String(256))


class Checkpoint(Identified, Created, Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("attempt_id", "sequence"),)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("attempts.id"))
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"))
    compatibility_digest: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int]


class Acceptance(Created, Base):
    __tablename__ = "acceptances"
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("attempts.id"), unique=True)
    result: Mapped[dict] = mapped_column(JSONB)
    report: Mapped[dict] = mapped_column(JSONB)


class Ledger(Created, Base):
    __tablename__ = "ledger"
    task_id: Mapped[UUID] = mapped_column(ForeignKey("acceptances.task_id"), primary_key=True)
    contributor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    consumer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    accepted_units: Mapped[int]
    unit: Mapped[str] = mapped_column(String(80))
    rate_version: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int]


class Event(Created, Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    pool_id: Mapped[UUID] = mapped_column(ForeignKey("pools.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[UUID] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class Log(Created, Base):
    __tablename__ = "logs"
    __table_args__ = (UniqueConstraint("attempt_id", "sequence"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("attempts.id"))
    sequence: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    gap: Mapped[bool]


class Metric(Created, Base):
    __tablename__ = "metrics"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"), index=True)
    stream_id: Mapped[UUID] = mapped_column(index=True)
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("attempts.id"), index=True)
    sequence: Mapped[int]
    sample: Mapped[dict] = mapped_column(JSONB)
    gap: Mapped[bool]
    __table_args__ = (UniqueConstraint("stream_id", "sequence"),)


class Benchmark(Identified, Created, Base):
    __tablename__ = "benchmarks"
    pool_id: Mapped[UUID] = mapped_column(ForeignKey("pools.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    config: Mapped[dict] = mapped_column(JSONB)
    config_digest: Mapped[str] = mapped_column(String(64))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    plan: Mapped[dict] = mapped_column(JSONB)
    prepared: Mapped[dict] = mapped_column(JSONB)
    service_runs: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="collecting")


class Standalone(Created, Base):
    __tablename__ = "standalone_results"
    benchmark_id: Mapped[UUID] = mapped_column(ForeignKey("benchmarks.id"), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    repetition: Mapped[int] = mapped_column(primary_key=True)
    record: Mapped[dict] = mapped_column(JSONB)
    credential_id: Mapped[UUID] = mapped_column(ForeignKey("credentials.id"))
