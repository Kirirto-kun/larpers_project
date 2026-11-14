# Campus Python SDK

Python 3.11+; runtime dependencies are the Python standard library only.

```console
python -m pip install ./sdk/python
```

```python
import os
import uuid
import json
from pathlib import Path
from campus import Client, NeedsInputError, SubmissionUnknownError

client = Client(os.environ["CAMPUS_ENDPOINT"], os.environ["CAMPUS_TOKEN"])
submission_key = uuid.uuid4().hex  # Persist before submission for recovery.

try:
    try:
        handle = client.submit(
            {"prompt": "Run the selected training project on the selected dataset"},
            idempotency_key=submission_key,
            timeout=120,
        )
    except NeedsInputError as error:
        print(error.questions)
        # Read explicit owner-authored answers; an absent file stops here.
        answers = json.loads(Path("answers.json").read_text(encoding="utf-8"))["answers"]
        client.answer_preview(error.preview_id, error.plan_revision, answers)
        handle = client.submit_preview(
            error.preview_id, idempotency_key=submission_key, timeout=120,
        )
except SubmissionUnknownError as error:
    handle = client.resume_submission(
        error.preview_id, error.plan_revision, error.idempotency_key,
    )

print(handle.job_id)  # Save this ID; observation may be disconnected.
job = handle.wait(timeout=3600)
directory = handle.download("./training-results")  # Must be a new directory.
```

The request dictionary is the coordinator's preview input, passed as JSON
without invented answers or additional execution settings. The example prompt
does not upload a project or dataset; those inputs must already be selected in
the coordinator request according to its eventual generated schema.

The canonical owner-authored `answers.json` format is shared with
`campus run --resume --answers answers.json`:

```json
{"answers":[{"question_id":"dataset","value":"selected-dataset-id"},{"question_id":"epochs","value":3}]}
```

Use the question IDs and values the owner actually selected. `value` is a JSON
value, including an explicit `null` when that is the owner's choice. The SDK
accepts the `answers` list from this document; it never chooses answers itself.
More questions raise another `NeedsInputError` and require another explicit
answer batch. No additional preview is created during refinement.

Reconnect by constructing a new `Client` and calling `wait(job_id)`,
`get_job(job_id)`, `logs(job_id, cursor=...)`, `cancel(job_id)`, or
`download(job_id, destination)`. These operations never create a preview or run.
`JobHandle` offers the same observation/download methods for its existing job.
`wait` returns the final job object, raises `JobFailedError` or
`JobCancelledError`, or raises `TimeoutError` without cancelling the job.
`cancel` explicitly requests cancellation; use `wait`/`get_job` to observe it.

`logs` returns one bounded page. Retain its `next_cursor` and pass that opaque
string unchanged to the next call; `None` means the server has no next cursor.
It is not an automatic infinite log follower.
Canonical records are objects containing `text`; metadata such as `sequence`
is preserved. For compatibility, the SDK converts old `message` objects and
plain strings to `text` records, dropping the old `message` alias. Malformed
records raise `ProtocolError`.

## Provisional wire contract and integration gate

**Foundation/server integration is still required.** This client uses the
approved plan's route table and runner-owned DTOs; it has not been tested against
a deployed coordinator or generated foundation contracts. All routes below
start with `/api/v1`. Constructor endpoints may be the origin or end in
`/api/v1`; other prefixes, credentials in the URL, query strings, and fragments
are rejected.

Before any API operation, a lazy, cached `GET /protocol` must return:

```json
{"api_version":"1","min_runner_version":"0.1.0","max_runner_version":"0.1.99"}
```

The SDK identifies as `campus/0.1.0`. It requires API version `"1"` and an
inclusive compatible runner version range. Requests use `Authorization: Bearer
<credential>` when a credential is supplied. The SDK does not log credentials,
and removes its credential from exposed API error messages.

Submission is exactly this sequence:

1. `POST /job-previews`, with the caller's request JSON and a stable
   `Idempotency-Key`, returns `202 {"preview_id":"preview-1"}`.
2. `GET /job-previews/preview-1` is polled until a terminal planning state:
   `{"preview_id":"preview-1","state":"ready","plan_revision":3}`.
   `plan_revision` is a nonnegative integer or a nonempty opaque string.
   Recognized interim states are `pending`, `queued`, `analyzing`, `planning`,
   and `preparing`; they are observed without executing anything.
3. `needs_input`, or a nonempty `questions` array even in a `ready` response,
   raises `NeedsInputError(preview_id, plan_revision, questions)`. No answer
   endpoint is called automatically. `failed`, `cancelled`, `expired`, and
   `rejected` immediately raise `PreviewError`; no run is submitted. A terminal
   response may omit `plan_revision`, in which case the exception's revision
   is `None`. Empty, unknown, or malformed states raise `ProtocolError` after
   that response, even when no observation timeout was supplied.
4. A ready preview produces `POST /jobs` with exactly
   `{"preview_id":"preview-1","plan_revision":3}` and the submission's
   `Idempotency-Key`. A successful response must contain `{"job_id":"job-1"}`;
   it normally also contains `"state":"queued"`.

To continue a needs-input preview, call
`answer_preview(preview_id, plan_revision, answers, idempotency_key=None)`.
It posts exactly this body to `POST /job-previews/preview-1/answers`:

```json
{"plan_revision":3,"answers":[{"question_id":"dataset","value":"selected-dataset-id"}]}
```

The answer list must have 1–100 entries, each containing exactly `question_id`
and `value`, with unique API question IDs (1–128 ASCII letters/digits/dot/dash/
underscore, starting with a letter or digit). The entire encoded POST body is
limited to 64 KiB, or the configured JSON limit if lower, and 16 levels of
nesting, counting its root at depth 0. Values must be finite JSON values with
string object keys. Invalid inputs fail before network I/O. The method returns
the answer receipt, which may be `{}`/`204` or contain a matching `preview_id`
and optional updated planning state/revision. It does not run a job.

Then call `submit_preview(preview_id, idempotency_key=..., timeout=...)` to poll
that same preview and submit its **current ready revision**. It never creates
another preview. For example, answers against revision 3 may lead to planning
and then ready revision 4; only revision 4 is sent in the subsequent job POST.

The server **must** persist idempotency for preview creation, answer mutation,
and job creation: identical key/body replays return the original identity or
receipt; a different body with the same key must conflict. Answer mutations
must also enforce `plan_revision` and reject stale answers or edits after job
submission. These are required even when an acknowledgement is lost.
Answer retries retain the exact canonical body and key. With no caller key,
the SDK derives `answers:<sha256>` from the preview ID and canonical answer
body; replaying the exact same batch/revision after reconnect derives the same
key. A caller may supply and persist an explicit key instead. After an unknown
answer ACK, replay only the original batch/revision/key; do not invent a new
batch or silently advance its revision.

The SDK reuses the original preview/revision/body/key for every run retry.
It never creates a new preview while retrying or resuming a run.

After an uncertain run ACK, the SDK optionally reconciles through
`GET /jobs?idempotency_key=<encoded-key>`. The provisional receipt shape is:

```json
{"jobs":[{"job_id":"job-1","state":"queued","idempotency_key":"original-key"}]}
```

Only a receipt that echoes the exact key is accepted; an unfiltered job list
cannot attach an unrelated job. Unsupported lookup responses (`400`, `404`,
`405`, `501`), malformed/unknown lookup DTOs, or transient lookup errors fall
back to the exact original idempotent POST. Thus this optional query is not a
prerequisite when the server implements normal POST idempotency. HTTP 401/403
and fencing errors still stop immediately. Exhaustion raises
`SubmissionUnknownError` containing the preview ID, revision, and original key.
Persist these and call `resume_submission` explicitly. That method first
reconciles and then, if needed, replays only that same run request. It does not
poll planning, answer questions, or refresh the frozen revision. Use
`submit_preview` for a preview that has not yet been submitted, including one
just refined with explicit answers; use `resume_submission` for an unknown job
POST acknowledgement.

Remaining response contracts:

| Operation | Request | Response |
| --- | --- | --- |
| Observe | `GET /jobs/job-1` | `{"job_id":"job-1","state":"running","artifacts":[]}`; terminal states are `succeeded`, `failed`, `cancelled` |
| Logs | `GET /jobs/job-1/logs?limit=1000&cursor=<opaque>` | `{"records":[{"text":"training"}],"next_cursor":"opaque-or-null"}`; `next_cursor` may be JSON `null` |
| Cancel | `POST /jobs/job-1/cancel` with `{}` and `Idempotency-Key: cancel:job-1` | `{"job_id":"job-1","state":"cancelling"}` or `204` |
| Download | `GET /artifacts/artifact-1/content`, optionally `Range: bytes=N-` | Raw immutable bytes, `200` or a matching `206`/`Content-Range` |

A succeeded job must contain every downloadable output in `artifacts`:

```json
{
  "job_id":"job-1",
  "state":"succeeded",
  "artifacts":[{
    "artifact_id":"artifact-1",
    "digest":"a2d42c4aa884e21216cbb8da4c7ba2fcf9b6033b2331666e666145c24caf7a38",
    "size_bytes":13,
    "path":"model.pt"
  }]
}
```

`digest` is a lowercase, unprefixed SHA-256 hex string. This mirrors
`runner/internal/client/wire.go`'s `Artifact` fields. Before foundation
integration, reconcile request schemas, the preview `state`/revision/questions
DTO, job/terminal-state DTOs, log pagination, cancellation receipts, artifact
authorization/Range behavior, and optional idempotency lookup against generated
contracts. The SDK currently does not implement project upload; explicit
answer/refinement entry and same-preview continuation are implemented above.

## Transfer and retry policy

HTTPS uses the system's normal certificate verification. HTTP is accepted only
for loopback development/test endpoints. Every redirect is refused, including
artifact redirects, so bearer credentials cannot follow a different origin.

Defaults are 3 attempts, 40 seconds per socket operation, exponential retry
backoff starting at 0.2 seconds plus up to 50% random jitter, and a 0.5-second
poll interval. Only transient network errors and HTTP
`408/425/429/500/502/503/504` are retried. Certificate failures, malformed JSON,
other HTTP failures, HTTP 401/403, and
`STALE_ATTEMPT/LEASE_EXPIRED/SESSION_REPLACED` are terminal. A malformed
successful run ACK is reconciled but is never blindly retried as a transient
response. JSON objects are bounded to 8 MiB and reject duplicate keys,
non-finite numbers, and nesting beyond 64 levels.

`submit(..., timeout=...)` and `submit_preview(..., timeout=...)` bound preview observation;
`wait(..., timeout=...)` bounds job observation and reduces the socket timeout
to the remaining observation budget. These timeouts never issue cancellation.
Socket timeouts are inactivity limits; a slowly trickling HTTP body is still
subject to the response byte limit and is checked against the observation
deadline when the bounded response read completes.

Downloads default to at most 1000 artifact files, 1 GiB total, and 1 GiB per
artifact. Change these explicitly with `max_download_files`,
`max_download_bytes`, and `max_artifact_bytes` on the constructor. Negative or
noninteger sizes, invalid identities/digests, and a manifest exceeding bounds
are rejected before artifact bytes are fetched.

Paths must be canonical relative POSIX paths: no traversal, absolute paths,
backslashes, streams, control characters, Windows device names, trailing dots
or spaces, or names that collide across case/Unicode normalization. Paths are
limited to 4096 UTF-8 bytes, 255 bytes per component, and 64 components.
File/directory prefix collisions, symlink/reparse ancestors, and linked staged
files are rejected.

Each artifact is streamed to a private sibling staging directory. Interrupted
transfers resume with `Range`; a resumed `206` must match the expected offset
and complete size. A server that responds with `200` restarts that file.
Encoding transformations are forbidden. Complete file sizes and SHA-256 digests
are checked, and all staged outputs are verified again before publication.
Only then is the whole new destination directory published using an atomic,
no-replace OS operation. A concurrently created destination is preserved.
Windows, Linux with `renameat2`, and macOS with `renamex_np` are supported by
that publication path; other platforms fail closed.

Failure removes staging and leaves the requested destination unpublished.
Range resumption occurs within the active download call; partial staging is not
retained for another process/call. The destination parent should be controlled
by the caller: these path checks do not claim to isolate an attacker running as
the same OS user and concurrently mutating the caller's directory tree.

## Verification

```console
uv run --project workloads --extra cpu pytest sdk/python/tests -q
uv run --project workloads --extra cpu pytest workloads/tests sdk/python/tests -q
```

Tests use real local `ThreadingHTTPServer` requests and real filesystem output.
They cover preview/run payloads, same-preview answer/refinement and revised
ready submission, terminal/unknown preview states, reconnect observation, explicit questions,
lost ACKs, stable keys, terminal errors, bounded retries/JSON, opaque cursors,
cancellation, partial transfers, SHA-256 failures, traversal/alias/quota checks,
symlink rejection, and atomic publication. They do not substitute for a real
coordinator run or cross-platform release testing. The HTTP fixture suppresses
only expected broken-pipe/connection-reset peer disconnects; all other handler
failures, including test assertions, are recorded and fail fixture teardown.
