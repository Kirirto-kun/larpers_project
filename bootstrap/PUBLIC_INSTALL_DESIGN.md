# Public, cached device installation

The owner explicitly specified and requested implementation of this flow:
detect OS/architecture/CPU/NVIDIA, download the matching client and environment,
download CUDA only after GPU consent, verify checksums, cache downloads, then
ask separately for a device invitation. Docker remains a prerequisite. Small
Windows and Mac starter files contain no invitations or credentials and can be
redistributed. This refines the existing approved connection design.

## Approach

Keep the existing verified offline `connect` path and add a public catalog and
native `install` command. Thin shell/CMD starters pin the native executable and
catalog hashes. Go handles selected environment downloads, resumable caching,
device detection and invitation entry. This avoids duplicating large-download
and enrollment behavior in two shell languages. Whole offline ZIPs remain an
alternative, but would force CPU users to receive CUDA; host Python installers
would add a dependency the existing design deliberately avoids.

The implementation choices below are engineering decisions within the owner's
specified flow. Existing branch `codex/runner-cli` remains isolated from main.

## Public catalog

JSON schema_version1: `{schema_version,endpoint,ca_file?,targets:[...]}`.
Each target is `{os,arch,runner,profiles,images}`. File objects are
`{path,sha256,size_bytes}`; images additionally have
`{image_digest,accelerator,platform}`. Paths are safe relative paths resolved
against the catalog URL directory, on the same HTTPS origin. Credentials,
query/fragment tokens, redirects, unknown fields and unsafe paths are rejected.
The catalog is at most64KiB; file/image quotas match the offline bundle.

Native target binaries cover macOS/Windows/Linux on amd64/arm64 where the
required local Docker runtime works. CPU images are shared by architecture.
CUDA is offered only on supported Linux/Windows amd64 with actual NVIDIA
compute detection, explicit consent and the existing real container probe.
This is not a claim of phone support, Windows ARM GPU support, or Apple Metal
passthrough. Docker Desktop Windows ARM currently has early-access support;
preflight remains authoritative.

## Trust and caching

Each starter embeds the catalog URL/hash and native client size/hash, plus an
optional public CA certificate. The CA is client-local, never installed into
the OS. The catalog and release directory contain no invitation or secrets.
The current LAN release uses the existing local HTTPS gateway; a future public
hostname can use ordinary public PKI without changing installation semantics.

Go verifies the frozen catalog and running native binary, selects CPU or
explicitly permitted CUDA, and populates an owner-protected content-addressed
cache. Cache hits are rehashed. Partial downloads use validated HTTP Range
responses and become published objects only after complete size/hash checks.
No unrelated cache files are removed. Concurrent installs cannot replace an
active download. Cancellation preserves valid partial data for resumption.

The prepared local `connection.json` includes only selected files, has no
invitation, and is consumed by existing `connect`. A saved identity reconnects
without requesting/redeeming another invitation. Fresh enrollment asks for the
private invitation only after successful preparation/preflight; token input
does not become a shell argument or a public file.

## Public release preparation and service

The maintainer builds native CGO0 clients and uses a stdlib-only publisher to
verify existing prepared image archives and native headers, create sanitized
profiles/catalogs and pinned starters, and publish a new local output directory.
It reuses the already exported CPU/CUDA archives rather than exporting them
again. Only that sanitized directory is mounted read-only under the gateway's
`/downloads/<release>/` prefix. Private offline device packages remain outside
the served root. No GitHub push/public internet release is inferred.

## Real Mac computation

The M2 Mac is already native ARM64 ready, with CPU preflight, stop/reconnect
and cross-device heartbeat confirmed. A real computation test must lease a
bounded task, execute in the Mac's guarded container, upload an artifact and
independently verify its mathematical result. Merely probing Torch or seeing
ready is not that test.

The old running Mac binary uses a richer execution wire model than the current
server DTO. A reproducible, explicitly allowlisted diagnostic compatibility
overlay can exercise this existing binary without changing other nodes or
pretending the general production API agreement is complete. It supplies a
server-clock relative hard bound and trusted explicit sandbox limits; the
ordinary server fencing/artifact validation stays enforced. The diagnostic is
CPU-only, one declared basename output, no inputs/checkpoints/GPU, <=60s,
<=256MiB RAM and <=1CPU, within the Mac's advertised budget. A trusted catalog
entry and normal validated job submission are required. No fabricated success
or direct database completion is allowed. Record any remaining protocol limits.
