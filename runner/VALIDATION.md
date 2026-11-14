# Local validation and remaining acceptance gates

## Current public preparation and physical Mac result

Final R3 live acceptance: the actual Windows CMD launcher, inherited from a
PowerShell 7 session, reused the verified native client and CPU environment,
passed the real CPU probe and restored the saved device. Owner API observed
`ready` at `2026-11-14T12:48:32.469569Z`. `install --stop` then returned 0 and the
foreground CMD exited 0. The default CPU cache contained no CUDA archive object.
Strict-CA R3 catalog, six native client HEAD sizes, three exact starter bodies,
206 Range and private-file 404 checks passed. A scan of public metadata/clients
found none of the nine known private credential/invitation values.

Update on 2026-11-14: the current handoff is an invitation-free public starter
with a pinned download catalog, while private offline device ZIPs remain usable.
See [the LAN release status](../bootstrap/LIVE_SERVER.md) and
[public release format](../bootstrap/public/README.md). The current R3 release is
`bootstrap/test-kit/public/runner-20261114-r3`, served read-only at
`https://10.4.146.76:8443/downloads/runner-20261114-r3/`. Its exact starter
sizes/hashes and pinned catalog are recorded in the handoff. The three image
archives are reused verified hardlinks checked for identity and size; all 18
non-image release files were freshly hashed, without rereading all image bytes.
The preceding R1 release was verified through strict TLS at
`https://10.4.146.76:8443/downloads/runner-20261114-r1/`: HEAD for all six native
targets, exact bytes for all three starter files, HTTP Range 206, and 404 for
invitation, owner and runtime-file paths. This is a LAN handoff, not an Internet
publication or GitHub release.

All six native Windows/macOS/Linux amd64/arm64 clients were built with
`CGO_ENABLED=0`, `-trimpath` and `-buildvcs=false`. Native Windows amd64 CPU and
physical M2 CPU execution have passed; the native Linux installer and Unix
filesystem checks passed under WSL. Cross-building the other targets is not
physical acceptance. Windows ARM Docker Desktop is currently Early Access;
there is no ordinary phone, Apple Metal or ARM CUDA offering.

The public starter verifies and caches the selected native client. `install`
pins the catalog and selected environment hashes, requests GPU consent before
any CUDA download, resumes interrupted large files, and rehashes cached files
before reuse. New-device invitations are entered separately after successful
preflight. Docker Desktop/WSL/driver prerequisites remain owner setup.

| Prepared environment archive | Compressed bytes |
| --- | ---: |
| CPU linux/arm64 | 198349093 |
| CPU linux/amd64 | 282383826 |
| CUDA linux/amd64 | 4175747216 |

A real Go HTTP preparation test downloaded the full 4,175,747,216-byte CUDA
archive through Caddy using the strict pinned CA. It first cancelled after
8,388,608 bytes, then resumed with `Range: bytes=8388608-` and received exact
`Content-Range: bytes 8388608-4175747215/4175747216` with status 206. Final object
and prepared-bundle hashes passed. A third `Prepare` call with a transport that
denied every network request succeeded with **zero HTTP requests**, proving
verified payload cache reuse. This test neither enrolled a device nor executed
CUDA. Evidence is in
`.git/campus-runner/public-live-download/live-download-report.json`.

Fresh Go normal/race tests and vet passed. The latest R3 targeted public suite
passed **53 tests in 52.19 seconds** after the Windows module-path and repeat-cache
ACL corrections. It includes two actual non-administrator runs with protected
owner DACLs and no second client download, CIM discovery, parent module-path
restoration and exit codes 0/1/7/130. ACL updates use the DACL alone and require
no `SeSecurityPrivilege` privilege. The earlier full locked Python suite passed
**426 tests with 1 opt-in E5 skip in 220.34 seconds** before those launcher fixes;
that full-suite result is distinct from the final targeted regression run.

An actual native R2 Windows CMD launch downloaded the pinned client and CPU
environment from an empty cache, passed preflight and reached ready. The R3 CMD
launch from inherited PowerShell 7 then reused the verified client/environment,
passed CPU preflight, restored the saved identity and entered Serving. The
CPU-only cache contained no CUDA archive object. These runs verify the real
Windows launcher/runtime path; they do not establish physical Mac public-starter
execution or NVIDIA CUDA execution.

The physical Apple M2, 8 cores and 8 GiB RAM, has also completed a real
distributed CPU computation through the live API's explicit diagnostic overlay.
Job `f62d56ff-b5d0-4866-aa5c-d1e0a24f00a4` reached succeeded on node
`50b752cf-f13f-4f76-ae0c-0b23f7666d35`. Native Linux/aarch64 Python 3.11.14
fitted a quadratic model to 250,000 deterministic samples, reporting 447.717 ms
of calculation. The authenticated artifact download was `model.json`, 281 bytes,
SHA-256 `eb57995ae2440f334c6d8c1c8e9ef4ed41b5ed3ef29e759fa85ee43db93482f7`.
An independent exact-rational reference accepted maximum coefficient error
`4.0589753780295723e-13` and MSE error `4.6629367034256575e-15`.
The node returned to ready with zero active attempts.

This is a bounded CPU diagnostic under a device/image/adapter allowlist: 1 CPU,
256 MiB RAM, 64 PIDs, 34 MiB explicit tmpfs quotas and a 60-second hard bound,
with no network or GPU. Server verification is `structural`; the independent
numerical check is separate evidence. [Policy and physical result](../bootstrap/control_plane_compat/RESULT.md)
record the proof. General published assignments still fail closed when their
relative deadline and detailed sandbox policy are absent. Arbitrary-job
compatibility, two physical devices in one job, actual RTX 5050/CUDA execution,
GPU recovery and comparative performance remain acceptance gates.

## Connection validation recorded before public preparation

Update on 2026-11-14: upstream control-plane `96bfe6d` is deployed from an
isolated local snapshot with Docker Compose project `campus-live`. Its LAN
endpoint is [https://10.4.146.76:8443](https://10.4.146.76:8443), with strict TLS
and a private client CA. Real Go device enrollment and ready heartbeat passed
against this service. A physical Apple M2 Mac has now also passed CPU preflight
and saved reconnect, with a server-observed ready heartbeat over the LAN. This
supersedes earlier coordinator-absent and Mac-peer-pending statements. These
connection-only checks do not themselves establish workload execution; the later
restricted Mac computation is recorded above.

The [native connection package](../bootstrap/connect/README.md) now starts
`campus connect`, verifies its pinned files and local Docker endpoint, loads
selected prepared images when missing, and preflights before redeeming an
invitation. Saved identity reconnects without a second redemption. CPU is the
default; GPU requires explicit permission and a successful real CUDA calculation.
Separate cached Windows NVIDIA display names do not become usable compute GPUs.
`--check-only` performs local setup/checks without enrollment. Stop/Drain remain
local and foreground operation adds no autostart or global CA installation.

Fresh connection-source verification: full Go tests, race tests and vet passed.
The full locked CPU Python suite passed **374 tests, with 1 opt-in E5 skip,
in 273.26 seconds**. The real new Windows `connect` binary detected the host AMD,
passed the CPU container probe, restored the existing device identity and sent
a fresh ready heartbeat at `2026-11-14T11:13:00.883801Z`; local `connect --stop`
then ended the foreground process with exit 0. Regression tests and re-review
cover positional opaque invitation handling and immutable private metadata
snapshots during long probes/loads.

The private Mac ZIP is 208331587 bytes, SHA-256
`8789a2c7bb7b711aa9be9bda6ebc14f9d52fe9a3d5929d1f43a683ee26110c07`.
Payload hashes, launcher pins, ZIP CRC and Unix executable modes passed.
These are candidate binaries built with CGO0/trimpath from the tested working
tree based on `a694371`; their Go VCS metadata honestly records modified sources.
The first physical Mac Finder launch showed `zsh: killed`, before Campus output.
The transferred runner subsequently matched its pinned SHA-256, and macOS
`codesign` reported valid on disk and satisfying its designated requirement.
The Edge quarantine attribute was present. The initial Finder failure's cause
is unconfirmed. The original ARM64 Mach-O's signature bounds and all 2417 ad-hoc
SHA-256 page hashes also matched on the maker.

On the physical **Apple M2, 8 cores, 8 GiB RAM**, the tested route from the
extracted folder was:

```sh
sh ./Connect.command --check-only
sh ./Connect.command
```

The first command passed the real CPU probe. The normal connection printed an
observed offer of 2 CPU and 0.38 GiB RAM, restored the saved identity and entered
Serving. A strict-CA owner API read independently observed `MacBook Apple Silicon`
(`50b752cf-f13f-4f76-ae0c-0b23f7666d35`) as ready, OS `darwin`, architecture
`arm64`, logical CPUs 8, last seen **2026-11-14T11:24:04.648714Z**. This establishes
actual Mac CPU preflight, saved reconnect and LAN peer communication. A later
heartbeat was observed at 11:26:37 UTC before the physical Ctrl+C test. Ctrl+C
printed `campus: context canceled` and returned the expected exit **130**; rerunning
the same `sh` launcher restored the saved connection and entered Serving again.
The owner API then confirmed the same Mac ready after restart, last seen
**2026-11-14T11:27:23.242365Z**.
At this connection-only checkpoint, a distributed workload had not been tested;
the later successful restricted Mac computation is recorded above.

The private Windows ZIP was independently verified: **4468742936 bytes**,
SHA-256 `c68c0eda2f85276d8cf34cb67fa25ff7b18b523215424a15ac39024c6902c134`.
Its six payloads include CPU and CUDA images; payload integrity, ZIP CRC and
private ACL passed. That package verification does not establish execution on
the target Windows 11 AMD/RTX 5050 laptop.
See [the current handoff](../bootstrap/LIVE_SERVER.md) for device steps.

Discovery boundary verification for this connection work passed normal and race
tests, discovery/client vet, and Darwin ARM64/Linux AMD64 test cross-compilation.
The generated probe parent executed actual arithmetic and independently stopped
a timed-out child. The exact cached Windows CIM query also ran successfully on
this host, reporting AMD CPU/total RAM and no NVIDIA display device. These checks
alone do not establish physical RTX 5050 CUDA; the separate physical Mac result
is recorded above.

Still required: resolve the initial Finder-launch failure and run the current
package on the Windows 11 AMD/NVIDIA RTX 5050 laptop, verifying its LAN access,
CPU/CUDA probes and owner Stop. Production assignments still lack the agreed
relative `task_remaining_ms` and detailed sandbox quota policy; the
runner fails closed before executing such work. Two-device jobs, GPU checkpoint
recovery and comparative benchmarks remain unproven. Older binary hashes and
idle measurements below describe their recorded builds, not a new measurement
of the connection changes.

## Update after foundation publication

Upstream `codex/control-plane` subsequently appeared at `5a5bd29`, with shared
contracts/DTOs and a mock API. The earlier statement that contracts were absent
describes the initial pass below. A later coordinator is now deployed as recorded
above. See [the integration record](PROTOCOL.md) for exact boundaries/gaps.

Reconciled-source verification recorded before the connection work:

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
At that earlier recording, native Mac, actual GPU and physical multi-device
acceptance were deferred. Current Mac connection evidence is recorded above;
actual GPU and general distributed workload acceptance remain outstanding. The
later restricted physical Mac computation is recorded at the top of this file.

The earlier offline Mac kit is `bootstrap/test-kit/macos-arm64-foundation/`, built from
clean implementation commit `eac3ae0`. All five payload hashes, full gzip CRC/tar
stream (22 entries) and `bash -n check.sh` pass. Payload208175556 bytes; image bytes
are unchanged from the verified ARM runtime. SHA-256 of `SHA256SUMS`:
`04134046d412f71b642229c89b7b45c57bc9abd15afd2230d695ba4559a33d9f`.
The new final Windows binary's actual `campus doctor` also exits0 with healthy
Docker29.2.1, verified canonical CPU reference and no GPU. The initial kit and
measurements below remain historical evidence, not the current handoff.

## Initial local pass (before foundation)

Date: 2026-11-14. Branch: `codex/runner-cli`, based on `f1914dd`.
Scope: implementation-plan part 4 only. The owner requested all available
first-notebook checks now and deferred the physical Apple Silicon notebook test.
At that point there was no production control plane, generated shared DTOs,
portal or console implementation in this checkout. Fixture APIs below do not establish those
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

- Run the current [native connection package](../bootstrap/connect/README.md)
  on Windows 11 AMD/RTX 5050. Mac CPU preflight, LAN heartbeat, physical Ctrl+C
  with exit130 and subsequent saved reconnect have passed through the `sh` route;
  the first Finder-launch failure remains unexplained.
  The earlier [Mac readiness kit](../bootstrap/SECOND_LAPTOP.md) checks offline CPU readiness
  only. Neither receiver needs host Python or Go. Full workload guard/filesystem,
  GPU and remaining device-control behavior still need those devices. The later
  bounded physical Mac computation is recorded above. Generated
  kits and caches are Git-ignored.
- Complete the live coordinator's [worker safety contract](PROTOCOL.md),
  especially the relative `task_remaining_ms` bound, detailed quotas and
  authority/receipt semantics, then repeat full CLI/SDK/worker flows. The server
  and generated DTOs now exist; portal/console and external planner/LLM
  end-to-end integration remain unverified here.
- Once the safety contract is complete, run one map job across two physical
  devices; test disconnect/sleep,
  reassignment/fencing/cancellation, result downloads and campus TLS/LAN access.
- Use an actual supported NVIDIA GPU for CUDA probe/E5 tolerance, utilization,
  training and compatible checkpoint recovery. Apple Silicon CPU readiness
  supplies no CUDA evidence.
- Establish registered trusted references and comparable final-source standalone
  and pooled series before reporting speedup/efficiency or accepted credits.
  No formal distributed benchmark or full live-worker compatibility is claimed;
  the local and physical Mac ready-heartbeat results are recorded separately above.

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
Native Mac execution had not been observed at that historical export; the
current connection package's physical Mac success is recorded above. The
first-notebook final binary's actual
`campus doctor` exits 0 with healthy Docker29.2.1, CPU budget and no detected GPU.
