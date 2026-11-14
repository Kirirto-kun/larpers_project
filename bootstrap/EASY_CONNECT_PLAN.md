# Simple Device Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task by task.

**Goal:** One native launcher detects and prepares a Mac/Windows laptop and
connects through an invitation, with truthful readiness and easy stop.
**Architecture:** Thin native launchers pin a common Go `connect` command;
operator-built offline packages carry immutable images and optional client CA.
**Tech Stack:** Existing Go1.25 CLI, shell/PowerShell, stdlib Python operator tool.
**Spec:** `bootstrap/EASY_CONNECT_DESIGN.md`, supplementing the approved pool spec.

## Global constraints

No host Python/Go on recipient; native darwin/arm64 or windows/amd64; GPU explicit
opt-in plus real probe; no shell eval, hidden admin install, autostart or global
trust/policy change. Keep image and file hashes, relative bounds and stop fences.

## Review focus

Wrong architecture/corrupt archive must fail before Docker changes. A remote
context must not advertise local hardware. An old joined state must reconnect
without reusing a one-time invite. GPU detection must not imply verified CUDA.
An invitation must not leak through terminal logs, public assets or error text.

## Task1: device/runtime detection (Windows subagent)

Own `runner/internal/discovery/**` plus additive local hardware fields in
`runner/internal/client/wire.go`. Expose CPUModel/CPUVendor/MemoryTotalBytes and
GPU.Name without changing generated server DTOs. Add
`RequireLocalDocker(ctx,Command,getenv) (string,error)` returning a local
unix/npipe endpoint; reject remote/unknown transports. Add `ProbeExec` with a
longer host timeout and an independent45s in-container probe bound.
Cache static inventory on the real default path so periodic discovery stays
lightweight. Keep non-NVIDIA display metadata separate from offered CUDA GPUs.

- [x] Boundary tests first: NVIDIA5050 present/unverified, missing driver,
  native Mac summary, remote/context-env precedence, bounded probe cancellation.
- [x] Observe failures, implement, run discovery tests/vet and cross-build.

## Task2: common verified connection (root)

Own new `runner/internal/onboarding/**`, `runner/internal/app/connect.go` and
connect tests; add App.Stdin, LocalDocker and LoadImage hooks and command routing.
Consume the exact manifest in the spec. Verify manifest/all payload hashes and
safe paths, inspect native target, pin local Docker endpoint, load missing
selected images, preflight, show budget, obtain explicit GPU consent, then join
or reconnect. Optional client CA/invite files are verified and private.

- [x] Test corrupt/wrong target/traversal/link payload rejection before side effects.
- [x] Test CPU/GPU choice, EOF, check-only, local endpoint pinning, saved identity,
  invite-origin mismatch and no token output.
- [x] Implement through existing join/serve and protected session primitives.
- [x] Verify package/app suites, race, real cached image preparation and live
  coordinator registration/telemetry/stop without claiming a workload run.

## Task3: package builder/native launchers (Mac subagent)

Own `bootstrap/prepare_connection_kit.py`, native launcher templates under
`bootstrap/connect/`, and `bootstrap/tests/test_connection_kit.py`. Inputs/manifest
are fixed by the spec. Pin launcher binary+manifest digests; no host-language
runtime beyond native shell/PowerShell. Export only verified immutable cached
images with named metadata when possible; bound streams and validate the saved
index identity. Publish a new output atomically; protect private invitation kits.

- [x] Test streaming/hash correctness, native binary selection, exact argv,
  Unicode folders, changed profiles/images, failure/atomicity and no public secrets.
- [x] Implement and run isolated builder/launcher tests on actual available shells.
- [x] Hand off actual Mac/Windows kit generation and platform-only verification.

## Integration

- [x] Fresh full Go/race/vet and Python suites; targeted real-container checks.
- [ ] Generate current native binaries and private Mac/Windows packages with CA
  and per-device invite; verify all published hashes and launcher syntax.
- [x] Record live server endpoint, expiry, restart/stop commands and actual gates.
- [x] Preserve separate local branch and defer native Mac/5050 observations until
  those devices run the package. Do not claim cross-device results from mocks.

First physical Mac launch reported `zsh: killed` before Campus output. Original
ARM64 Mach-O and its ad-hoc signature page hashes verify locally; diagnosis on
the target Mac is pending. Physical connection is not marked passed.
