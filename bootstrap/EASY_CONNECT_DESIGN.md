# Simple device connection

Implements the already approved temporary foreground bootstrap design and the
owner's 2026-11-14 request: one downloaded package/script, automatic device
detection, Apple Silicon Mac plus Windows11 AMD/NVIDIA RTX5050, using subagents.
Windows11 was explicitly confirmed. This is an implementation refinement of
the approved script/portable-runner approach, not a background service install.

## Chosen flow

One native launcher starts `campus connect`. A verified package contains the
native binary, approved profiles, offline CPU image and optional CUDA image.
The Mac launcher is `Connect.command`; Windows has `Connect.cmd`/`Connect.ps1`.
Only Docker Desktop is a host prerequisite. Missing runtime opens no privileged
installer silently and produces the official next step. A stopped runtime can
be started through its normal desktop application with bounded waiting.

The runner verifies the pinned connection manifest and every selected file
before loading anything. It verifies the Docker endpoint is local and pins that
socket in its own process. It detects CPU model/vendor, architecture, RAM/disk,
NVIDIA model/driver/VRAM and runtime health. It shows the existing conservative
budget. GPU detection does not grant GPU use: a flag or one explicit confirmation
is required, and a real bounded CUDA calculation must pass.

Then the runner uses a supplied invitation or asks for one. An optional private
per-device invitation file permits fully prepared one-click packages; it must
never be included in a generic public download. A CA is used only by this client,
not installed into the global trust store. Preflight precedes redemption.
Saved identity reconnects through serve; it is not redeemed again. Participation
remains visible, Ctrl+C/Stop works, and Drain/Stop are supported without autostart.

## Package contract

`connection.json`, schema_version1, contains `os`, `arch`, `runner`, `profiles`,
`images`, optional `ca_file`, `invite_file`, and `endpoint`. File objects have
`path`, `sha256`, `size_bytes`; image objects additionally have `image_digest`,
`accelerator`, `platform`. Paths are bounded, relative, canonical and link-free.
CPU image is mandatory; CUDA is optional. Maximum2 images,8GiB per archive,
16GiB total package,128MiB binary,2MiB profiles/CA/invitation metadata each.
The manifest itself is <=64KiB. Launchers pin both its digest and binary digest.

Invocation: `campus connect --bundle DIR --manifest-sha256 HEX [--check-only]
[--gpu] [--drain|--stop]`. Global state/endpoint/CA options remain available.
File names and arbitrary Unicode/spaces are passed through argv, never eval.
`--check-only` verifies setup without consuming an invitation or joining.
Only chosen CPU/CUDA archives are loaded; cached verified images are reused.

Builder: `bootstrap/prepare_connection_kit.py --profiles FILE --binary FILE
--target darwin-arm64|windows-amd64 --out NEW_DIR [--ca-file FILE]
[--invite-file FILE] [--endpoint HTTPS_URL]`. It uses native build metadata and
actual locally cached immutable images, exports named references when available,
validates image/archive identity, streams compression with bounded memory, and
publishes a new complete private directory atomically. It never pushes images.

## Constraints and actual service

The user explicitly requested immediate server startup. An isolated
`campus-live` deployment of upstream96bfe6d now runs on
`https://10.4.146.76:8443`, with PostgreSQL internal-only, private runtime secrets,
local CA and LAN-only bind. Health/protocol and actual device registration/ready
telemetry have passed. Peer reachability on eduroam needs the actual second
device; the VPN adapter must not be mistaken for the Wi-Fi address.

Full workload execution still has the documented assignment deadline/sandbox
contract gate. Do not claim task execution from successful device enrolment.
Mac Finder/quarantine and RTX5050 CUDA require real target hardware checks.
No silent OS/driver/WSL install, global trust change, global policy bypass,
firewall reset, system reboot or removal of other containers is authorized.

## Verification focus

Corrupt/wrong-platform payloads must fail before execution/load/redemption;
remote Docker must fail before probing; first join and saved reconnect must be
distinct; CPU-only and explicit-GPU paths must preserve actual probe outcomes;
invites/credentials must not enter logs or public packages. Test interactive
EOF, Unicode paths, missing/stopped Docker, interrupted load and cancellation.
