# Native connection packages

The operator builds the target Campus executable with `CGO_ENABLED=0`, then
runs the stdlib-only package builder against already cached approved images:

```console
uv run --offline --project workloads --extra cpu python bootstrap/prepare_connection_kit.py --profiles environments/resolved-profiles.json --binary PATH_TO_NATIVE_CAMPUS --target darwin-arm64 --out NEW_PACKAGE_DIRECTORY --ca-file PRIVATE_CA.pem --invite-file PRIVATE_INVITATION.txt --endpoint https://COORDINATOR:8443
```

Use `--target windows-amd64` for Windows. Target selection keeps exactly one
native CPU profile and at most one prepared native CUDA profile. Windows includes
CUDA when it is present in the supplied profiles file; pass a CPU-only profiles
file to omit that archive. The maker needs Python and Docker, while recipients
need neither Python nor Go. Images are never built, pulled or pushed here.

The optional invitation input is one UTF-8 URL/token line. Its presence makes
the entire output a private per-device package; do not publish that directory
or archive. Leave `--invite-file` out of generic packages. The builder protects
the output directory with private Unix permissions or a current-user Windows
ACL before writing invitation bytes. The client CA stays in the package and
is never installed into the system trust store.

`connection.json` pins all payload sizes/hashes. Mac and Windows launchers pin
that manifest and the native runner separately, then call `campus connect` in
the foreground. `Connect.cmd` uses a static inline PowerShell hash check and
does not execute `Connect.ps1`; it accepts no extra arguments. Advanced Windows
arguments can be passed through `Connect.ps1` under the user's normal execution
policy. No wrapper changes that policy or system security settings.
The CMD window waits for Enter after an error so setup instructions remain
visible. Success and cancellation exit immediately; closed input never stalls
a headless run. The original Campus exit code is preserved.

On Mac, run `sh ./Connect.command` from Terminal in the extracted folder;
on Windows, open `Connect.cmd`. Keep the complete package folder together.
The `sh` route passed physical M2 preflight and live enrollment/reconnect;
the first Finder double-click reported `zsh: killed`. For setup without
enrollment, use:

```sh
sh ./Connect.command --check-only
```

```powershell
./Connect.ps1 --check-only
```

Use the same entry point with `--drain` to finish active work and disconnect,
or `--stop` to stop active work. GPU hardware detection alone does not opt the
owner in. An explicit GPU choice and a real bounded CUDA probe are required.

The builder exports a verified cached image tag when available and checks it
again after the export; otherwise it saves the immutable local image ID. The
streamed tar index/config identity and blob hashes are verified while gzip is
written. A named export does not promise that a fresh Docker image store will
retain canonical participation metadata. `connect` checks the loaded image;
the real Mac/Docker/coordinator connection still needs target-device QA.

The Mac launcher and binary are emitted with Unix mode 0755. Windows cannot
retain Unix execute bits in a raw directory. If packaging the Mac directory on
Windows into a ZIP for Finder, use Unix ZIP metadata (`create_system=3`, mode
0755 in `ZipInfo.external_attr`) for `Connect.command` and `campus`. Test the
actual extracted download on a Mac; quarantine may require its normal per-file
security decision. Native Mac evidence comes from the physical M2 check in
[the current handoff](../LIVE_SERVER.md), separately from Windows-side tests.
