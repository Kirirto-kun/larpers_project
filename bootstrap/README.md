# Verified user-space bootstrap

For the current Mac/Windows handoff, use an operator-prepared
[native connection package](connect/README.md). On Apple Silicon, run
`sh ./Connect.command` from the extracted folder; on Windows 11 AMD64, open
`Connect.cmd`. It starts `campus connect`, checks
the native runner/manifest, prepares selected approved local images and verifies
CPU or explicitly permitted GPU computation before enrollment. Recipients need
Docker Desktop but no Python or Go. See [CLI usage](../runner/CLI.md) for
`--check-only`, explicit CPU/GPU selection, saved reconnect and local Stop/Drain.
The [current local-server handoff](LIVE_SERVER.md) includes device steps,
operator restart commands and the observed physical-test status.

The LAN coordinator now runs upstream `96bfe6d` at
[https://10.4.146.76:8443](https://10.4.146.76:8443), using strict TLS and a private
client CA. Real local Go enrollment and ready heartbeat passed. The package CA
is client-local; no global trust change is needed. Private invitation packages
must not be published. The physical Apple M2 Mac passed CPU check-only, saved
reconnect and a server-observed LAN ready heartbeat through the `sh` route.
Its first Finder double-click was killed for an unconfirmed reason despite
matching hash and valid macOS signature checks. Physical Mac Ctrl+C returned
the expected exit130; rerunning the launcher restored the saved connection and
Serving. The Windows 11 AMD/RTX 5050 CPU/CUDA/LAN run remains to test; the
assignment deadline/quota safety gate remains.

## Alternative binary-download scripts

`join.sh` and `join.ps1` download one platform-specific Campus runner, verify its
SHA-256, keep it in a private user cache, and launch `campus join <invite>` with
the owner's additional arguments. They do not install Python/Go, elevate
privileges, change system services, install Docker/drivers, or enable autostart.

**No runner release is currently published by this repository.** Obtain the
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

Public HTTPS release hosting, published per-platform digests and native
Linux/macOS execution remain release/integration gates for these download
scripts. A local Go join against the running LAN coordinator has separately
passed; that does not establish a published-download-to-join path or a
distributed workload result.
