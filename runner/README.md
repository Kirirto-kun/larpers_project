# Campus runner (part 4)

This branch contains the Go CLI/worker, isolated workload runtimes, Python SDK,
checkpoint helpers and verified bootstrap for the Campus Pool implementation plan.
The control plane runs separately: upstream `96bfe6d` with the restricted
diagnostic compatibility overlay is deployed on the LAN at
[https://10.4.146.76:8443](https://10.4.146.76:8443), using strict TLS with a private
client CA. Real local Go enrollment and ready heartbeat have passed; a physical
Apple M2 Mac also passed CPU preflight, saved reconnect and a server-observed
LAN ready heartbeat using `sh ./Connect.command` from its extracted package.
That Mac subsequently completed a real distributed CPU model-fitting job;
its downloaded output passed independent numerical verification. This uses an
explicit device/image/adapter allowlist and fixed bounded policy, not general
compatibility for arbitrary jobs.
The two web applications are outside this runner branch.
Published foundation DTOs are now generated into the Go client and optional
Python SDK types. [Integration status](PROTOCOL.md) records the supported request
boundaries, the successful restricted diagnostic and the worker safety contract
that still blocks ordinary incompatible assignments.

The current [public starter](../bootstrap/public/README.md) is a small,
invitation-free file that selects the native OS/architecture, verifies and caches
the client, and invokes `campus install` to prepare the matching CPU environment.
CUDA downloads require explicit GPU consent. A new device supplies its private
invitation separately after preflight; reconnect uses its saved identity.
Docker Desktop remains a prerequisite on Windows/macOS. The current download
host is LAN-only; [handoff and status](../bootstrap/LIVE_SERVER.md) distinguish
staged releases from verified ones. Private offline device packages remain an
alternative.

## Build and test

Go 1.25.6 and Python 3.11/3.12 are supported by the locked development environment.
After installing `uv` and Go, run from the repository root:

```text
go -C runner test ./...
go -C runner test -race ./...
go -C runner vet ./...
go -C runner build -o bin/campus ./cmd/campus
uv sync --project workloads --extra cpu
uv run --offline --project workloads --extra cpu python -m pytest -q
```

Use `python -m pytest` for a repository-wide run so the repository is on Python's
import path. The CUDA extra is an alternative prepared environment, not a CPU
fallback. Do not combine the `cpu` and `cuda` extras.

Prepare the pinned image explicitly before going offline:

```text
uv run --project workloads --extra cpu python environments/prepare.py --accelerator cpu
campus doctor --profiles environments/resolved-profiles.json
campus join INVITATION_URL --profiles environments/resolved-profiles.json
```

`prepare.py` builds from a bounded source-only context and records the actual
immutable Docker image ID only after success. The checked-in profile has a null
image digest until then. There is no implicit image/model pull or dependency
installation during a job. The model preparation command is separate and explicit:

```text
uv run --project workloads --extra cpu python environments/prepare_model.py --to environments/models/multilingual-e5-small
```

The pinned model revision and upstream file digests are verified before the local
model lock is published. Model files, images, virtual environments, executable
builds and generated test outputs are not committed.

## Interfaces and operation

- [Prepared Mac/Windows connection packages](../bootstrap/connect/README.md)
- [Public native starters and release preparation](../bootstrap/public/README.md)
- [CLI and standalone measurements](CLI.md)
- [Python SDK and checkpoint helpers](../sdk/python/README.md)
- [Adapters, E5 search, training and independent validators](../workloads/README.md)
- [Bootstrap with trusted release URL and SHA-256](../bootstrap/README.md)
- [Implementation decisions](IMPLEMENTATION.md)
- [Measured validation and remaining gates](VALIDATION.md)
- [Offline dependencies and prepared wheelhouse](../environments/README.md)
- [Apple Silicon readiness kit](../bootstrap/SECOND_LAPTOP.md)
- [Provisional v1 foundation handoff](PROTOCOL.md)

`campus run --gpu --data ./dataset:/data --output model.pt -- python train.py`
creates a bounded selected-project snapshot and an immutable preview before job
submission. Member credentials are separate from device invitations. Set
`CAMPUS_TOKEN` to the scoped member CLI credential; node credentials never provide
submission authority. Use a trusted TLS certificate or `--ca-file`; no insecure
TLS mode exists. `leave --drain` and `leave --stop` have different semantics.

Generic code returns files with quality marked unverified. Registered training
and embeddings obtain correctness claims only through their actual registered
validators. A signature proves which registered credential produced a measurement;
it does not prove the owner measured honestly.

## Integration gates

Foundation is owned by the control-plane branch. Its published OpenAPI and
fixtures are pinned in PROTOCOL.md; generation and client boundary checks have
been performed. Rich local execution policy remains separate from those DTOs.
The live coordinator's general relative hard-deadline/sandbox agreement remains
required before executing ordinary production assignments. The server supports
the verified
local and physical Mac enrollment/ready-heartbeat paths. Physical Mac Ctrl+C
returned the expected exit130, and another launch restored its saved connection.
The [physical Mac computation](../bootstrap/control_plane_compat/RESULT.md)
additionally reached succeeded and returned a verified 281-byte model under the
restricted CPU policy. The Windows 11 AMD/RTX 5050 CPU/CUDA/LAN run remains
outstanding. The initial Mac
Finder launch failure is unconfirmed; use the tested `sh ./Connect.command`
route. Never replace the other owners' lockfiles/configs.

Native clients are built for Windows/macOS/Linux on amd64/arm64, with CGO disabled,
`-trimpath` and `-buildvcs=false`. Build availability does not establish every
platform's Docker support or physical acceptance; Windows ARM Docker Desktop is
currently Early Access. No ordinary phone or Apple Metal target is offered.
CPU amd64/arm64 and CUDA amd64 images have been built locally. Real CPU guard,
training/resume and pretrained E5 checks are recorded in VALIDATION.md. Release
acceptance still requires readiness checks on the remaining laptops, two physical
devices in
one map job, trusted references and comparable standalone baselines, physical
disconnect/crash fault checks, and actual GPU checkpoint recovery. Local fixtures
and a single physical Mac diagnostic do not satisfy those physical gates.
