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
and entered Serving again. A later restricted compatibility overlay enabled
that same physical Mac to complete a real distributed CPU diagnostic, including
accepted artifact publication and independent numerical verification. The
ordinary published worker contract remains incomplete as described below.

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

## Restricted live Mac diagnostic

The live API is built from a separate `control-plane-96bfe6d-compat` staging
copy; the pristine upstream snapshot remains unchanged. Its explicit
administrator policy allows only Mac node
`50b752cf-f13f-4f76-ae0c-0b23f7666d35`, the exact diagnostic adapter and native
ARM image. It translates assignments only for that authenticated device with
the exact `campus/0.1.0` user agent. Other clients keep the ordinary DTO, and
unsupported work is rejected before leasing.

The policy supplies a server-derived relative hard bound of at most 60 seconds,
1 CPU, 256 MiB RAM, 64 PIDs and 34 MiB of explicit tmpfs quotas, with no network,
GPU, inputs or checkpoint resume. The installed runner's monotonic lease guard
and independent hard bound remain active. A narrowly validated completion-name
translation retains the ordinary fencing, artifact ownership, integrity,
idempotency and verification paths.

Job `f62d56ff-b5d0-4866-aa5c-d1e0a24f00a4` reached succeeded on the physical M2.
Its native Linux/aarch64 Python task fitted a quadratic model to 250,000 samples.
The authenticated 281-byte `model.json` download matched SHA-256
`eb57995ae2440f334c6d8c1c8e9ef4ed41b5ed3ef29e759fa85ee43db93482f7`;
an independent exact-rational reference accepted its coefficients and MSE.
Server verification remains explicitly `structural`; that numerical evidence
is a separate client-side check. The node returned to ready with zero active
attempts. See [overlay policy](../bootstrap/control_plane_compat/README.md) and
[physical result](../bootstrap/control_plane_compat/RESULT.md).

This proves the restricted diagnostic path, not arbitrary-job compatibility,
general planner execution, a multi-device benchmark or GPU support.

## General worker safety contract still required

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

Outside the explicit diagnostic overlay, `ClaimWork` fails closed with
`ErrWorkerContractIncomplete` when the required
relative bound/policy is absent. A complete fixture validated by the actual
foundation Assignment DTO is checked by a worker regression: no `/started`
mutation or container creation occurs, and polling stops with the explicit
error. Safe container functionality demonstrated locally does not prove that
this ordinary published assignment can run through a real coordinator. The
allowlisted diagnostic succeeds because it supplies the missing bounded policy
explicitly; it does not weaken this failure mode for other work.

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
The original foundation commit did not contain that invoker. The restricted Mac
diagnostic establishes only its one allowlisted adapter path; general live
adapter/planner execution remains unverified.

## Other project parts

`codex/portal-console` commit `874e0529ab2b83f8353b20be6968545f289b10c9`
still declares provisional UI models in its own integration handoff. Examples
include uploaded-part inventory, artifact digest/ready fields, preview question
collections and utilization windows. Those apps must consume their generated
foundation DTOs and be tested against the real API by their integration owner.
This runner branch does not overwrite their source, generated clients or locks.

See `VALIDATION.md` for actual local commands/results. Full acceptance still
needs an agreed general live-worker safety contract, the Windows 11 AMD/RTX 5050 run,
two-device workloads, CUDA computation, trusted references and matched
final-source standalone/pooled measurements. Server availability and the tested
local and physical Mac connection paths and restricted computation are recorded
above. Public starter/download verification is tracked separately in
[VALIDATION.md](VALIDATION.md); redistributable packaging does not expand the
worker protocol's execution authority.
