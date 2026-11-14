from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

SHA256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
ImageDigest = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$", max_length=256)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")]
FilePath = Annotated[str, StringConstraints(min_length=1, max_length=512)]
Positive = Annotated[int, Field(gt=0)]
Nonnegative = Annotated[int, Field(ge=0)]
Accelerator = Literal["cpu", "cuda"]
NodeState = Literal["preflight", "ready", "draining", "paused", "offline", "quarantined"]
PreviewState = Literal["inspecting", "planning", "needs_input", "validating", "ready", "failed", "cancelled"]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TaskState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    VERIFYING = "verifying"
    RETRY_WAIT = "retry_wait"
    WAITING = "waiting_for_capacity"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Error(DTO):
    code: str
    message: str
    field_errors: list[dict[str, Any]] = []
    retryable: bool = False
    request_id: str


class Event(DTO):
    event_id: Positive
    occurred_at: AwareDatetime
    entity_type: str
    entity_id: UUID
    type: str
    payload: dict[str, Any]


class EventPage(DTO):
    events: list[Event]
    cursor: Nonnegative
    reset: bool = False


class Budget(DTO):
    cpu_millis: Nonnegative
    memory_bytes: Nonnegative
    disk_bytes: Nonnegative
    gpu_slots: Annotated[int, Field(ge=0, le=64)] = 0
    vram_bytes: Nonnegative = 0


class Resources(Budget):
    cpu_millis: Positive
    memory_bytes: Positive
    disk_bytes: Positive


class Runtime(DTO):
    type: Literal["docker"]
    version: ShortText
    healthy: bool


class GPU(DTO):
    id: Identifier
    vendor: ShortText
    vram_bytes: Nonnegative
    driver: ShortText
    cuda_verified: bool


class Capabilities(DTO):
    os: Literal["linux", "windows", "darwin"]
    arch: Literal["amd64", "arm64"]
    cpu_logical: Annotated[int, Field(gt=0, le=1024)]
    memory_available_bytes: Nonnegative
    disk_available_bytes: Nonnegative
    runtime: Runtime
    gpus: list[GPU] = Field(default_factory=list, max_length=64)
    power_state: Literal["ac", "battery", "unknown"]
    offered_budget: Budget
    preflight_revision: Positive
    image_digests: list[ImageDigest] = Field(default_factory=list, max_length=128)
    allow_on_battery: bool = False


class AdapterRef(DTO):
    id: Identifier
    version: Identifier


class Variant(DTO):
    variant_id: Identifier
    image_digest: ImageDigest
    platform: Literal["linux/amd64", "linux/arm64"]
    accelerator: Accelerator
    resource_requirements: Resources


class InputBinding(DTO):
    artifact_id: UUID
    mount_path: FilePath
    archive: bool = False


class Output(DTO):
    path: FilePath
    required: bool = True
    max_bytes: Positive


class Evidence(DTO):
    source: FilePath
    detail: ShortText


class Question(DTO):
    question_id: Identifier
    prompt: ShortText


class Stage(DTO):
    stage_id: Identifier
    depends_on: list[Identifier] = Field(default_factory=list, max_length=24)
    adapter: AdapterRef
    variants: list[Variant] = Field(min_length=1, max_length=8)
    argv: list[Annotated[str, StringConstraints(max_length=4096)]] = Field(min_length=1, max_length=64)
    working_dir: FilePath
    bindings: list[InputBinding] = Field(default_factory=list, max_length=32)
    outputs: list[Output] = Field(min_length=1, max_length=32)
    network_policy: Literal["none"] = "none"
    task_deadline_ms: Annotated[int, Field(ge=1000, le=86_400_000)]
    validation_policy: Identifier
    resume_policy: Literal["checkpoint", "restart", "unsupported"]
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=32)


class RetryPolicy(DTO):
    max_attempts: Annotated[int, Field(ge=1, le=3)] = 3
    backoff_ms: Annotated[int, Field(ge=0, le=60_000)] = 1000


class JobPlan(DTO):
    schema_version: Literal["1"] = "1"
    plan_revision: Positive
    project_digest: SHA256 | None = None
    input_manifest_digest: SHA256
    execution_kind: Literal["single", "map", "dag"]
    stages: list[Stage] = Field(min_length=1, max_length=24)
    job_deadline_at: AwareDatetime
    retry_policy: RetryPolicy
    assumptions: list[ShortText] = Field(default_factory=list, max_length=24)
    questions: list[Question] = Field(default_factory=list, max_length=1)
    evidence: list[Evidence] = Field(default_factory=list, max_length=64)


class PreviewRequest(DTO):
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=8000)] | None = None
    recipe_id: Identifier | None = None
    project_artifact_id: UUID | None = None
    inputs: list[InputBinding] = Field(default_factory=list, max_length=32)
    selected_files: list[FilePath] = Field(default_factory=list, max_length=16)
    share_with_llm: bool = False
    accelerator: Accelerator | None = None
    prepared_plan: JobPlan | None = None


class PreviewAnswer(DTO):
    question_id: Identifier
    plan_revision: Positive
    answer: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class Preview(DTO):
    id: UUID
    state: PreviewState
    plan_revision: Positive
    plan: JobPlan | None = None
    plan_digest: SHA256 | None = None
    question: Question | None = None
    error_code: str | None = None
    error_message: str | None = None


class JobCreate(DTO):
    preview_id: UUID
    plan_revision: Positive


class SearchRequest(DTO):
    query: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    top_k: Annotated[int, Field(ge=1, le=50)] = 10
    index_artifact_id: UUID


class SearchMatch(DTO):
    item_id: ShortText
    source: FilePath
    excerpt: Annotated[str, StringConstraints(max_length=4096)]
    score: float


class SearchResult(DTO):
    matches: list[SearchMatch]
    model_digest: SHA256
    index_digest: SHA256


class Artifact(DTO):
    id: UUID
    name: FilePath
    size_bytes: Nonnegative
    sha256: SHA256
    media_type: ShortText


class Task(DTO):
    id: UUID
    stage_id: Identifier
    state: TaskState
    generation: Nonnegative
    units: Nonnegative
    error_code: str | None = None
    attempt_id: UUID | None = None
    node_id: UUID | None = None
    verification_level: str | None = None


class Job(DTO):
    id: UUID
    owner_id: UUID
    state: Literal["queued", "running", "waiting_for_capacity", "succeeded", "partial", "failed", "cancelled"]
    purpose: Literal["work", "probe", "search", "benchmark"]
    created_at: AwareDatetime
    accepted_units: Nonnegative
    failed_units: Nonnegative
    total_units: Nonnegative
    tasks: list[Task]
    artifacts: list[Artifact]
    search_result: SearchResult | None = None


class AssignmentInput(InputBinding):
    artifact: Artifact


class AttemptProof(DTO):
    generation: Positive
    lease_token: Annotated[str, StringConstraints(min_length=32, max_length=128)]


class Assignment(AttemptProof):
    job_id: UUID
    stage_id: Identifier
    task_id: UUID
    attempt_id: UUID
    variant_id: Identifier
    lease_expires_at: AwareDatetime
    lease_remaining_ms: Nonnegative
    heartbeat_interval_ms: Positive
    task_deadline_at: AwareDatetime
    manifest_digest: SHA256
    adapter: AdapterRef
    image_digest: ImageDigest
    platform: str
    accelerator: Accelerator
    argv: list[str]
    working_dir: FilePath
    inputs: list[AssignmentInput]
    outputs: list[Output]
    resource_limits: Resources
    network_policy: Literal["none"]
    resume_checkpoint: Artifact | None = None
    validator_policy: Identifier
    item_ids: list[ShortText]
    parameters: dict[str, Any]


class WorkRequest(DTO):
    wait_ms: Annotated[int, Field(ge=0, le=25_000)] = 25_000


class LeaseReceipt(DTO):
    attempt_id: UUID
    lease_expires_at: AwareDatetime
    lease_remaining_ms: Nonnegative
    state: TaskState


class OutputRef(DTO):
    path: FilePath
    artifact_id: UUID


class ResultManifest(DTO):
    outputs: list[OutputRef] = Field(min_length=1, max_length=32)
    item_ids: list[ShortText] = Field(default_factory=list, max_length=10_000)
    compute_ms: Nonnegative | None = None


class Complete(AttemptProof):
    manifest: ResultManifest


class Failure(AttemptProof):
    code: Identifier
    retryable: bool = False


class CheckpointPublish(AttemptProof):
    artifact_id: UUID
    compatibility_digest: SHA256
    sequence: Positive


class CompletionReceipt(DTO):
    attempt_id: UUID
    task_id: UUID
    state: TaskState
    submitted_at: AwareDatetime


class AttemptStatus(DTO):
    attempt_id: UUID
    task_id: UUID
    generation: Positive
    state: TaskState
    error_code: str | None = None
    receipt: CompletionReceipt | None = None


class UploadCreate(DTO):
    name: FilePath
    size_bytes: Nonnegative
    sha256: SHA256
    media_type: ShortText = "application/octet-stream"
    part_size_bytes: Annotated[int, Field(ge=1, le=8_388_608)] = 4_194_304
    kind: Literal["input", "project", "output", "checkpoint"] = "input"
    attempt_id: UUID | None = None
    proof: AttemptProof | None = None


class UploadPart(DTO):
    number: Nonnegative
    sha256: SHA256
    size_bytes: Positive


class Upload(DTO):
    id: UUID
    state: Literal["pending", "ready", "expired"]
    part_count: Nonnegative
    parts: list[UploadPart]
    artifact: Artifact | None = None


class InviteCreate(DTO):
    kind: Literal["member", "device"]
    name: ShortText
    ttl_seconds: Annotated[int, Field(ge=30, le=86_400)] = 1800


class Invitation(DTO):
    id: UUID
    token: str
    kind: Literal["member", "device"]
    expires_at: AwareDatetime


class InviteLogin(DTO):
    token: Annotated[str, StringConstraints(min_length=32, max_length=160)]


class DeviceRedeem(InviteLogin):
    installation_id: UUID
    protocol_version: Literal[1]


class Credential(DTO):
    id: UUID
    token: str
    scope: Literal["cli", "device"]
    node_id: UUID | None = None
    expires_at: AwareDatetime | None = None


class Identity(DTO):
    id: UUID
    name: ShortText
    role: Literal["owner", "admin", "member"]
    pool_id: UUID
    csrf_token: str | None = None


class SessionCreate(DTO):
    protocol_version: Literal[1]
    capabilities: Capabilities | None = None


class NodeSession(DTO):
    id: UUID
    node_id: UUID
    state: NodeState


class Node(DTO):
    id: UUID
    name: ShortText
    state: NodeState
    last_seen_at: AwareDatetime | None = None
    capabilities: Capabilities | None = None
    accepted_units: Nonnegative
    active_attempts: Nonnegative


class NodeControl(DTO):
    action: Literal["drain", "stop", "resume", "quarantine", "release"]


class LogWrite(AttemptProof):
    sequence: Nonnegative
    text: Annotated[str, StringConstraints(max_length=8192)]


class LogEntry(DTO):
    cursor: Positive
    attempt_id: UUID
    sequence: Nonnegative
    occurred_at: AwareDatetime
    text: str
    gap: bool


class LogPage(DTO):
    entries: list[LogEntry]
    cursor: Nonnegative
    truncated: bool


class Telemetry(DTO):
    sequence: Nonnegative
    cpu_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    gpu_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    memory_bytes: Nonnegative | None = None
    phase: Literal["idle", "transfer", "start", "compute", "verify", "aggregate"]


class AttemptTelemetry(Telemetry, AttemptProof):
    pass


class Protocol(DTO):
    api_version: Literal[1] = 1
    min_worker_version: Literal[1] = 1
    max_worker_version: Literal[1] = 1
    lease_ms: Positive
    heartbeat_interval_ms: Positive
    long_poll_ms: Nonnegative
    tls_fingerprint_sha256: SHA256 | None = None


class PoolSummary(DTO):
    pool_id: UUID
    nodes: Nonnegative
    ready_nodes: Nonnegative
    jobs_by_state: dict[str, Nonnegative]
    accepted_units: Nonnegative


class Credit(DTO):
    task_id: UUID
    contributor_id: UUID
    consumer_id: UUID
    accepted_units: Positive
    unit: str
    rate_version: str
    amount: Positive
    occurred_at: AwareDatetime


class Environment(Variant):
    id: Identifier
    name: ShortText


class Recipe(DTO):
    id: Identifier
    name: ShortText
    description: str
    adapter: AdapterRef


class AdapterRequest(DTO):
    operation: Literal["validate_input", "split", "verify", "aggregate"]
    payload: dict[str, Any]


class AdapterResponse(DTO):
    result: dict[str, Any] | None = None
    error: ShortText | None = None


class StandaloneRecord(DTO):
    schema_version: Literal["1"] = "1"
    node_id: UUID
    manifest_digest: SHA256
    config_digest: SHA256
    model_digest: SHA256 | None = None
    image_digest: ImageDigest
    adapter: AdapterRef
    resource_limits: Resources
    repetition: Nonnegative
    units: Positive
    quality_passed: bool
    duration_ms: Positive
    verification_ms: Nonnegative = 0
    started_at: AwareDatetime
    ended_at: AwareDatetime
    warm: bool
    utilization: list[Telemetry] = Field(default_factory=list, max_length=10_000)


class BenchmarkCreate(DTO):
    preview_id: UUID
    plan_revision: Positive
    node_ids: list[UUID] = Field(min_length=2, max_length=32)
    concurrency: Annotated[int, Field(ge=1, le=512)]
    batch_policy: dict[str, Positive] = Field(default_factory=dict, max_length=16)
    repetitions: Annotated[int, Field(ge=3, le=20)] = 3
    warm: bool = True


class BenchmarkReport(DTO):
    id: UUID
    config: BenchmarkCreate
    config_digest: SHA256
    manifest_digest: SHA256
    status: Literal["collecting", "running", "complete", "failed"]
    standalone_results: list[StandaloneRecord]
    service_job_ids: list[UUID]
    standalone_units_per_second: dict[str, float]
    pooled_units_per_second: float | None = None
    efficiency_percent: float | None = None
    speedup: float | None = None
    service_runs: list[dict[str, Any]]


class SearchStart(DTO):
    search_job_id: UUID
