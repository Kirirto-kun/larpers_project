# Campus Web Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Deliver the integrated web product with accounts, native device controls, AI jobs, statistics and administration.

**Architecture:** Reuse merged FastAPI/PostgreSQL control plane, React portal/console and Go workers. Extend durable account/device interfaces, align the real HTTP DTOs and deploy production builds to the existing HTTPS server without discarding state.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic/PostgreSQL, Go 1.25, React 19/TypeScript/Vite, Docker/Caddy.

**Spec:** docs/superpowers/specs/2026-11-15-web-platform-design.md

## Global Constraints

- Preserve existing campus-live data/credentials/TLS and connected physical devices.
- Member registration never grants admin; email alone never authenticates.
- Provider keys/passwords/invitations remain private; browser writes require CSRF.
- Profiles low/medium/max mean 10/50/90% with local availability/Docker/consent bounds.
- Browser connect requires a running native supervisor; never fabricate readiness.
- Actual job execution and artifact download must be verified against the live API.

## Review Focus

- Cross-user node/job/artifact access: ownership tests must deny it.
- Network loss or old clients during pause/resume: truthful pending/offline state.
- Mode reductions during active work: stop/drain safely before changing quotas.
- Duplicate account/email casing and login probing: normalized unique IDs/generic errors.
- AI unavailable or invalid plan: useful recoverable UI; no execution before review.

### Task 1: Accounts, owned devices, remote settings and statistics

**Files:** server/campus/{auth,models,dto,main,config,observability}.py; new account/settings modules as needed; server/migrations/versions/0004*; server/tests/test_accounts.py and test_device_settings.py.
**Interfaces:** POST /auth/register {name,email,password}; POST /auth/login email/password or legacy token; GET /auth/me identity plus email; POST /auth/account authenticated credential setup; GET /me/stats; GET /device/settings device bearer independent of node session; PATCH /nodes/{id}/settings {mode?,desired_state?}. Node includes desired settings and applied revision; capabilities optionally acknowledge controls. Default members see owned nodes, admin sees pool. No breaking device auth changes.

- [x] Write tests showing account signup/password login/normalization/CSRF/role boundaries fail before implementation.
- [x] Implement password hashing, migration, endpoints and owner activation credential setup.
- [x] Write isolation/settings revision/stats tests; implement durable settings and owner/member filtering.
- [x] Run tests against an isolated PostgreSQL schema and report exact results.

### Task 2: Portal and console product UI

**Files:** apps/portal/src and tests, apps/console/src and tests; keep builds independent.
**Interfaces:** Consume actual server DTOs and Task 1 interfaces; extend node types with settings. Use authenticated real uploads/previews/jobs/logs/results, not provisional fixture formats.

- [x] Add browser tests for signup/login and device empty/live states before UI behavior changes.
- [x] Build Russian account forms, overview/stats, device download/invitation/cards/mode controls and clear pending/offline states.
- [x] Align job upload/AI consent/preview questions/revision submission/status/logs/download and admin views with real contracts; add functional example job entry points supplied by integration.
- [x] Verify builds, responsive keyboard-accessible states and meaningful Playwright journeys.

### Task 3: Native web controls and profiles

**Files:** runner/internal/{app,worker,client,discovery,session} focused additions/tests; CLI docs as needed.
**Interfaces:** GET /device/settings returns revision/mode/desired_state under device bearer, no session; optional capability acknowledgement. New opt-in supervisor serves, survives remote pause and reopens session on resume; local leave/Ctrl+C wins. Pass an explicit opt-in worker protocol marker for rich assignments.

- [x] Write failing tests for profile fractions/limits, no GPU escalation, remote pause/resume/revisions and local stop precedence.
- [x] Implement bounded poll/supervision plus mode application and applied revision reporting without breaking legacy CLI semantics.
- [x] Run Go suite/race/vet as appropriate and provide reproducible binary build instructions.

### Task 4: Integrated workload protocol and executable job examples

**Files:** server/campus new worker protocol module + scheduler integration; catalog/workload adapters; contract tests; bootstrap publishing/deployment scripts as needed.
**Interfaces:** Versioned rich native assignments with explicit bounded quotas, relative hard deadline, input mapping, basename output compatibility and no bypass of fencing/verification. Preserve legacy allowlisted diagnostics. Supply simple CPU/CUDA examples through ordinary previews/jobs and AI-accessible trusted catalog.

- [x] Test ordinary new-client assignment validation/input/output execution and legacy diagnostic admission.
- [x] Integrate rich protocol with the current server; register real trusted adapters/images for supported work.
- [x] Verify real native job execution and returned artifact independently.

### Task 5: Deploy and verify the complete product

**Files:** infra deployment config/scripts/docs and recorded sanitized verification evidence.

- [x] Snapshot current database/artifacts/config privately before schema/API deployment.
- [x] Configure DeepSeek privately, build UIs/new native releases, preserve public downloads and migrate/recreate only required containers.
- [x] Run real browser signup/login/devices/stats/admin flows, real AI preview -> job -> physical execution -> artifact download, native settings controls and error/reconnect cases.
- [x] Perform independent final review, fix findings, document limitations/evidence and commit the complete branch.

## Execution ledger

Implementation is authorized by the goal. Independent frontend/backend/native ownership allows parallel implementation; root owns integration/deployment and reviews cross-boundary changes. No shared main.py/model edits by the runner or frontend agents.

## Delivery evidence

All planned product paths were exercised together on the existing deployment.
Final verification: server 106 passed; portal 14 passed; console 5 passed; full
Go suite passed; contracts/Ruff/startup boundary checks passed. Real RTX CUDA,
DeepSeek own-code execution and member-owned CPU contribution produced checked
artifacts. Test-only unfinished preview cleanup was corrected before the final
full server run. Independent backend/native/product reviews have no unresolved
material findings.

See [WEB_VERIFICATION.md](../../WEB_VERIFICATION.md) for exact job/result evidence,
validation scope and limitations, [WEB_PRODUCT.md](../../WEB_PRODUCT.md) for user
steps and [WEB_OPERATIONS.md](../../WEB_OPERATIONS.md) for repeatable startup.
Changes are retained on the dedicated branch; no remote push or base-branch merge
is part of delivery.
