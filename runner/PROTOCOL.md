# Published foundation integration

Foundation became available after the first local validation pass:
`codex/control-plane`, commit `5a5bd297dfc0c9fd704e408a929d56643042c4ba`.
Its committed `contracts/openapi-v1.yaml` is JSON and has SHA-256
`f6811226468f3dcba58e734654044b4710687b0e942f849430ee8b2411b1ace8`.
The unmodified upstream schema/fixture checker passes. This commit contains
DTOs, a generator and a deterministic mock API; that original commit did not
contain a production coordinator. Later upstream `96bfe6d` is now deployed
separately on the LAN at [https://10.4.146.76:8443](https://10.4.146.76:8443), with
strict TLS and a private client CA. A real local Go join and ready heartbeat
passed. A physical Apple M2 Mac subsequently passed CPU preflight and saved
reconnect through `sh ./Connect.command`; the owner API independently observed
its LAN ready heartbeat at `2026-11-14T11:24:04.648714Z`. Physical Ctrl+C then
returned the expected exit130, and relaunching restored the saved connection
and entered Serving again. This establishes that Mac peer's reachability
without claiming scheduling, transactional workload
acceptance or credits.

Go DTOs are generated into `internal/generated/api.go`; their provenance and
regeneration command are in `internal/generated/PROVENANCE.md`. The Python SDK
contains an exact generated DTO copy as an optional dependency and pinned
source/fixture snapshots. Its normal client remains stdlib-only.

## Implemented request boundaries

All routes are under `/api/v1`; HTTPS is required except on loopback. Redirects
are rejected. Bearer credentials stay scoped to member or device authority.

| Boundary | Published representation |
| --- | --- |
| Protocol | Integer `api_version`, `min_worker_version`, `max_worker_version`; omitted defaults are1. Required lease, heartbeat and long-poll durations are validated. CLI binary version remains0.1.0. |
| Worker session | `X-Node-Session` header; SessionCreate uses `protocol_version:1` and generated capabilities. Credentials/sessions return `id`. |
| Capabilities | Integer preflight revision; server Budget uses CPU/RAM/disk bytes, GPU slots and VRAM. Local diagnostics and detailed sandbox limits are kept separately. Only verified canonical repository image digests are advertised. |
| Preview | Actual PreviewRequest fields, UUID `id`, one optional `question`; answers are one `{question_id,plan_revision,answer:string}`. |
| Job submission | Frozen `{preview_id,plan_revision}` and the same Idempotency-Key after uncertain job acknowledgement. Unsupported job-list lookup is not used. |
| Logs/cancel | LogPage has `entries`, integer cursor and truncation indicator. Cancellation sends no invented body. |
| Artifact | `{id,name,size_bytes,sha256,media_type}`. Download still verifies safe paths, byte counts, Range and SHA-256 before atomic publication. |
| Upload | UploadCreate uses name/hash/size/kind/selected part size and optional nested attempt proof. Upload status has id/state/part_count/part descriptors. PUT checksums and retained bytes are verified. Finalize has no body. |
| Work poll | Generated WorkRequest contains only `wait_ms`; node authority is in the authenticated session header. |

No idempotency guarantee is published for preview, answer, credential redemption,
session creation or upload creation. Those mutations make one attempt. Unknown
acknowledgements remain explicit; a known preview may be read back, and only a
newer revision or terminal state resolves an unknown answer. CLI keeps its
durable submission state and safe never-submitted discard path. A file finalize
with a lost acknowledgement is reconciled through verified ready upload status.
Part PUTs can be retried with identical bytes and checksum.

The command CLI retains `campus run --gpu -- python train.py`. It submits the
literal command intent through the published planner API, then verifies the
ready single-stage plan's argv, adapter, working directory, bindings, network,
accelerator and declared outputs before creating a job. It never silently
accepts a substituted shell command. Actual planner/LLM end-to-end execution has
not been verified against the live deployment.

## Worker safety contract still required

The published Assignment is not sufficient for this runner's full execution
contract. In particular:

- It has absolute `task_deadline_at`, but no relative `task_remaining_ms`.
  The runner must not derive its hard limit from the laptop's wall clock.
- Resources has a single `disk_bytes`; the approved PID/workspace/output/
  checkpoint/tmp limits and precise GPU admission mapping need an agreed
  representation or named administrator policy.
- Assignment inputs use nested artifacts/mount bindings; outputs are structured
  declarations and validator policy is an identifier. The rich local execution
  model has different representations. Conversion must preserve binding,
  output quotas, checkpoint compatibility and registered validator authority.

`ClaimWork` fails closed with `ErrWorkerContractIncomplete` when the required
relative bound/policy is absent. A complete fixture validated by the actual
foundation Assignment DTO is checked by a worker regression: no `/started`
mutation or container creation occurs, and polling stops with the explicit
error. Safe container functionality demonstrated locally does not prove that
this published assignment can run through a real coordinator.

The typed attempt transport uses generated proof, completion, lease receipt and
checkpoint DTOs. Completion outputs reference finalized artifacts; local
quality/provenance diagnostics do not become server correctness claims. A lost
completion response is reconciled against matching attempt/generation and its
nested receipt before any retry. Renewals never extend the original local hard
bound. Checkpoint publication keeps the complete verified envelope and explicit
unknown-acknowledgement behavior.

Checkpoint compatibility is proposed as SHA-256 of the canonical sorted JSON
map containing exactly code/input/environment/trainer digest fields. Foundation
currently gives the field a SHA-256 type without defining that algorithm; this
must be agreed before cross-device pointer/resume interoperability is claimed.

## Measurement and adapter handoff

The foundation's telemetry body has sequence, optional CPU/GPU percentages and
memory bytes, and a phase. It has no raw measurement-window or stream-kind
fields. Wire percentages must use documented denominators and missing values
remain absent. Raw local samples/windows are distinct evidence; the current
schema does not prove their retention by a coordinator. Concurrent attempt
CPU/GPU senders share one monotonic sequence.

Local standalone manifests/records retain their original schema1 file binding
and output descriptors. These are distinct from server Artifact DTOs: changing
API names must not lose archive kinds or invalidate existing HMAC records.
Foundation StandaloneRecord represents one repetition and differs from the
local aggregate evidence export. Its config-digest definition, registered-node
authority, units, adapter-source identity and accepted comparison policy still
need coordinator agreement. No automatic import or formal benchmark claim is
made by a local record.

Foundation AdapterRequest has only operation/payload and AdapterResponse has
object result/string error. The current trusted adapter executable also binds
schema version and a static registered adapter ID/version, with richer error
details. The coordinator invocation must explicitly bind that trusted identity
and adapt the envelope; never infer a Python module/validator from user input.
The original foundation commit did not contain that invoker; its live
end-to-end behavior has not been established by the enrollment checks.

## Other project parts

`codex/portal-console` commit `874e0529ab2b83f8353b20be6968545f289b10c9`
still declares provisional UI models in its own integration handoff. Examples
include uploaded-part inventory, artifact digest/ready fields, preview question
collections and utilization windows. Those apps must consume their generated
foundation DTOs and be tested against the real API by their integration owner.
This runner branch does not overwrite their source, generated clients or locks.

See `VALIDATION.md` for actual local commands/results. Full acceptance still
needs an agreed live-worker safety contract, the Windows 11 AMD/RTX 5050 run,
two-device workloads, CUDA computation, trusted references and matched
final-source standalone/pooled measurements. Server availability and the tested
local and physical Mac connection paths are recorded above.
