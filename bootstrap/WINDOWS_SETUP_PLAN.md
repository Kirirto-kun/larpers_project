# Windows prerequisite setup

The owner's latest clarification is authoritative: Docker Desktop is installed
manually; the starter installs missing WSL2 and the Windows NVIDIA driver, then
continues the existing verified client/environment/invitation flow. No Docker
installation is performed by this change. Existing isolated branch is reused.

## Design and decisions

Embed two focused PowerShell helpers into the existing single public CMD/PS1.
`Ensure-CampusWSL(StateDir)` and `Ensure-CampusNVIDIA(StateDir,Architecture)`
return0 or3010. They use official Microsoft/NVIDIA installers, validate trusted
sources/signatures, skip already-ready components and never install a Linux
display driver. Only official machine installers receive ordinary Windows UAC
elevation; Campus itself remains under the original user. No secrets are passed
to elevated processes. No forced reboot, RunOnce/task, security-policy change,
WSL shutdown or changes to this host's running Docker deployment are allowed.

GPU permission is collected before driver or CUDA downloads and forwarded to
the native client so it is asked once. CPU users skip NVIDIA setup. A required
restart stops before enrollment, with instructions to rerun the same file.
The caller refreshes only its process PATH and restores environment afterward.
Docker Desktop must be found in a standard user/machine installation; otherwise
the starter explains the manual prerequisite. Existing Go preflight remains
authoritative for local Linux Docker and actual CUDA computation.

For the known RTX5050 laptop, NVIDIA WHQL621.14 is supported by the official
package metadata. Unsupported hardware must fail explicitly rather than guess
a driver. The installer opens the official vendor wizard when silent SFX
switches cannot be verified. The user approves UAC/vendor UI and any reboot.

## Parallel execution

- [x] WSL helper/tests: mock install/update/elevation/readiness/restart/cancel;
  no feature changes on this host. Verify native UTF16 output handling.
- [x] NVIDIA helper/tests: physical PCI identification, compatible package,
  publisher/size/hash-cache checks, healthy skip, UAC/restart/failure behavior;
  no real graphics installation on this host.
- [x] Root: tests-first orchestration, embed helper sources, Docker prerequisite
  and process PATH discovery, one GPU consent and restart fence before invite.
- [x] Review high-impact operations and run targeted suites plus existing
  public starter regression coverage.
- [x] Refresh a new public release from unchanged verified runtime assets;
  validate starter bytes/pins and read-only live publication without re-exporting
  or rehashing unchanged multi-GiB images.
- [x] Record what was actually tested; actual privileged installation/reboot
  must be checked on the target laptop with missing components.

R4 live launch passed ready-Windows skip/setup orchestration, CPU probe, saved
connection and owner Stop without installing/updating this machine. Core Go
clients were unchanged. New module tests:17 WSL,58 NVIDIA,7 orchestration;
combined public/orchestration54 passed with6 old argv assertions updated/rerun green.
Read-only review found no normal64-bit blocker. Its32-bit native-tool selection
concern was fixed and checked using actual32/64-bit PowerShell processes.

Official references: [Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands),
[Docker Windows setup](https://docs.docker.com/desktop/setup/install/windows-install/),
[NVIDIA621.14 notebook](https://www.nvidia.com/en-us/drivers/details/283969/).
