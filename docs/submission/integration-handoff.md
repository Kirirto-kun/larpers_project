# Integration handoff

Work branch: `codex/portal-console`. Plan source: main, `f1914dddd8324cf8a63506fb6e7085b567f08356`. The user assigned final branch compatibility and merging to a separate agent. No server, runner, root configuration, root README or authoritative contract was changed in this work.

## Delivered paths

- `apps/portal/`: Home, Contribute, NewJob, JobDetails; browser session/login; upload disclosure; resumable checksummed uploads; planning questions; revision-fenced Run with stable retry idempotency; task events, logs, search and outputs.
- `apps/console/`: Overview, Nodes, Jobs, Benchmarks; role-gated actions; attempts/digests; matched throughput comparison, timing and CPU/GPU charts; cost assumptions and internal credits.
- `tools/benchmark/`: HTTP-only collection, safe saved-ID resume, strict matching/report export and tests. No new server dependencies.
- `docs/submission/`: all six planned submission documents, raw-measurement directory, index and review.

## Connect to the final contracts

Both apps use relative `/api/v1` requests, cookie sessions and `X-CSRF-Token`. `src/lib/types.ts` contains provisional UI models because foundation is absent on main. Adapt `src/lib/api.ts` and models to the generated foundation DTOs; generate ignored `src/generated/` before build/test using the final generator. Do not hand-edit generated output or copy one application's lockfile over the other.

Serve `apps/portal/dist/` at `/`, `apps/console/dist/` at `/admin/`, and the API at `/api/v1/`, with separate SPA fallbacks. Vite development proxies use `CAMPUS_API_URL` (default loopback port 8000). HTTPS, cookie flags, CSRF validation and server authorization are control-plane responsibilities.

Mutations use the paths in the main plan. The portal assumes auth/login and auth/me provide a session with name, role and csrf_token. Upload status includes part_size_bytes, uploaded_parts and optional part_checksums keyed by part number. A recorded part is skipped only when its checksum matches the selected file; absent checksums cause idempotent resend. Part data carries X-Content-SHA256. Changed part content clears the saved upload ID and requires a new upload. The server must validate archive paths, secrets, quotas, checksums and final manifests independently.

Preview records supply state/revision/questions and user-facing execution summaries. Answers carry question_id and plan_revision. Job creation carries preview_id/plan_revision and Idempotency-Key; the same request retry keeps its key, while changed revisions receive a new key. SSE uses event_id and cursor query replay, with reset triggering a fresh snapshot. Unexpected disconnects refresh and reconnect.

The benchmark UI expects server-accepted rates/configuration, not client-generated counters. Raw utilization samples are elapsed_ms/cpu_percent/gpu_percent; gaps remain visible, with reported averages distinguished from missing data. Report-tool raw export requirements are documented in `tools/benchmark/README.md`; map the final server export there. Do not mix fault-injection/normal, cold/warm, different timing scopes, units, precision or workload digests.

## Intentionally deferred by the user

No real coordinator URL, benchmark exports or inventory were available. Leave the physical inventory, actual raw reports, demand interviews, human usability check and integrated campus demo pending. They require real evidence; do not populate them from the synthetic fixture API. On the final merged SHA, repeat the two physical integrations, >=2-node map job, CPU disconnect recovery, own-code model download and compatible GPU checkpoint resume, then collect formal matched repetitions and update results/inventory.

The development-only `apps/portal/dev/fixture_api.py` binds loopback and identifies synthetic data in the session and reports. Never deploy it or use it for performance claims. Production app code does not import test fixtures.
