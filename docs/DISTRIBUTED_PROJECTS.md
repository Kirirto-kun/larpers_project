# Run a distributed project

From a project directory, `campus run` reads `campus.json`, uploads a bounded
code snapshot and declared data files, reviews the plan, and submits one job.
Map tasks can execute on different compatible computers; reductions combine
their accepted outputs. The project declares how the work is split.

## Get the developer CLI and sign in

Use the native `campus` binary for your OS and architecture. Put it on `PATH`,
or invoke its local path (`.\campus.exe` in PowerShell, `./campus` on macOS/Linux).
The submitting computer needs no Go, host Python or Docker merely to upload
and follow a prepared project. Data preparation can have its own dependencies.

The published project-capable release is **`runner-20261115-r8`**. Download and
extract the matching CLI ZIP; it includes the native binary, the public Campus
CA and a short sign-in guide, with no invitation or credential:

| Computer | Developer CLI |
| --- | --- |
| Windows Intel/AMD | [Windows x64 ZIP](https://10.4.146.76:8443/downloads/runner-20261115-r8/campus-cli-windows-amd64.zip) |
| Windows ARM | [Windows ARM64 ZIP](https://10.4.146.76:8443/downloads/runner-20261115-r8/campus-cli-windows-arm64.zip) |
| Mac Apple Silicon | [macOS ARM64 ZIP](https://10.4.146.76:8443/downloads/runner-20261115-r8/campus-cli-darwin-arm64.zip) |
| Mac Intel | [macOS Intel ZIP](https://10.4.146.76:8443/downloads/runner-20261115-r8/campus-cli-darwin-amd64.zip) |
| Linux Intel/AMD | [Linux x64 ZIP](https://10.4.146.76:8443/downloads/runner-20261115-r8/campus-cli-linux-amd64.zip) |
| Linux ARM | [Linux ARM64 ZIP](https://10.4.146.76:8443/downloads/runner-20261115-r8/campus-cli-linux-arm64.zip) |

These are LAN links. The [SHA256SUMS](https://10.4.146.76:8443/downloads/runner-20261115-r8/SHA256SUMS)
file records all public download identities. Full download checksums, starter
attachment headers and private-path 404 checks passed over strict CA-verified
HTTPS. The [live handoff](../bootstrap/LIVE_SERVER.md) records deployment and
physical verification status. Source build instructions are in the
[CLI guide](../runner/CLI.md).

Use an existing Campus member account and the coordinator URL supplied by your
operator. For an installation with a private CA, obtain its public CA certificate
through the established trusted channel, then sign in once:

```text
campus login --endpoint https://coordinator.example --ca-file campus-ca.pem
```

Enter your email and password in the terminal prompts. The password prompt is
hidden. The client saves a revocable CLI credential and the approved CA outside
the project; later commands reuse the coordinator and trust. This does not
change system certificate trust. Omit `--ca-file` when the coordinator uses a
publicly trusted certificate. `campus logout` revokes the saved CLI credential.

For the current LAN service, use `https://10.4.146.76:8443` as the endpoint and
the included `campus-ca.pem`. On macOS/Linux, make the extracted binary executable
with `chmod +x ./campus`. Follow normal per-file OS approval when requested.

`Connect.cmd` / `Connect.command` prepare and connect a **computing device**.
They are separate from developer sign-in. You can submit from one computer and
run every task on other connected computers. To contribute the submitting
computer too, connect it through the ordinary device workflow and keep its
foreground worker running. See [device connection](../runner/CLI.md#public-starter-and-automatic-preparation).

## Run, review and collect results

From a directory containing `campus.json`:

```text
campus run --wait --to ./results
```

The CLI prints the code/data inventory, effective resources, task counts and
reviewed plan. Choose the exact code paths to share with the LLM, or enter
`none`, then enter `yes` at the submission prompt. It supports selected UTF-8
text only: up to 16 files, 8,000 bytes each and 24,000 bytes total.

The LLM receives the project configuration, prompt and bounded file metadata,
plus the source text you explicitly selected. Dataset bytes and credentials
are not sent to the provider. Choosing `none` permits a metadata-only review.
The server compiles the executable plan from the declared contract; the LLM
cannot change commands, input selection, dependencies or resource reservations.

`--project DIR` selects another project. `--to` implies waiting, is relative to
that project, and must name a directory that does not already exist. The CLI
follows task state and prints accepted work and device IDs. Downloaded outputs
are checked against their declared sizes and SHA-256 values. Intermediate
outputs, and outputs of stages with multiple tasks, use
`stages/<stage>/<task-id>/<output-path>` to preserve distinct files.

After one explicit review, an unchanged project can reuse that saved approval:

```text
campus run --yes --wait --to ./new-results
```

Approval binds the code, data, configuration, sharing choices and authenticated
coordinator context. Changes require another explicit review. CI can supply a
CLI credential through `CAMPUS_TOKEN`; keep the approved review in the intended
private CLI state and supply secrets through the CI secret mechanism.
`--yes` does not approve a new or changed project.

After a disconnected terminal or uncertain submission acknowledgement, resume
the saved job rather than submitting a fresh run:

```text
campus run --resume --wait --to ./results
```

Resume keeps the frozen submission revision and job key. `--discard` only
abandons a never-submitted preview. A partial or failed job returns an error;
with `--to`, a partial job still downloads its accepted outputs. More recovery,
question-answering, logs and cancellation commands are in the
[CLI guide](../runner/CLI.md#sign-in-and-run-a-distributed-project).

## What a project can express

`campus.json` is UTF-8 JSON with `schema_version: "1"`, a `name`, relative `data`
globs, optional `prompt`, and ordered `stages`. Each stage declares its `id`,
`kind`, `inputs`, `depends_on`, literal `command` argv, bounded `outputs`,
`accelerator`, `resources` and `timeout_seconds`. The
[CLI guide includes a minimal complete contract](../runner/CLI.md#sign-in-and-run-a-distributed-project).

| Declaration | Execution |
| --- | --- |
| `kind: "map"` | One task per matching immutable data file; inputs must be explicit and nonempty. |
| `kind: "reduce"` | One task; it can select declared data and consume accepted predecessor outputs. |
| `depends_on` | Names earlier stages exactly. A dependent map still splits its own selected files and waits for its predecessors. |
| `{input}` | Map argument token resolving to that task's `/inputs/<relative-file>`. |
| `{accelerator}` | Argument token resolving to the selected `cpu` or `cuda` variant. |
| Predecessor output | Mounted read-only at `/inputs/stages/<stage>/<task-id>/<output-path>`. |
| Code | The immutable project archive mounts at `/workspace`, the working directory. |

Globs are case-sensitive portable paths: `*`, `?`, character classes and whole
`**` directory segments. Every declared input glob must match uploaded data.
Paths cannot escape the project, name secrets or have case aliases. Commands
use argv directly, without shell expansion. Computation runs offline in the
approved protected container; required packages and models must already be in
its image or declared inputs.

Defaults are one CPU (`cpu_millis: 1000`), 512 MiB RAM, 256 MiB sandbox disk,
1 GiB CUDA VRAM, `accelerator: "auto"` and a 300-second task timeout. CPU
variants always reserve zero VRAM. The CLI shows the effective values before
approval. Current limits are:

- 24 ordered stages; 32 data globs and 32 input globs per stage.
- A code snapshot of at most 10,000 files / 256 MiB, and up to 511 separate
  data files / 1 GiB in the CLI workflow.
- At most 4 CPUs, 4 GiB RAM, 512 MiB sandbox disk and 4 GiB CUDA VRAM per task,
  further bounded by the registered environment and device's actual offer.
- The task-timeout schema accepts 1 second to 24 hours, but new CLI project
  previews currently set a 30-minute job deadline. The job deadline bounds
  execution and capacity waiting; the protocol's absolute validation cap is
  48 hours. This workflow does not yet expose a longer job-deadline option.
- Up to 32 declared output paths per stage. Each needs `max_bytes`; `required`
  defaults to true. Outputs and temporary files must fit the disk reservation.
  The first output path component `stages` is reserved, case-insensitively;
  `stages.json` is allowed.

The snapshot excludes declared data, credentials, state, results, virtual
environments and symlinks. Use `.campusignore` for raw downloads and unrelated
files too. Data uploads are separate and checksummed; reuse requires the
coordinator to confirm that the authenticated account owns the exact bytes.

A standalone `train.py`, an arbitrary stateful program or a PyTorch DDP job
does not become distributed merely by uploading it. This contract supports
independent file tasks and explicit reductions. It does not provide DDP
rendezvous, synchronized neural-network gradients, or combined device memory.

## Computers, CPU and CUDA

Workers must be online, reachable through the same coordinator, and offering
enough compatible resources and approved images. Leave their foreground
connection windows running. Paused, offline, busy or incompatible computers do
not provide capacity; the job can wait until a suitable offer becomes available.
Connecting multiple computers does not guarantee that each will receive work.

Apple Silicon runs native ARM64 **CPU** containers; Metal is not supported.
CUDA requires a supported NVIDIA GPU, working driver/runtime, successful device
preflight and local GPU consent. `accelerator: "auto"` uses eligible CPU/CUDA
variants and accepted throughput observations; it never enables GPU permission
on a device. An explicit `cuda` stage waits for a compatible CUDA offer.

## Reproduce the Fashion-MNIST example

The [complete example](../examples/fashion-mnist/README.md) trains one global
nearest-centroid model, evaluates disjoint test shards, and merges all
predictions. Its training sufficient statistics are merged exactly; these are
not separate final models trained independently on each shard.

Use the Python/NumPy preparation environment described in that README. From
the repository root, after signing in:

```text
cd examples/fashion-mnist
python -m pip install numpy==2.2.6
python prepare.py
campus run --wait --to results/pool
python reference.py --output results/reference.json --check results/pool/result.json
```

Preparation verifies the pinned official dataset and creates immutable shards.
Keep the generated root `manifest.json` in the code snapshot; `.campusignore`
already excludes raw downloads, data and results. The DAG is `fit` map →
`model` reduce → `evaluate` map → `aggregate` reduce. The final `result.json`
contains complete predictions and metrics. To verify the learned parameters
as well, pass `--model` to `reference.py` using the downloaded
`stages/model/<task-id>/model.json` path.

The example README explains the independent reference and standalone baseline.
Compare equivalent resources, partitions and correctness checks; account for
uploads, queueing and container startup. Physical participation, CUDA acceptance
and performance numbers belong in the verification report for the actual run.
