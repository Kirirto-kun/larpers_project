# Campus CLI

Build with Go 1.25.6 or newer:

```powershell
go -C runner build -o ../campus.exe ./cmd/campus
go -C runner test ./...
```

The binary needs no host Python. Executing work needs a healthy Linux Docker
runtime and locally cached, approved Campus images containing the protected
guard, registered adapters and offline dependencies. Windows additionally needs
a working Docker Desktop/WSL2 installation. The CLI does not install drivers,
enable privileged services, download models, or run network package installation.

## Device participation

```text
campus doctor --profiles environments/resolved-profiles.json
campus join https://coordinator.example/join?token=INVITATION --no-serve
campus serve --profiles environments/resolved-profiles.json
campus serve --profiles environments/resolved-profiles.json --gpu
campus leave --drain
campus leave --stop
```

`join` accepts an invitation URL or a token with `--endpoint URL`. URLs support a
`token` query parameter, `#token=...`, a token fragment, or `/join/TOKEN`,
`/invite/TOKEN` and `/invitations/TOKEN`. A normal join continues into serving;
`--no-serve` only enrolls the installation. Enrollment is durable if subsequent
preflight fails: prepare the runtime, then use `serve`. There is no autostart.

The state directory comes from `--state-dir DIR`, `CAMPUS_STATE_DIR`, or the OS
user configuration directory's `campus` subdirectory. It contains private
`identity.json`, the local participation control, the process lock and the latest
submission recovery state. On Windows identity access is restricted with the
session package's owner ACL; on Unix it uses private permissions. Do not copy one
installation's state to another device. Join takes the process lock before
creating an installation ID, and serve holds it for its entire lifetime.

Join negotiates `/api/v1/protocol` before redeeming an invitation. Credential
redemption and node-session creation each send one POST per explicit command;
unknown acknowledgements are reported without an automatic mutation retry. Serving
registers a fresh node session, publishes capabilities, then enters the worker
loop. `leave` writes local control without requiring coordinator connectivity:
`--drain` finishes active attempts; `--stop` stops them. The device credential
remains available for a later explicit `serve`.

`doctor` emits JSON inventory and diagnostics. It returns failure when the
runtime, selected profile, real computation probe or configured coordinator
handshake fails. CPU/RAM participation uses discovery's conservative budget and
pauses for insufficient memory/disk or detected battery power. GPU resources are
offered only after `--gpu` and successful real CUDA computation. The present
probe addresses CUDA's default GPU; extra detected GPUs remain unoffered.

Profiles come from `--profiles FILE`, `CAMPUS_PROFILES`, a checkout's
`environments/profiles.json`, or `profiles.json` beside the installed executable:

```json
{
  "schema_version": "1",
  "profiles": [{
    "id": "python-ml-cpu-v1",
    "accelerator": "cpu",
    "platform": "linux/amd64",
    "image_digest": "campus/python-ml-cpu-v1@sha256:REPLACE_WITH_64_LOWERCASE_HEX_DIGITS",
    "guard_version": "1"
  }]
}
```

Use `cuda` for CUDA profiles. Participation publishes canonical OCI repository
digests `name@sha256:...`. A local
Docker content ID `sha256:...` is resolved through that cached image's own
`RepoDigests`; an image without one requires local preparation before serving.
`doctor` and `benchmark-local` can use the verified local ID without a repository
digest, including images loaded from an offline archive. Doctor reports missing
canonical participation metadata as a diagnostic. Resolution never pulls an image.
Only selected images whose architecture, guard
contract and computation probe passed are advertised or allowed for work. A null
digest marks an
unprepared profile and causes an actionable error when that profile is needed.
Image architecture and the guard contract are verified before probing; no
unapproved image is probed. Cross-architecture emulation is not enabled.

## Submit, inspect and retrieve work

A device invitation grants worker participation. Member operations require a
separate member credential in `CAMPUS_TOKEN` (or an explicitly provisioned saved
member credential). They never substitute the device credential. The coordinator
comes from `--endpoint URL`, `CAMPUS_ENDPOINT`, or the saved identity.

```powershell
$env:CAMPUS_TOKEN = "MEMBER_CREDENTIAL"
campus run --gpu --data ./dataset:/data --output model.pt -- python train.py
campus logs JOB_ID
campus fetch JOB_ID --to ./downloaded-results
campus cancel JOB_ID
```

`run` snapshots the current directory, or `--project DIR`, using the bounded
Campus packager. It respects `.campusignore`, excludes state and an explicitly
bound in-project dataset, and always filters common credentials, `.env`, keys,
Git metadata, virtual environments and symlinks. The project limit is 10,000
files/256 MiB; the separate dataset limit is 10,000 files/1 GiB. A dataset must be
a directory and only one read-only `/data` binding is supported. Splitting on the
last colon preserves Windows paths such as `C:\data set:/data`.

The program and each argument after `--` are encoded literally in a bounded
planner prompt, including spaces and Unicode. No host shell command is
constructed. Before submitting a ready plan, the CLI checks the exact argv,
working directory, generic adapter, archive bindings, accelerator, offline
network policy and every declared required output. A changed command is refused.
Repeat `--output RELATIVE_PATH` to declare outputs. The planner is also asked to
capture changed/new workspace files within the guard quota. Commands accept at
most 64 arguments of 4,096 characters each and must fit the complete
8,000-character planner prompt. GPU requirements never silently become CPU.

The CLI uploads a `project.zip` artifact and a separate `data.zip` artifact when
requested, asks for a preview, prints the ready plan and submits its revision.
An unresolved preview prints its ID, positive plan revision and single question,
and exits with status 3. It does not invent missing inputs or answers.

```text
campus run --resume
```

The latest preview request, exact command intent, preview ID, frozen plan
revision, request keys, pending explicit answer and job receipt are persisted in
`submission.json`. Resume reads a known preview. Preview creation has no
published idempotency guarantee: if its acknowledgement is lost before the ID is
known, resume does not repeat the mutation. `run --discard` can abandon that
never-submitted preview before a fresh run. Once job submission starts, resume
sends the exact saved
`POST /jobs {preview_id, plan_revision}` with the same key, even after an unknown
acknowledgement. It does not replan that submission. A second new run is refused
while a submission remains unresolved. Failed/cancelled/expired/rejected planning
previews persist a definitive terminal flag and permit a fresh run. An unknown
job acknowledgement always preserves its frozen request regardless of a preview's
subsequent state.

After a `needs_input` exit, answer the saved preview explicitly:

```text
campus run --resume --answers ./answers.json
campus run --resume --answer dataset=training-set
```

The canonical answers file contains one explicit string answer:

```json
{"question_id":"dataset","answer":"training-set"}
```

A one-entry array or `{"answers":[...]}` wrapper is also accepted locally. Use the
current question ID returned by the preview. `--answer question_id=value` supplies
the same string format; text must contain 1–2,000 characters. Multiple answers
and typed JSON values are rejected before network access. The CLI posts
`{question_id, plan_revision, answer}` once, against the positive current revision,
then inspects the same preview and submits its ready revision after checking the
command intent. An answer request/key is saved before transmission. After an
unknown acknowledgement, resume reads the preview without repeating the answer;
a strictly newer revision or terminal state resolves that uncertainty. If the
saved revision remains unchanged, the answer stays unresolved. A definitive 4xx
rejection (except rate limiting) permits corrected answers on that same preview.
New answers are refused after job submission starts.

```text
campus run --discard
```

Discard only marks a local never-submitted preview as abandoned. It makes no
network mutation and permits a new run. It refuses previews whose job submission
started or whose job receipt exists. An unknown submitted job must be reconciled
with `run --resume`.

Logs read `{entries:[{cursor,text,gap,...}], cursor, truncated}` with numeric,
strictly increasing cursors until an empty page, within a 4 MiB/1,000-page bound.
Reported retention gaps and truncation are printed to stderr. Fetch reads the
job's artifact list,
checks all paths, case aliases, file/directory conflicts and download staging
collisions before requesting bytes, then verifies every SHA-256/declared size.
It publishes a complete staged directory by rename. The destination must not
exist and its parents must contain no symlinks. Fetch is bounded to 10,000
files/1 GiB, and failed downloads do not expose the final destination.

All HTTPS certificates are verified. `--ca-file FILE` or `CAMPUS_CA_FILE` extends
the system CA trust. There is no insecure TLS option. Plain HTTP is accepted
only for loopback development endpoints. Credential-bearing redirects are not
followed.

## Foundation API boundary

The network DTOs follow foundation commit
`5a5bd297dfc0c9fd704e408a929d56643042c4ba` and its generated Go types.
`run` sends the published `PreviewRequest`, for example:

```json
{
  "prompt": "Prepare the exact JSON command intent carried in this bounded prompt...",
  "project_artifact_id": "00000000-0000-4000-8000-000000000001",
  "accelerator": "cuda",
  "inputs": [{
    "artifact_id": "00000000-0000-4000-8000-000000000002",
    "mount_path": "/data",
    "archive": true
  }],
  "share_with_llm": false
}
```

The endpoints are `POST /job-previews`, `GET /job-previews/{id}`, then
`POST /jobs {preview_id, plan_revision}`. Preview and job responses use `id`;
preview responses use a singular `question`. Revisions must be positive integers.
Planning states are `inspecting`, `planning` and `validating`; server terminal
states are `failed` and `cancelled`. Older saved terminal flags remain recoverable.
Unknown job acknowledgements retain the exact frozen request and stable
`Idempotency-Key`; this is local recovery behavior, not proof of production
exactly-once handling. Preview and answer mutations have no automatic replay.

Member upload creation uses `name`, `sha256`, `size_bytes`, `media_type`, bounded
`part_size_bytes` and `kind` (`project` or `input`), without worker attempt proof.
Upload responses use `id`, `state`, `part_count` and part descriptors. Finalized
and job artifacts use `{id, name, sha256, size_bytes, media_type}`; checksum and
size verification still precede publishing any downloaded directory.

The published foundation currently supplies DTOs, fixtures and a fixture server,
with no production coordinator entry point. The published assignment also lacks
the worker's relative task hard-deadline and detailed sandbox quota contract.
Worker execution fails closed at that boundary. Passing HTTP integration tests
establishes request/response compatibility, not live enrollment, distributed
execution, server-side idempotency or a completed two-device benchmark.

## Local baseline

```text
campus benchmark-local --manifest prepared-local.json --repetitions 3 --out baseline.json --profiles environments/resolved-profiles.json
```

This runs locally under the same Docker guard with no scheduler, upload or
coordinator request. It needs prepared local input files, cached models and an
approved immutable image. A GPU assignment additionally needs `--gpu`. It locks
the local installation against simultaneous serving. It does not export a
successful record when Docker or execution fails.

The version 1 local manifest uses this shape; replace all digest placeholders
with real lowercase SHA-256 values from the prepared recipe:

```json
{
  "schema_version": "1",
  "adapter_digest": "ADAPTER_SHA256",
  "model_digest": "MODEL_SHA256_OR_OMIT_FIELD",
  "batch": 1,
  "concurrency": 1,
  "assignment": {
    "manifest_digest": "WORKLOAD_MANIFEST_SHA256",
    "adapter": {"id": "generic.project", "version": "1"},
    "image_digest": "sha256:IMAGE_SHA256",
    "platform": "linux/amd64",
    "argv": ["python", "train.py"],
    "working_dir": "/workspace",
    "task_remaining_ms": 600000,
    "outputs": ["model.pt"],
    "network_policy": "none",
    "resource_limits": {
      "cpu_millis": 1000,
      "memory_bytes": 1073741824,
      "pids": 256,
      "workspace_bytes": 268435456,
      "output_bytes": 134217728,
      "checkpoint_bytes": 134217728,
      "tmp_bytes": 67108864,
      "gpu_ids": [],
      "vram_bytes": 0
    }
  },
  "inputs": [{
    "source": "prepared/project.zip",
    "artifact_id": "local-project",
    "digest": "PROJECT_ZIP_SHA256",
    "size_bytes": 123,
    "kind": "project",
    "path": "project.zip"
  }]
}
```

Local sources may be absolute or relative to the manifest. They are copied with
digest/size checks; traversal, symlinks and collisions are rejected. A `dataset`
input is a separately prepared ZIP extracted to read-only `/data`. Project ZIPs
must use `project.zip`. Runtime bounds are 1 second–24 hours, repetitions 1–100,
and only sequential `concurrency=1` is implemented. Resume checkpoints are not
used in a fresh baseline. Supplied digest values are claims checked against the
trusted registered adapter in the immutable image, alongside the full
configuration digest and verified input bytes. With a validator configured, a
separate bounded `validate_input` container runs before compute. Its
`manifest_digest` and trusted-source `adapter_digest` must match the record's
claims. A supplied model digest must match; an omitted model digest is derived
from this trusted preflight and frozen before measurement. Verification must
return that same actual tuple. Missing or mismatched provenance prevents an
eligible record from being exported.

An optional `validator` object enables actual registered-adapter verification:

```json
{
  "adapter": {"id": "training.image-classifier", "version": "1"},
  "manifest": {"schema_version": "1", "kind": "training", "...": "complete prepared workload manifest"},
  "policy": {"...": "fixed registered validation policy"},
  "output_paths": ["outputs/result.json"],
  "inputs": []
}
```

The validator must match the assignment's registered adapter/version. Supported
IDs are `embeddings.e5`, `training.image-classifier` and `generic.project`, version
`1`. Validator-only inputs (for example a full E5 reference) use the same local
input descriptor format and are mounted only for validation. The trusted JSON
adapter runs in a separate bounded, offline guard container using isolated
Python and the immutable `/opt/campus` adapter registry. Verified compute outputs
are copied into that container at their original `/outputs` or `/workspace`
paths, so the training evaluator sees its safetensors weights. Arbitrary
manifest-provided Python validator code is not imported.

`quality` and eligibility come from that real validator. Missing validators and
generic execution stay `unverified`; structural or sampled checks are never
promoted to full correctness. Each measurement separates container duration,
validator duration and other overhead. The record additionally reports separate
pre-compute `provenance_duration_ms` and actual trusted metadata. Registered
adapter source digests bind the fixed LF-normalized trusted source bundle, with
`adapter_digest_kind=trusted_source_lf_sha256`. E5's `model_kind` is
`pretrained_transformer` and binds its ID/revision/complete actual local file lock.
Training's `registered_training_recipe` digest binds its registered recipe,
effective validated parameters, classes and cuBLAS setting; trained weights have
their own output digest. Generic uses `model_kind=none`, an empty model digest and
never becomes benchmark eligible. Record metadata is checked again against the
post-compute verifier.

Durations use Go's monotonic clock; timestamps use UTC. Container duration
includes guard startup and output capture.
Utilization uses real Docker samples at one-second windows, so subsecond runs
can have no samples. GPU utilization is not fabricated when Docker has no such
metric. Each repetition uses a fresh process/container; first/subsequent cache
labels explicitly leave host/model/image cache temperature uncontrolled. This
does not measure an in-process warm model or claim flushed cold caches.

`StandaloneRecord` version 1 binds the device/config/manifest/model/image/adapter,
limits, batch, concurrency, measurements and output digests. A local registered
device credential adds HMAC-SHA256 over the record and signer metadata with the
signature value empty. This verifies credential origin and integrity only; it
does not attest hardware timings or correctness. Without one, the record is
marked `unsigned_local`. There is no automatic benchmark import command; a
coordinator must authenticate and apply its policy before importing the record.

Exit status: `0` success/help/version; `1` operational failure; `2` invalid usage;
`3` unresolved preview input; `130` context cancellation/deadline.

Real execution gates remain environment dependent. A passing Go suite uses HTTP
integration servers and explicit runtime boundary doubles; it does not establish
Docker isolation, CUDA execution, ML quality or a two-device benchmark result.
