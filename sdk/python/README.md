# Campus Python SDK

Python 3.11+. The ordinary client and checkpoint helpers use only the Python
standard library. Install the optional exact foundation DTOs when needed:

```console
python -m pip install ./sdk/python
python -m pip install "./sdk/python[dto]"
```

```python
import os
import uuid
from campus import Client, NeedsInputError, SubmissionUnknownError

client = Client(os.environ["CAMPUS_ENDPOINT"], os.environ["CAMPUS_TOKEN"])
submission_key = uuid.uuid4().hex  # Persist before submission for recovery.

try:
    try:
        handle = client.submit(
            {"prompt": "Run the selected project", "accelerator": "cpu"},
            idempotency_key=submission_key,
            timeout=120,
        )
    except NeedsInputError as error:
        print(error.questions)  # One {question_id, prompt} object.
        # Obtain one explicit, owner-authored text answer before continuing.
        answer = input("Answer: ")
        client.answer_preview(
            error.preview_id, error.plan_revision,
            [{"question_id": error.questions[0]["question_id"], "value": answer}],
        )
        handle = client.submit_preview(
            error.preview_id, idempotency_key=submission_key, timeout=120,
        )
except SubmissionUnknownError as error:
    handle = client.resume_submission(
        error.preview_id, error.plan_revision, error.idempotency_key,
    )

print(handle.job_id)  # Persist this ID to reconnect.
job = handle.wait(timeout=3600)
directory = handle.download("./results")  # Must be a new directory.
```

`submit` accepts the published `PreviewRequest` dictionary. Its fields are
`prompt`, `recipe_id`, `project_artifact_id`, `inputs`, `selected_files`,
`share_with_llm`, `accelerator`, and `prepared_plan`; unknown top-level fields
fail before network I/O. A prompt alone does not upload local files. Projects
and inputs must reference artifacts already uploaded through the coordinator.
The SDK has no upload method. The server enforces the complete request schema;
for local schema validation, use the optional generated model:

```python
from campus.generated.api import PreviewRequest
request = PreviewRequest(prompt="Run selected project", accelerator="cpu")
handle = client.submit(request.model_dump(mode="json", exclude_none=True))
```

The generated module is a self-contained copy produced by foundation's own
`contracts/generate.py`; importing `campus` never imports Pydantic or server
code. The `dto` extra pins Pydantic 2.14.3, matching foundation's lockfile.

## Published contract

This SDK targets foundation commit
`5a5bd297dfc0c9fd704e408a929d56643042c4ba`. Its exact OpenAPI document and
scenario fixtures are stored in `tests/fixtures/foundation`, with original
SHA-256 hashes in `SOURCE.json`. The generated module records the same source.
The upstream production application was absent from that commit. Verification
against these published fixtures does not establish deployed-server readiness.

All routes begin with `/api/v1`. Constructor endpoints may be the origin or
end in `/api/v1`; other prefixes, credentials in URLs, queries, and fragments
are rejected. Requests use `Authorization: Bearer <credential>` when supplied.
Bearer authentication requires neither browser CSRF nor node session headers.

A lazy cached `GET /protocol` checks numeric `api_version`,
`min_worker_version`, and `max_worker_version`, each equal to `1`. The published
schema defaults these fields to `1`, so the client accepts their omission in
foundation's fixtures. Positive `lease_ms` and `heartbeat_interval_ms` and
nonnegative `long_poll_ms` are required. Old semantic runner-version fields
are rejected. The User-Agent is `campus/0.1.0`.

| Operation | Request and response |
| --- | --- |
| Create preview | `POST /job-previews` with `PreviewRequest`; `202 Preview` uses UUID `id` |
| Observe preview | `GET /job-previews/{id}`; `Preview` contains `state`, positive integer `plan_revision`, optional `question`, `plan`, and `plan_digest` |
| Answer | `POST /job-previews/{id}/answers` with `{question_id,plan_revision,answer}`; `202 Preview` |
| Run | `POST /jobs` with `{preview_id,plan_revision}` and `Idempotency-Key`; `201 Job` uses UUID `id` |
| Observe | `GET /jobs/{id}` returns `Job` |
| Logs | `GET /jobs/{id}/logs?cursor=0` returns `{entries,cursor,truncated}` |
| Cancel | `POST /jobs/{id}/cancel` with no body returns `Job` |
| Download | `GET /artifacts/{id}/content` returns immutable bytes; Range is supported |

Preview interim states are `inspecting`, `planning`, and `validating`.
`failed` and `cancelled` raise `PreviewError`. `needs_input`, or an explicit
question in a ready preview or its plan, raises `NeedsInputError`; the client
never invents an answer. Unknown states raise `ProtocolError` immediately.

`answer_preview` preserves the public one-entry `[{question_id,value}]`
convenience format while emitting the exact singular foundation request.
`value` must be a string of 1–2000 characters, `question_id` a valid foundation
identifier of at most 64 characters, and the revision a positive integer.
Multiple answers, JSON numbers/objects/null, and automatic string coercion are
rejected. Passing an answer `idempotency_key` is rejected because this contract
defines no idempotency guarantee for that operation. `submit_preview` observes
the same preview and runs its current ready revision; it creates no preview.

Only the job creation operation declares idempotency. Its key is 1–128
printable ASCII characters. After a transient or lost job ACK, retries replay
only the exact original preview/revision/key. The SDK performs no filtered
job-list lookup: foundation's `GET /jobs` has no key filter or idempotency echo.
Exhaustion or a malformed successful ACK raises `SubmissionUnknownError`
with the original coordinates. `resume_submission` replays those same
coordinates, without planning again or advancing the revision. The server
must enforce key/body idempotency for that replay to be safe.

Preview creation, answer mutation, and cancellation make one attempt because
foundation does not declare replay semantics for them. After an uncertain
preview or answer ACK, inspect coordinator state before making another
mutation; the SDK cannot safely recover a lost preview creation identity.
These missing replay guarantees are contract limits, not silently assumed
capabilities.

Reconnect with `get_job`, `wait`, `logs`, `cancel`, or `download` using a
persisted job UUID; observation never creates another preview or job.
`wait` returns a succeeded job, raises `JobFailedError` for `failed` or
`partial` (available as `error.state`), raises `JobCancelledError` for
`cancelled`, or raises `TimeoutError` without cancellation. Partial jobs never
publish an apparently complete download directory.

Logs use a nonnegative signed 64-bit integer cursor. Retain the returned
`cursor` for the next request; the server chooses the page size. Each entry
preserves its `cursor`, `attempt_id`, `sequence`, `occurred_at`, `text`, and
`gap`; `truncated` signals a gap in retained logs. Legacy `records`,
`next_cursor`, string records, and `message` aliases are not accepted.

A job's downloadable artifacts use exactly `id`, `name`, `size_bytes`,
`sha256`, and `media_type`. `name` supplies the relative output path and
`sha256` is lowercase unprefixed SHA-256. Wire aliases from the old provisional
contract are not accepted.

## Transfer and retry policy

HTTPS uses normal system certificate verification. HTTP is accepted only on
loopback. All redirects are refused, including artifact redirects, to prevent
forwarding bearer credentials. Exposed API error messages redact credentials.

Defaults are 3 attempts, 40 seconds per socket operation, exponential retry
backoff starting at 0.2 seconds plus up to 50% jitter, and 0.5 seconds between
polls. Safe observations and exact idempotent job replays retry transient
network failures and HTTP `408/425/429/500/502/503/504`. Certificate failures,
HTTP 401/403, and `STALE_ATTEMPT/LEASE_EXPIRED/SESSION_REPLACED` stop immediately.
JSON is bounded to 8 MiB and rejects duplicate keys, non-finite numbers, and
nesting beyond 64 levels. Preview and job observation timeouts bound socket
inactivity and are checked after bounded reads; they never issue cancellation.

Downloads default to at most 1000 files, 1 GiB total, and 1 GiB per artifact.
Override `max_download_files`, `max_download_bytes`, and `max_artifact_bytes`
explicitly. Invalid identity/digest/size, unsafe paths, and quota violations
are rejected before artifact bytes are fetched.

Paths must be canonical relative POSIX paths: no traversal, absolute paths,
backslashes, streams, controls, Windows device names, trailing dots/spaces,
case/Unicode collisions, file/directory collisions, or symlink/reparse
ancestors. Limits are 4096 UTF-8 bytes per path, 255 per component, and 64
components. Transfers stream to private sibling staging. Interrupted transfers
resume with Range; `206` must match the expected offset and full size. A `200`
response restarts that file. Content encodings are forbidden. Sizes and SHA-256
are checked, then the entire staging tree is verified before atomic no-replace
publication. Failures remove staging and leave the destination unpublished.
Staging is not persisted across calls. The destination parent should be
controlled by the caller; checks do not isolate a hostile same-user process
mutating the directory tree concurrently.

## Verification and regeneration

The SDK suite installs only its lightweight test/DTO dependencies, without
workload, PyTorch, or server dependencies:

```console
uv run --project sdk/python --no-sync --with pytest==8.4.2 --with pydantic==2.14.3 pytest sdk/python/tests -q
python sdk/python/tools/sync_foundation.py --source PATH_TO_PINNED_FOUNDATION_CHECKOUT
```

The regeneration tool verifies the recorded upstream hashes before running
foundation's generator and writes only within `sdk/python`. Tests exercise
real local HTTP sockets and filesystem publication, including exact published
fixtures/DTOs, request boundaries, unknown ACKs, bounded retries, checksums,
Range recovery, hostile paths, quotas, and fixture handler error reporting.
Actual coordinator integration and Mac/second-device execution remain release
gates.
