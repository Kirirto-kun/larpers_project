# Implementation review — 2026-11-14

Scope: the two UI workstreams, benchmark/report tools and submission materials from the main-branch plan. This is an implementation self-review with static inspection, TypeScript builds, browser behavior tests, report/driver tests, dependency audits and rendered UI inspection. It is not independent verification of a live physical pool.

## Findings resolved

- Fixed stale snapshot responses overwriting newer resource data; request sequence and unmount cleanup protect the current view.
- Kept one idempotency key for retries of the same Run body and rotated it for a changed preview revision. Edited inputs invalidate consent and the ready plan. Older preview revisions cannot replace newer ones through events.
- Added resumable upload checksum comparison; metadata alone no longer justifies skipping a server part. Expired/mismatched upload IDs can be retried with a fresh upload. Secret exclusions include case-insensitive environment files and common credential directories.
- Fixed native output verification to check actual downloaded bytes. A filename-only assertion would not establish a usable output.
- Isolated logs/search state when changing job routes, bounded client log retention, prevented duplicate log-page appends and stopped search polling after terminal status.
- Added global session-expiry handling, cleared local upload state after logout and removed invitation text after login. Server denial of an admin action remains explicit and does not create a local success state.
- Added CPU/RAM budgets, explicit GPU opt-in and disabled controls during pending contribution actions.
- Completed timing/utilization charts. Unknown values, zero, estimates and gaps remain distinct; utilization is not labelled pooling efficiency.
- Tightened comparisons: exact unique participant denominator; matching inputs/settings/digests/precision/mode/timing scope; result cache disabled; missing results rejected. Efficiency above 100% remains visible with a methodology flag.
- Added collector resume against saved coordinator/configuration/experiment ID. Existing output cannot be overwritten as a new experiment; redirects cannot forward its bearer credential to another host.
- Escaped HTML report content and neutralized CSV formula-like node labels. Updated and pinned frontend dependencies after resolving initial audit advisories and Tailwind build-tool mismatch.

## Requirement evidence

| Requirement | Authoritative local evidence |
|---|---|
| Separate React/TypeScript apps and builds | Each app has its own package.json/lockfile/router/config; both npm ci and production builds passed. |
| Portal home, contribution, files and disclosure | `pages/Home.tsx`, `Contribute.tsx`, `NewJob.tsx`, `UploadPicker.tsx`; mobile consent, secret exclusion and interrupted-upload browser tests. |
| Preview/questions/Run correctness | Revision, same-key retry, stale-preview, bound-answer and edited-input tests in `tests/user-journey.spec.ts`. |
| Progress, recovery, partial output, logs and own-code files | JobDetails/JobTimeline/ResultViewer plus partial/truncated, reload, SSE replay/reset and actual-download tests. Search follows the ordinary search-job API. |
| Pool visibility, attempts and authorization | Console Overview/Nodes/Jobs, Topology/AttemptTimeline; offline, member-role and server-denial browser tests. |
| Benchmark selection, comparison and exports | Benchmarks/ThroughputChart/MeasurementCharts and changed-concurrency test; raw sample/gap/missing-baseline tests; Python report tests. |
| Credits and costs | CostBreakdown labels accounting units, missing measurements and estimates; per-unit cost requires complete costs and positive accepted units. |
| Reproducible measurements | Collector saves config/origin/ID/snapshots/raw export; report emits JSON/CSV/Markdown/HTML. Driver tests cover HTTP-only requests, timeout, resume, origin/config safety and redirect rejection. |
| Submission package | `docs/submission/README.md` maps all seven brief materials. Product/business contain hypotheses instead of invented demand/prices. |
| Ownership and branch isolation | Changes confined to apps/portal, apps/console, tools/benchmark, docs/submission and the user-requested root to do.md. Main plan/spec files unchanged. |

## Validation results

- Portal: npm ci, TypeScript check/Vite production build, **10 Playwright scenarios PASS**.
- Console: npm ci, TypeScript check/Vite production build, **9 Playwright scenarios PASS**.
- Reports/driver: `python3 -m unittest discover -s tools/benchmark/tests -v`, **17 tests PASS**. The plan's `uv run --project server pytest ...` command is deferred with the absent server branch; these tests use unittest and are pytest-discoverable without added dependencies.
- Both frontend dependency audits: **0 known advisories** after pinned updates.
- Desktop portal, console and benchmark screen plus mobile task form inspected against the explicitly synthetic loopback API. No browser page errors; mobile form has no horizontal overflow. Screenshots are local QA evidence, not measured compute results.
- git diff --check: clean.

No remaining high-priority local defect was identified in this review. This does not establish production or physical-pool readiness.

## User-deferred evidence and integration

The user confirmed that no real API, inventory or benchmark data is available and instructed us to leave those items. Final contract adaptation, DTO generation and merging are assigned to the merge agent. Real-device performance, actual five-laptop inventory, >=2-node useful work, CUDA, fault injection, checkpoint recovery, external LLM behavior, user interviews and independent human usability are not claimed or marked as performed. See integration-handoff.md and the deferred checklist in to do.md.
