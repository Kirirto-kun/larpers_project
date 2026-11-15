# Campus web product

## Intent and scope

Turn the existing coordinator, native runner and two web apps into one usable
LAN-hosted product. Users create an account with name, email and password, sign
in, connect their computers, choose their contribution level, submit work with
AI assistance and inspect results and statistics. Administrators see the whole
pool and manage devices/jobs. Deliver working software and end-to-end evidence,
not independent mock screens.

The user authorized implementation, branch integration and parallel agents.
Implementation choices below are engineering decisions within that request.
Admin email supplied by the user: jafarman2007@gmail.com. Passwords and provider
credentials must stay outside Git and outside frontend bundles.

## Existing evidence and approach

Both origin/codex/control-plane (5b2ce70) and origin/codex/portal-console
(874e052) are merged into codex/web-platform, preserving runner history ab84784.
The React 19/TypeScript/Vite portal and console already cover previews, work,
results and pool operations, but use provisional DTOs incompatible with the
server. The coordinator has durable PostgreSQL state, cookie/CSRF sessions,
roles, ownership, invitations, uploads, job scheduling and DeepSeek planning.
Native devices already perform real CPU/CUDA diagnostics.

Reuse these components and align their actual interfaces. Alternatives of a new
standalone website or a mock-only dashboard discard working infrastructure and
cannot meet real device-control requirements. Retain the established forest
green/cream identity, clear typography and accessible, responsive layouts.

## Account and permissions

- Registration creates only a member in the existing configured pool. Normalize
  email; enforce unique identity and bounded name/email/password input. Store
  salted, slow password hashes, never raw passwords.
- Login supports email/password plus the existing one-time invitation flow.
  Cookies remain HttpOnly/Secure/SameSite and writes require matching Origin and
  CSRF. Rate-limit entry endpoints and return generic login failures.
- Extend identity with email. An already authenticated user may set account
  credentials through /auth/account. This lets the existing owner set their
  password through a private one-time activation link. Email alone never grants
  admin. Preserve existing device/CLI credentials.
- Members list and control their own devices and see their own jobs/statistics;
  owner/admin can operate the full pool. Explicit admin guards protect console
  data and controls. No token or provider secret goes to public assets.

## Devices and contribution

The Devices page leads with one action: add this computer. It offers the tiny
public starter for Windows/macOS, displays three clear steps, creates a private
device invitation and allows copying it separately. First launch and GPU consent
are local actions; the browser cannot install or start a native process.
Windows Docker Desktop remains a manual prerequisite, as previously requested.

Each owned device card shows truthful online/offline/preflight/paused state,
hardware, last heartbeat, current contribution and active work. Controls are
Connect/Resume, Disconnect/Pause and finish-current-work (drain). Offline devices
remain offline; a browser command does not fabricate a heartbeat.

Modes: low (10%), medium (50%), maximum (90%) of locally available CPU/RAM,
bounded by Docker capacity and existing owner limits. Explain maximum is intended
for an idle plugged-in computer. A mode never enables GPU without local consent,
allows battery work without local consent, or reserves unavailable memory.

Server desired settings are durable per node with a monotonic revision:
GET /api/v1/device/settings authenticated by a device bearer credential, without
requiring a live session, returns {revision,mode,desired_state}. mode is
low|medium|max; desired_state is running|paused|draining.
PATCH /api/v1/nodes/{id}/settings accepts mode and/or desired_state under ownership
and CSRF checks. Node views include these desired settings plus the acknowledged
revision; UI distinguishes pending changes from applied changes.

The opt-in native supervisor survives remote pause, cancels/stops active work
before advertising new smaller quotas, and reconnects a session on resume.
Local Ctrl+C or local leave always ends participation and cannot be overridden
by later web commands. Old clients remain supported, clearly identifying that
remote configuration needs a client update. Commands are idempotent; transient
network errors do not silently increase contribution.

## Work and AI

Keep the reviewable preview-before-run model. Provide quick functional CPU and
CUDA examples, plus uploading code/data, describing the desired task and opting
in to share selected excerpts with DeepSeek. No user code is sent to AI without
consent. AI returns validated catalog-backed plans, never direct shell execution
on the coordinator. Show assumptions, compatible capacity, resource/time limits,
clarification questions and useful errors. Run only after the user reviews the
plan. Preserve upload checksums, idempotent submission, fenced attempts, logs,
progress, cancellation and authenticated output download.

General jobs must use an explicit versioned rich assignment protocol compatible
with native runner quotas/deadlines/artifacts. Preserve exact limited legacy
Mac/RTX diagnostic paths, but do not present their allowlist as universal worker
compatibility. Add tests for ordinary new clients and actual physical execution.

## Statistics and administration

User overview: owned devices online/total, contributed accepted units and time
when measured, own jobs by state, recent results and real usage history. Empty
states guide the next action and never invent activity. Admin overview shows
pool status, current devices/hardware/heartbeats, active tasks/jobs, errors and
real telemetry. Existing benchmarks/charts remain usable after DTO alignment.

## Delivery and validation

Use the existing campus-live PostgreSQL/artifact/TLS volumes. Snapshot state before
migration; apply new schema and serve both production UI builds on the same HTTPS
origin. Keep public installer downloads available. DeepSeek configuration is
private and validated against official current provider documentation.

Prove account registration/login/logout/CSRF/ownership, real dashboard data,
starter/invitation flow, native settings pause/resume/modes and GPU consent,
AI preview/review/job execution/result download. Run meaningful server/Go tests,
frontend build and browser flows including mobile/keyboard/error states. Record
actual live tests separately from mocked component fixtures. Full completion
requires every requested path to work together; missing physical access or a
failed provider call remains explicit unfinished work.
