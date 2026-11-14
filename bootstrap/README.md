# Verified user-space bootstrap

The current handoff uses a small [public starter](public/README.md):
`Connect.cmd` on Windows or `Connect.command` on macOS/Linux. These files are
safe to forward; they contain public download metadata and an optional public
CA certificate, with no invitation, owner token or device identity. On Mac run
`sh ./Connect.command` in Terminal. Docker Desktop is a prerequisite on
Windows/macOS; recipients need no host Python or Go.

The starter detects native OS/architecture, downloads the exact pinned client
into a private per-hash cache, verifies SHA-256/size and starts `campus install`.
That client checks CPU/NVIDIA and local Docker, selects the matching approved
CPU environment, and asks explicitly before downloading CUDA or offering GPU
computation. Interrupted environment downloads resume; verified cached payloads
are rehashed and reused. An invitation is requested separately after preflight
for a new device. Saved identity reconnect needs no new invitation.
No global CA, service, autostart, driver or Docker installation is performed.

The [live handoff](LIVE_SERVER.md) gives the current LAN release status, device
steps and operator restart command. Native CGO-disabled clients are built for
Windows/macOS/Linux on amd64/arm64. Windows ARM Docker Desktop is currently
Early Access, and binary availability does not guarantee runtime readiness.
Ordinary phones and Apple Metal are not supported targets.
See [CLI usage](../runner/CLI.md) for `install`, `--check-only`, CPU/GPU consent,
cache selection, saved reconnect and local Stop/Drain.

The coordinator runs upstream `96bfe6d` with the restricted diagnostic overlay at
[https://10.4.146.76:8443](https://10.4.146.76:8443). TLS and hostname checks remain
enabled; the included public CA is trusted only by Campus. The `10.4` endpoint
is a LAN address, not an Internet release. Real Windows amd64 CPU connection and
physical M2 CPU connection have passed. The Mac also completed a real bounded
distributed CPU model-fitting job whose downloaded artifact passed independent
numerical verification; [evidence](control_plane_compat/RESULT.md) explains its
explicit allowlist and policy. General arbitrary-job compatibility, two-device
benchmarks and actual RTX 5050/CUDA execution remain separate gates.

## Alternative offline device package

An operator-prepared [native connection package](connect/README.md) keeps the
client and image archives together for offline transfer and starts
`campus connect`. Existing private ZIPs remain usable. A package containing an
invitation is private to its device and must not be hosted in the public release.
The physical Mac's tested package entry point is `sh ./Connect.command`; its
initial Finder kill remains unconfirmed. Do not disable macOS security.

## Alternative binary-download scripts

`join.sh` and `join.ps1` download one platform-specific Campus runner, verify its
SHA-256, keep it in a private user cache, and launch `campus join <invite>` with
the owner's additional arguments. They do not install Python/Go, elevate
privileges, change system services, install Docker/drivers, or enable autostart.

**These older scripts have no default release URL or checksum.** Obtain the
release base URL, the exact binary SHA-256 for your OS/architecture, and invite
from the trusted administrator/release channel. All three are required inputs;
these scripts contain no default release URL or invented checksum. A checksum
downloaded from an untrusted source alongside a binary is not a trust anchor.

## POSIX shell

Requires a POSIX shell, `curl`, `sha256sum` or `shasum`, and normal POSIX tools
(`uname`, `mktemp`, `mkdir`, `chmod`, `mv`, `rm`, `id`, `ls`, `awk`, `grep`, `tr`).
No host Python or Go is required.

```sh
sh bootstrap/join.sh \
  --release-base-url "$CAMPUS_RELEASE_BASE" \
  --sha256 "$CAMPUS_RUNNER_SHA256" \
  --invite "$CAMPUS_INVITE" \
  -- --endpoint "$CAMPUS_ENDPOINT"
```

`--cache-dir DIR` optionally selects another directory owned by the current
user. The default is `${XDG_CACHE_HOME:-$HOME/.cache}/campus/bin`. Bootstrap
options come before `--`; all arguments after it go directly to `campus join`.
Spaces, Unicode, punctuation, and shell-looking text remain literal arguments.
Do not add an `eval` or concatenate a command string around this invocation.

## PowerShell

Use Windows PowerShell 5.1 or PowerShell 7 with its normal certificate trust
store. No host Python or Go is required. Pass additional runner options as a
string array:

```powershell
$joinOptions = @('--endpoint', $env:CAMPUS_ENDPOINT)
& ./bootstrap/join.ps1 `
  -ReleaseBaseUrl $env:CAMPUS_RELEASE_BASE `
  -Sha256 $env:CAMPUS_RUNNER_SHA256 `
  -Invite $env:CAMPUS_INVITE `
  -JoinArguments $joinOptions
```

`-CacheDirectory DIR` overrides the default `%LOCALAPPDATA%\campus\bin` on
Windows. The script protects that directory's ACL and grants inherited access
only to the current user. Existing directories must be owned by that user.
PowerShell on Linux/macOS uses the XDG/user cache and mode `0700`; the POSIX
script is the primary entry point for those platforms.

## Download and execution policy

Runtime OS and native architecture select these release names:

| Platform | File |
| --- | --- |
| Linux amd64/arm64 | `campus-linux-amd64`, `campus-linux-arm64` |
| macOS amd64/arm64 | `campus-darwin-amd64`, `campus-darwin-arm64` |
| Windows amd64/arm64 | `campus-windows-amd64.exe`, `campus-windows-arm64.exe` |

The selected filename is appended to the supplied release base URL. Other
architectures fail before download. There is no automatic x86 emulation.

- Release URLs require HTTPS and a host, and reject credentials, queries,
  fragments, whitespace, and backslashes. Use a directly served release URL.
- Normal TLS certificate validation remains enabled; TLS 1.2 is required.
  Every redirect is refused, including a redirect to the same origin. The
  response must be HTTP 200.
- Downloads have a 120-second request limit. The shell also sets a 20-second
  connection limit and a 128 MiB curl file limit. PowerShell rejects files over
  128 MiB after download, before hashing/publication/execution.
- A private temporary file is hashed before it becomes executable or is
  published to the cache. Mismatch/error removes that temporary file and never
  launches the downloaded payload.
- Cache entries include OS, architecture, and expected digest. Each reuse
  hashes the cached file again. A tampered entry is refused; remove that entry
  and retry to download a fresh copy. Cache paths containing symlinks/reparse
  points are rejected, and the POSIX cache uses mode `0700` with `umask 077`.
- Launch uses an argument array: `binary`, `join`, `invite`, then the explicit
  additional options. The runner's exit code is preserved. The bootstrap never
  prints the invite or constructs shell code from it. The launched runner owns
  its own output/logging and must retain its normal credential-redaction rules.

These scripts join using the runner's normal consent/resource defaults. Any
GPU opt-in or other join setting must be supplied explicitly as a runner
option. Runtime preflight still belongs to `campus doctor`/the runner; bootstrap
does not silently provision privileged prerequisites.

## Behavioral verification

```console
uv run --project workloads --extra cpu pytest bootstrap/tests -q
```

Tests execute the scripts as real processes. They replace only the download
boundary (a `curl` executable for the shell and an `Invoke-WebRequest` function
for PowerShell) and use real files, checksum tools, OS permissions/ACLs, argv,
and child execution. A wrong trusted digest must leave the execution marker
absent. Other cases cover literal Unicode arguments, redirect refusal, invalid
inputs/architectures, cached-file tampering, linked caches, and runner exit
codes. Windows tests exercise a real native executable and both available
PowerShell editions; shell tests use Git Bash on Windows.

The new public starter/catalog route is the primary LAN handoff; its verification
is recorded in [VALIDATION.md](../runner/VALIDATION.md). An Internet release and
physical acceptance of every target remain separate gates. The tests above
describe the older binary-download scripts; they do not establish every
public-starter-to-enrollment flow or general distributed workload compatibility.
