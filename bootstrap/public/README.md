# Public release starter

The operator creates a new release with `prepare_public_release.py`. Inputs are
fresh native CGO-disabled Go clients, resolved immutable profiles, approved cached
Docker gzip archives and an optional **public certificate**. No private device
folder or invitation is an input. The builder never invokes Docker, exports an
image, recompresses an archive or follows an input symlink/reparse point.

```powershell
workloads/.venv/Scripts/python.exe bootstrap/prepare_public_release.py `
  --binaries bootstrap/test-kit/public-binaries `
  --profiles environments/resolved-profiles.json `
  --cpu-amd64-archive bootstrap/test-kit/windows-connect/images/cpu.tar.gz `
  --cpu-arm64-archive bootstrap/test-kit/mac-connect/images/cpu.tar.gz `
  --cuda-amd64-archive bootstrap/test-kit/windows-connect/images/cuda.tar.gz `
  --ca-file PUBLIC_CA_CERTIFICATE.pem `
  --endpoint https://COORDINATOR:8443 `
  --base-url https://COORDINATOR:8443/downloads/RELEASE `
  --out bootstrap/test-kit/public-RELEASE
```

Binary names are `campus-{darwin,windows,linux}-{amd64,arm64}` with `.exe` on
Windows. A subset is accepted. Every supplied file must have the expected native
executable header and Go build settings `GOOS`, `GOARCH`, `CGO_ENABLED=0`.
The source image archives are streamed once to verify gzip integrity, compressed
SHA-256, every addressed blob digest, approved OCI/Docker identity, native Linux
architecture and guard labels. Archives are hardlinked into the new release:
keep their sources immutable, and put the output on the same filesystem.
The builder refuses an existing destination and publishes only a complete release.

`catalog.json` uses schema_version `"1"`, one endpoint, optional `ca_file` and
`targets` containing `os`, `arch`, `runner`, `profiles` and `images`. A file reference
is `{path, sha256, size_bytes}`. Image references also carry `image_digest`,
`accelerator`, `platform`. All paths are generated safe ASCII paths relative to
the catalog directory. Assets share the configured HTTPS origin. Targets share
one CPU archive per architecture; CUDA is offered only on non-Mac amd64 targets
when an approved CUDA archive was explicitly provided. Profiles include only
`id`, `accelerator`, `platform`, `image_digest`, `guard_version`.

Share `Connect.cmd` for Windows and `Connect.command` for macOS/Linux, through a
trusted channel. On Mac use `sh ./Connect.command` in Terminal. Do not remove
quarantine or disable OS security. `Connect.ps1` is the optional advanced Windows
entry point; the CMD entry point needs no execution-policy bypass. Standard
Windows 11 `curl.exe` and POSIX `curl` are used for the small native client.
Clients are downloaded to a private cache, verified against embedded exact size
and SHA-256, then invoked with the embedded catalog URL and catalog SHA-256.
Each cache reuse is hashed again. Rosetta shells still select Apple Silicon.
The starter downloads no environment archive and accepts no invitation argument.
The native client performs CPU/NVIDIA checks, asks for CUDA consent before its
CUDA download, then asks separately for the private invitation.

TLS certificate and hostname verification stay enabled. Redirects are refused;
HTTP 200, time limits and exact size/hash verification are required. TLS 1.2 is
the minimum, and the pinned asset hostname bypasses proxy environment settings.
An embedded public CA is stored only in the private cache. It is never installed
in global trust. Only Windows downloads using this custom CA enable
`--ssl-revoke-best-effort`, permitting unavailable/offline CRL checks while
retaining certificate/hostname checks. Default public-PKI revocation remains
unchanged. No insecure TLS or global revocation-disable option is used.

`SHA256SUMS` covers public files. Verify the release has no invitations before
hosting its directory read-only at `/downloads/RELEASE/`. The CLI prints only its
output directory and catalog digest. It does not publish or change a live server.

Behavioral tests (real WinPS5/CMD, native executable boundary and Git sh):

```powershell
workloads/.venv/Scripts/python.exe -m pytest bootstrap/tests -q
```
