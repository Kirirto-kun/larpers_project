# v1 foundation handoff

The route groups and required Assignment/Capabilities fields follow the existing
implementation plan. The control-plane owner has not published foundation yet;
these are proposed detailed payloads, exercised through actual HTTP/TLS boundary
tests. They must be agreed against foundation before the branches are integrated.

## Common rules

All API calls use `/api/v1`, snake_case JSON, SHA-256 digests and durations in ms.
Times are UTC RFC3339. Production IDs are UUIDs; fixture IDs are readable tokens.
Version handshake precedes invitation redemption. `Authorization: Bearer ...`
and `X-Node-Session-ID` supply identity. Attempt mutations carry attempt_id,
generation and lease_token in the body; upload part requests carry the equivalent
X-Attempt-ID/X-Generation/X-Lease-Token headers. Receipts are queried after an
uncertain complete response before any application-level complete replay.

Protocol response: `{api_version:"1", min_runner_version:"0.1.0",
max_runner_version:"0.1.99"}`. Production must publish its actual compatibility
range. Errors use the plan's `{code,message,field_errors,retryable,request_id}`.
Authentication/session failures and stale attempts stop their respective work.

## Relative deadlines

Assignments include the plan's absolute server times plus `lease_remaining_ms`
and **task_remaining_ms**. Renew responses repeat both relative durations plus an
optional control. This additional relative task bound is required to avoid using
the laptop's wall clock for the hard task deadline. RTT and a 250 ms safety margin
are subtracted; confirmed renewals cannot extend the original hard task bound.

## Artifacts and execution

An artifact reference is `{artifact_id,digest,size_bytes,path,kind?}`. Paths are
safe relative POSIX paths. Project snapshots bind to `project.zip` (`kind=project`);
one separately uploaded dataset archive (`kind=dataset`) is safely unpacked and
mounted read-only at `/data`. Other inputs live read-only under `/inputs`. A resume
artifact is a ZIP checkpoint envelope whose explicit code/input/environment/trainer
binding is supplied as `validator_policy.resume_binding` and checked before use.

POST uploads uses digest, size_bytes and optional attempt fence, returning
`{upload_id,part_size_bytes}`. GET upload repeats those fields and completed_parts
(zero-based integers). PUT parts supplies X-Content-SHA256. Finalize returns an
immutable artifact reference only after storage checksums succeed. Artifact GET
supports Range and exact Content-Range for interrupted downloads.

Complete supplies `{attempt_id,generation,lease_token,result_manifest}`. The result
has schema_version, files, quality, benchmark_eligible, compute_duration_ms and
overhead_duration_ms. Initial generic quality is unverified; the server's isolated
validator decides acceptance/correctness. Attempt status returns state/receipt_id.
Checkpoint publication finalizes its ZIP first, then sends artifact, binding and
manifest_digest through the fenced checkpoints endpoint. No local READY marker
alone is a server-published checkpoint.

Resource limits are cpu_millis, memory_bytes, pids, workspace_bytes, output_bytes,
checkpoint_bytes, tmp_bytes, gpu_ids and vram_bytes. CPU/GPU queues share one budget.
Only locally approved immutable image IDs/digests, a verified guard label, a native
Linux architecture, and network_policy=none are admitted. Models and adapters have
separate version/digest identity; a model cache never supplies benchmark answers.

## User and observer payloads

CLI.md describes the preview submission request and persisted idempotency state.
The SDK accepts the same JSON preview request rather than constructing another
planner protocol. Both consume `{job_id,state,artifacts:[artifact reference]}` and
cursor log pages; the exact log record fields must be shared by all clients.

The JSON adapter executable uses versioned AdapterRequest/AdapterResponse, a
static registered adapter ID/version and operation/payload. Full request formats
and validator-only reference boundaries are in workloads/README.md. Heavy ML code
is not imported into the API process and user validators are never imported.

StandaloneRecord and benchmark import schema/scopes remain an explicit foundation
handoff. Preserve manifest/model/image/adapter/resource/quality equivalence and
the distinction between container computation, validation and overhead timings.
Only a real registered validator may mark a record eligible for formal correctness
throughput; arbitrary caller labels cannot establish provenance.
