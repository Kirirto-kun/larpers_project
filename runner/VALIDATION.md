# Local validation and remaining acceptance gates

## Update after foundation publication

Upstream `codex/control-plane` subsequently appeared at `5a5bd29`, with shared
contracts/DTOs and a mock API. The earlier statement that contracts were absent
describes the initial pass below. There is still no production coordinator.
See [the current integration record](PROTOCOL.md) for exact boundaries/gaps.

Current reconciled-source verification:

- Full Go tests and race tests pass; `go vet ./...` exits 0. All Linux/amd64
  package test binaries also pass under WSL when launched from their respective
  package working directories (required for testdata paths).
- Full locked Python CPU suite: **347 passed, 1 opt-in Docker E5 skip in142.67 s**.
  This includes193 SDK tests and actual upstream fixture/DTO validation. Pydantic
  is pinned as development tooling; normal SDK imports remain stdlib-only.
- The actual unmodified upstream `contracts/mock_server.py` runs locally through
  its locked Uvicorn/FastAPI environment. SDK ready submit/wait/log/cancel and
  needs-input/answer flows pass against it. The process is stopped afterward.
  Its handler does not validate requests or implement production state changes;
  separate strict DTO boundary tests cover outgoing shapes.
- Typed upload/proof/completion/log/telemetry transport passes the real Docker
  worker test, returning the same1563-byte model SHA-256 as below:11 renewals,
  1 log request,4 telemetry posts,1 committed completion and1 receipt lookup.
  Its assignment includes the local bounded-runtime extension; the real
  published Assignment safety gate is tested separately and intentionally blocks.
- Registered standalone training and new-container checkpoint resume pass again.
  The API Artifact migration initially lost the local dataset ZIP kind and
  failed provenance preflight. Isolating the local input descriptor fixed the
  same real standalone check (24.02 s). A separate review found old local HMAC
  records could lose output fields; an independent local output descriptor and
  a legacy signed-record regression preserve schema1 bytes/signatures.
- Final new five-minute idle measurement: **300 parent-observed native samples**,
  duration300000871100 ns, average **0.343749% of one logical CPU**, peak current
  RSS **22437888 bytes (21.40 MiB)**, owner Stop succeeds. CPU/RSS now come directly
  from the actual child PID, independent of API timing or telemetry DTO fields.
  Measured binary SHA-256:
  `da333ce332da976e0e7582764b2dd30f75802388212890e4384bce3e75b1a86d`.
  The prior0.415308% raw record is retained as `idle-before-foundation.json`.

Production worker execution still needs an agreed relative hard deadline and
bounded sandbox/compatibility policy. A complete Assignment validated by the
actual upstream model is rejected before `/started` or Docker Create. Adapter
invocation envelopes, formal StandaloneRecord provenance/config matching and
UI-generated DTO integration also remain unresolved with the other owners.
Native Mac, actual GPU and physical multi-device acceptance are still deferred.

## Initial local pass (before foundation)

Date: 2026-11-14. Branch: `codex/runner-cli`, based on `f1914dd`.
Scope: implementation-plan part 4 only. The owner requested all available
first-notebook checks now and deferred the physical Apple Silicon notebook test.
The repository still has no production control plane, generated shared DTOs,
portal or console implementation. Fixture APIs below do not establish those
components' compatibility or transaction semantics.

## Reproducible local checks

Run from repository root, with Go 1.25.6 and the locked Python CPU environment:

```text
go -C runner test ./... -count=1
go -C runner test -race ./... -count=1
go -C runner vet ./...
uv run --offline --project workloads --extra cpu python -m pytest -q --tb=short
```

Observed: all 12 Go test packages pass, race detection passes, and vet exits 0.
Python: **322 passed, 1 skipped in 152.52 s**. The skipped case is the explicitly
opt-in real pretrained E5 Docker test, which passed separately below. On Windows,
race detection needs a working GCC; quote the complete `CC` value when its path
contains spaces. One invocation with an unquoted compiler path failed before
compilation; the corrected complete run passed.

Linux/amd64 test executables were cross-compiled and all 12 packages executed
successfully under Ubuntu WSL, using native `/tmp` storage for temporary files.
Darwin/arm64 CLI cross-compiles with CGO disabled and `-trimpath`; compilation
does not establish native Mac execution. Additional real POSIX UID1000/65532
process checks pass for readable RO inputs, private supervisor state, exported
ownership and prompt rejection of a FIFO checkpoint manifest.

Boundary coverage includes snapshots and secret exclusions; Unicode argv and
paths; traversal/link rejection; interrupted artifact transport, corrupt cache
refetch and bounded digest reuse; lost completion ACK/receipt reconciliation;
credential/session scope; simultaneous budgets; blocked-renew Stop and
blocked-poll Drain; stale-attempt isolation; preview questions/answers, terminal
state recovery and frozen submission idempotency; bootstrap hashes/ACLs;
offline wheel resolution and network rejection; full-vector validation,
microbatch OOM retry coverage; false training metrics; and complete checkpoints.

## Real Docker and workload evidence

Docker Desktop was restored without factory reset, data pruning or deletion of
existing containers/volumes. The crashed backend's stale socket directories were
renamed to private recovery backups:

```text
%LOCALAPPDATA%/Docker/run.campus-recovery-20261114-150311
%LOCALAPPDATA%/docker-secrets-engine.campus-recovery-20261114-150311
```

The running Linux engine reports version 29.2.1. Only uniquely labelled test
containers were removed; shared images/volumes were preserved.

| Prepared profile | Actual local immutable image ID |
| --- | --- |
| CPU linux/amd64 | `sha256:c4c7fad9136b8817b9d599dc73c1398dc4ccaa93f03dbb9641d2f22f38310bdf` |
| CPU linux/arm64 | `sha256:3d7435b47960fcc183cd61a2350b5b3ca2e58fd5abfcf832b4563dad4eda55fb` |
| CUDA linux/amd64 | `sha256:d416742141559c0428ce0e41b5c94044dfd523522c61b0da09189e8f8dc48bb6` |

All three builds completed. ARM wheels were installed by a native builder with
explicit target tags; no x86 emulation/binfmt installation was used. Extracted
ARM Python and Torch extension ELF headers both report AArch64 machine 183.
The CUDA image imports Torch 2.9.1+cu128, reports compiled CUDA 12.8, and performs
the CPU arithmetic check under network-none/nonroot/read-only restrictions.
`cuda_available=false` on this machine: **no actual GPU computation was tested**.

For real runtime tests:

```powershell
$env:CAMPUS_DOCKER_PROFILE=(Resolve-Path environments/resolved-profiles.json).Path
go -C runner test ./tests -run TestDocker -v -count=1 -timeout 6m
$env:CAMPUS_E5_MODEL=(Resolve-Path environments/models/multilingual-e5-small).Path
uv run --offline --project workloads --extra cpu python -m pytest environments/tests/test_docker_e5.py -q
```

Observed on Windows/Docker Desktop and again through native Linux test binaries:

- A real worker executes a fenced HTTP-fixture assignment, automatically captures
  a new workspace `torch.save` model (1563 bytes), checks its uploaded SHA-256,
  and reconciles one lost complete ACK with a receipt. Actual engine/process
  inspection confirms UID 65532, no workload capabilities, no-new-privileges,
  read-only root/inputs/control, inaccessible supervisor state, denied PID1
  signals, no Docker socket and outbound connection failure.
- Without fresh valid publication, copied/enlarged sequence 1 cannot renew the
  lease. A real hanging workload and child receive TERM then KILL in about 3.2 s.
  Fresh sequence updates cannot extend the four-second hard bound. This models
  loss of runner publication; an actual host runner was not forcibly crashed.
- Registered CPU training uses 8 generated train images and 4 disjoint held-out
  images, 12 steps, safetensors weights and an independent guarded evaluator.
  Accuracy 1.0, loss 1.3113009345033788e-6, seven complete READY checkpoints.
  Weight SHA-256: `2540dfd6969c5e4cadb7c75bd0da37d18d84f9a64f30691d47deca2ee70652a0`.
  A fresh container resuming a verified read-only step 3 checkpoint reaches step 12
  with identical weights and metrics. This is a synthetic functional check,
  not a dataset-quality conclusion or a performance comparison.
- The actual cached multilingual-e5-small revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`, verified against upstream file hashes,
  passes the opt-in Docker test: 4 distinct items, 384 finite normalized values
  each, full reference comparison, aggregation, Unicode sources and search.
  A normalized but incorrect vector is rejected. Reference and outputs are
  generated locally for this functional test, not by a registered reference node.

The native Linux benchmark initially failed because `copytree` inherited 0555
from read-only inputs onto the validator's output root. Restoring write access
to the workload-owned destination root fixed the same real regression; the
registered standalone test then passed in 22.54 s. Native Linux checkpoint resume
also passed. Other actual-runtime findings fixed before these passes include
JSON null output declarations, Docker 29 local-log compression settings, writable
tmpfs ownership, nonce refresh independent of heartbeat, and bounded Windows
sharing retries. The independent source review's fencing, provenance, preview,
cuBLAS and checkpoint-reader findings have regression coverage.

## Idle measurement

```powershell
$env:CAMPUS_DOCKER_PROFILE=(Resolve-Path environments/resolved-profiles.json).Path
$env:CAMPUS_IDLE_SECONDS='300'
go -C runner test ./tests -run TestIdleRunnerNativeProcess -v -count=1 -timeout 8m
```

The test builds and launches a separate real CLI process, keeps its TLS fixture
in the parent process, records native runner CPU/RSS windows once per second,
and exercises owner Stop at the end. Raw data is written under
`runner/test-results/idle-runner.json`; it includes the measured binary digest.
External Docker/WSL/model memory is excluded from runner RSS. Separate shared
runtime snapshots are stored in `runner/test-results/runtime-overhead.json`;
they include preexisting processes/caches and cannot be attributed to Campus.
Inaccessible WSL CPU accounting is represented as null.

An initial five-minute measurement exceeded the CPU target at 3.1198% of one
logical CPU (peak RSS 22.22 MiB). Repeated Windows control-file reads were replaced
with directory notifications. A subsequent run exposed a transient Windows
sharing lock during owner Stop; bounded read retries and real Win32-lock
regressions were added.

The final complete run **passes both engineering targets**: 301 samples over
300848 ms, average **0.415308% of one logical CPU**, peak RSS **21999616 bytes
(20.98 MiB)**. Owner Stop succeeds. The measured binary's SHA-256 is
`50b6639d0be9a595b9310b3d0be204956e151846c7ea2ab7807131aaba5376bf`.
This is the tested source build before the final documentation commit; release
binary build metadata can change its digest. Targets are <0.5% and <50 MiB.

Separate 61-second shared-runtime observation: Docker Desktop/backend peak
RSS 490.16 MiB, WSL VM peak RSS 12.00 GiB; observed Desktop/backend CPU averages
47.21% of one logical CPU across those processes. WSL CPU accounting is unavailable.
These processes predate this task and include other workloads and cached memory;
these numbers do not measure Campus's incremental overhead. No Campus model is
resident during that snapshot; model steady-state RSS was not benchmarked.

## Deferred checks and handoff

- Run the prepared [Mac readiness kit](../bootstrap/SECOND_LAPTOP.md) on native
  Apple Silicon. It verifies the supplied image/binary and tiny CPU computation;
  no host Python or Go is needed. Native guard/filesystem behavior still needs
  that device. Generated kits and binary/model/image caches are Git-ignored.
- Obtain the real control-plane foundation, agree [v1 payloads](PROTOCOL.md),
  generate DTOs, and repeat CLI/SDK/worker flows against it. In particular agree
  the required relative `task_remaining_ms` bound and authority/receipt semantics.
  Portal/console and external planner/LLM integration are unavailable here.
- Enrol two physical devices and run one map job; test disconnect/sleep,
  reassignment/fencing/cancellation, result downloads and campus TLS/LAN access.
- Use an actual supported NVIDIA GPU for CUDA probe/E5 tolerance, utilization,
  training and compatible checkpoint recovery. Apple Silicon CPU readiness
  supplies no CUDA evidence.
- Establish registered trusted references and comparable final-source standalone
  and pooled series before reporting speedup/efficiency or accepted credits.
  No formal distributed benchmark or real-server compatibility is claimed.

Local implementation and these checks do not satisfy the original full pool's
two-device/CUDA acceptance conditions. The separate branch is preserved locally
for the user's deferred hardware check; it has not been merged or published.

### Actual Mac kit export

`bootstrap/test-kit/macos-arm64-ready/` was generated and independently rehashed.
The gzip archive was fully streamed through CRC/tar validation (22 archive
entries), and `check.sh` passes shell syntax validation. Payload total is
208085236 bytes; compressed image is 198348974 bytes. The bundled native binary
was built with `-trimpath`, CGO disabled and clean source commit
`cd975f3d675a4cdb8ad0a87b91e72c5033b632f7`.

Trusted handoff SHA-256 of `SHA256SUMS`:
`854e56f98ac161a4df7acf41f927eaf89ca975ee15e62981e78f9ad7d8a9b212`.
Native Mac execution remains deferred. The first-notebook final binary's actual
`campus doctor` exits 0 with healthy Docker29.2.1, CPU budget and no detected GPU.
