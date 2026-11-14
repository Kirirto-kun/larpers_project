# Campus runner (part 4)

This branch contains the Go CLI/worker, isolated workload runtimes, Python SDK,
checkpoint helpers and verified bootstrap for the Campus Pool implementation plan.
It does not contain the control plane, shared generated contracts or the two web
applications. Those components must be integrated before this is a deployable pool.

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

Foundation is owned by the control-plane branch. Runner-owned wire structs are
provisional, not generated DTOs. Align the exact payloads in CLI.md and the SDK
README with the real OpenAPI/schemas/fixtures, run generation, then execute both
documented integration rounds. Never replace the other owners' lockfiles/configs.

CPU amd64/arm64 and CUDA amd64 images have been built locally. Real CPU guard,
training/resume and pretrained E5 checks are recorded in VALIDATION.md. Release
acceptance still requires a real doctor on each laptop, two physical devices in
one map job, trusted references and comparable standalone baselines, physical
disconnect/crash fault checks, and actual GPU checkpoint recovery. Local fixtures
and single-host CPU execution do not satisfy those physical gates.
