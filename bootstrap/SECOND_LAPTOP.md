# Second notebook: Apple Silicon connection and offline history

For the current LAN connection, use the new
[native connection package](connect/README.md). From the extracted folder, run:

```sh
sh ./Connect.command --check-only
sh ./Connect.command
```

`campus connect` prepares and checks the selected runtime before enrollment;
`--check-only` stays local and consumes no invitation. The LAN coordinator now
runs at [https://10.4.146.76:8443](https://10.4.146.76:8443) with strict TLS and a
private client CA.

Both commands have now passed on the physical **Apple M2 Mac, 8 cores, 8 GiB
RAM**. Check-only passed the real CPU probe; normal connection offered 2 CPU and
0.38 GiB RAM in that run, restored the saved identity and entered Serving.
A strict-CA owner API read independently observed `MacBook Apple Silicon`
(`50b752cf-f13f-4f76-ae0c-0b23f7666d35`) ready over the LAN at
`2026-11-14T11:24:04.648714Z`, OS `darwin`, architecture `arm64`, CPUs 8.

The transferred runner matched its package's pinned SHA-256, and Mac `codesign`
reported valid on disk and satisfying its designated requirement. Edge
quarantine metadata was present. The first Finder double-click produced
`zsh: killed`; its cause remains unconfirmed. Use the tested `sh` route above.
Physical Mac Ctrl+C printed `campus: context canceled` and returned the expected
exit **130**. Running the same `sh` launcher again restored the saved connection
and entered Serving. The owner API confirmed that same Mac ready after restart
at `2026-11-14T11:27:23.242365Z`. Distributed workloads remain untested; the relative
deadline/sandbox quota contract gate remains in force.

The remainder records the earlier CPU-only offline readiness kit. Its hashes
and checks remain useful evidence, but it does not replace the current
connection package or establish a live device session.

## Earlier offline prepared kit

The earlier offline handoff used `bootstrap/test-kit/macos-arm64-foundation/`.
It contains the clean native binary built from implementation commit
`eac3ae08284158f4c7ac76e83544af8e4ca0f094`, after published-contract reconciliation.
The earlier `macos-arm64-ready/` folder is retained as a historical first-pass kit.

Trusted SHA-256 of that historical kit's `SHA256SUMS`:

```text
04134046d412f71b642229c89b7b45c57bc9abd15afd2230d695ba4559a33d9f
```

Payload size:208175556 bytes (about198.53 MiB). All payload hashes, full gzip/tar
archive validation and shell syntax passed on the first notebook. Doctor also
supports a verified locally loaded image without RepoDigest; joining/serving
still requires a verified canonical reference and compatible real control plane.
This kit is local CPU readiness only. Native Mac execution had not been observed
when it was exported; the current connection package's physical result is above.

The following maker/checker instructions describe that earlier offline handoff.
Its checker exercises a Mac's native CPU runtime; it does not establish a pool
run or benchmark. Use the current connection package for the LAN session.

The receiving Mac needs macOS on Apple Silicon, Docker Desktop for Apple
Silicon installed/running, AC power, and enough free disk for the compressed
archive plus loaded image layers. macOS supplies the shell and `shasum`.
Host Python/Go, a model download, GPU setup, invites, and credentials are not
part of this kit.

## Make the kit on the first notebook

Use the already-built native Darwin/arm64 `campus` and an already-cached CPU
`linux/arm64` image. The prepared profiles file must contain exactly one such
CPU entry with guard version `1` and a local immutable `sha256:<64 hex>` image
ID. An amd64 profile or mutable tag is rejected. The generator inspects the
actual local image identity, platform, and CPU/guard labels before export.
It performs no build or image pull.

From the repository root:

```console
uv run --offline --project workloads --extra cpu python bootstrap/prepare_test_kit.py --profiles "environments/resolved-profiles.json" --binary "bootstrap/test-kit/macos-arm64/campus" --out "bootstrap/test-kit/macos-arm64-ready"
```

`--out` must not exist, including as an empty directory. For another kit choose
a new output path. The target is fixed to macOS ARM64. The maker needs Python
and its existing Docker CLI; no Python packages are needed by the generator.

The complete directory contains:

| File | Purpose |
| --- | --- |
| `campus` | Supplied native ARM64 Mach-O executable |
| `profiles.json` | One minimal CPU ARM64 runtime profile |
| `image.tar.gz` | Only that cached image, exported by ID and streamed through gzip level 1 |
| `SHA256SUMS` | SHA-256 of all five payload/document/check files |
| `README.md` | Self-contained Mac instructions and limits |
| `check.sh` | Checksum, platform, doctor, and bounded CPU checks |

The generator prints JSON with the absolute kit directory and SHA-256 of
`SHA256SUMS`. Keep that digest with the later trusted handoff. A failed export
leaves no published kit; private staging is removed. The destination is
published atomically without replacing an existing directory. Image compression
uses bounded reads and omits the maker filename/time from gzip metadata.
The manifest/profile does not copy arbitrary builder metadata, credentials,
personal paths, models, or a GPU profile. Generated kits under
`bootstrap/test-kit/` are ignored by Git.

## Historical offline checker instructions

Transfer the whole folder through an owner-approved local method after the
first notebook's checks are done. Keep its files together. Start Docker
Desktop and connect AC power, then use Terminal; paths with spaces or Unicode
must remain quoted:

```sh
cd "$HOME/Desktop/Campus Mac Ready"
shasum -a 256 ./SHA256SUMS
# Compare the value above with the maker's trusted handoff value.
shasum -a 256 -c ./SHA256SUMS
sh ./check.sh
```

The checker uses its own directory, so another folder name is also fine. It
verifies checksums before executing/loading payloads, requires Darwin/arm64,
adds execute permission only to the supplied `campus`, and loads the bundled
archive with `docker load --input`. It then inspects the loaded image and runs
`./campus doctor --profiles ./profiles.json` with Campus's normal private user
state. A fresh readiness notebook needs no coordinator identity.

If macOS specifically blocks the checksum-verified binary because of its
quarantine attribute, remove that attribute only from this verified file:

```sh
xattr -d com.apple.quarantine "./campus"
sh ./check.sh
```

If execution remains blocked, keep the error for the owner. The kit does not
change global Gatekeeper settings.

The additional tiny PyTorch matrix calculation is CPU-only and runs in the
loaded image with `--pull=never`, `--network=none`, read-only root, UID65532,
all capabilities dropped, no-new-privileges, 64 PIDs, 1 GiB memory, 1 CPU,
64 MiB temporary storage, and restart disabled. No host directories are mounted.
Its in-container Python parent bounds the calculation subprocess to 45 seconds,
independently of the host CLI's lifetime. An interrupted host command therefore
does not remove that calculation deadline.

## Records and the later shared-LAN step

Review `readiness-records/` before returning it later:

- `checksums.txt`, `system.txt`, `image.txt`, and `docker-load.log`;
- `doctor.json` and `doctor.stderr`;
- `cpu-check.json` and `cpu-check.stderr`.

These report local integrity, platform/runtime inventory, and one actual tiny
CPU calculation. Keep credentials, datasets, and other personal files out of
the handoff. At the time of this offline-kit record, native Mac execution had not
been performed. Its maker tests alone did not establish that result; the current
connection package's later physical Mac check is recorded above.

Both notebooks should use the same LAN for a later coordinated test. That
step still needs the real control plane, endpoint/certificate/invite, and
explicitly chosen network configuration. This older readiness kit does not
bundle a control plane; the current LAN coordinator is available separately at
the address above. The current Mac connection did prove that peer's LAN access;
these earlier readiness results alone did not. Neither establishes distributed
correctness, training resume, GPU support or comparable benchmark performance.
Production workloads also remain
behind the relative deadline/sandbox quota contract gate.

## Local generator verification

```console
uv run --offline --project workloads --extra cpu python -m pytest bootstrap/tests/test_kit.py -q
```

Tests exercise streaming/checksums, native binary/profile/image selection,
export-failure and concurrent-destination atomicity, the independent subprocess
deadline, a real tiny CPU Torch calculation, and the generated checker's argv
flow using a fake external Docker boundary where this host cannot execute a
Mac binary or the ARM image natively.

## Historical first-pass handoff on 2026-11-14

The first notebook has generated `bootstrap/test-kit/macos-arm64-ready/`.
Copy that entire folder later; its payload is about 198.45 MiB. All payload hashes,
the complete compressed image archive and shell syntax have been verified on
the first notebook. Native execution had not been observed at that historical
handoff; the current connection package's physical success is recorded above.

Compare the output of `shasum -a 256 ./SHA256SUMS` with this trusted value before
running the checker:

```text
854e56f98ac161a4df7acf41f927eaf89ca975ee15e62981e78f9ad7d8a9b212
```

The bundled native binary uses clean implementation commit
`cd975f3d675a4cdb8ad0a87b91e72c5033b632f7`. See
[the local validation record](../runner/VALIDATION.md) for the image identity and
first-notebook results.
