# Distributed projects from one command

The user explicitly authorized full implementation and testing after the design
discussion, delegated selection of Fashion-MNIST or another suitable workload,
and requested parallel work. This design records engineering choices within that
authorization; implementation proceeds without another approval round.

## Outcome

From a project directory, `campus run` discovers `campus.json`, snapshots code,
uploads declared datasets, obtains a reviewable plan through the existing LLM
preview pipeline, submits one DAG job, follows its tasks and downloads results.
The same command accepts a noninteractive reviewed configuration for CI. All
tasks share one job and execute on multiple real physical devices. Existing
explicit-command `run`, website, device onboarding and local stop remain usable.

The first complete project trains one Fashion-MNIST nearest-centroid classifier
from distributed class sums/counts, merges one global model, evaluates disjoint
test shards across devices and aggregates predictions/accuracy. This is a real
single-model map/reduce algorithm with an exact standalone reference; it does
not require synchronized neural-network gradient exchange or merge VRAM.

## Project contract

`campus.json` is UTF-8 JSON with `schema_version:"1"`, `name`, `data` (relative
file globs), `prompt` (optional) and ordered `stages`. A stage has `id`, `kind`
(`map` or `reduce`), `inputs` (globs selecting declared data files), `depends_on`,
`command` (literal argv), `outputs` (`path`, `max_bytes`, optional `required`),
`accelerator` (`auto`, `cpu`, `cuda`), `resources` (`cpu_millis`, `memory_bytes`,
`disk_bytes`, `vram_bytes`) and `timeout_seconds`. Defaults are bounded and shown
before execution. `{input}` is allowed only in map argv and expands to that
task's `/inputs/<relative-path>`; `{accelerator}` becomes the selected variant's
`cpu` or `cuda`. No shell interpolation or arbitrary planner substitutions.

Map creates one task for each matching immutable input file. A dependent map
stage is split too, but waits for accepted predecessor outputs. Reduce creates
one task. Accepted upstream outputs are mounted read-only under
`/inputs/stages/<stage>/<task-id>/<output-path>`. User map/reduce code executes
only in the native protected container, never in the coordinator process.

New PreviewRequest fields: `project_config` (the above object), with ordinary
`project_artifact_id`, `inputs`, `selected_files`, `share_with_llm`. Input mount
paths preserve project-relative data paths under `/inputs/`. The project ZIP is
bound at `/workspace`. A trusted `campus.project-map@1` adapter prepares tasks
from the validated configuration; `campus.project-reduce@1` handles reductions.
The server checks LLM-proposed stage identities, dependency graph, commands,
input selection, output declarations and accelerators against the contract.

Resource scheduling is deterministic and bounded by actual device offers.
`auto` permits CPU and verified CUDA variants, with no GPU consent escalation.
First planning may read only explicitly selected bounded text. Dataset bytes,
credentials and unrelated files are not sent to the provider. Reviewed unchanged
configuration can be reused by CI; idempotent submission and frozen revisions
prevent accidental duplicate jobs.

## CLI and data

`campus login` signs in over trusted HTTPS using email/password, receives a
revocable CLI-scoped credential, and stores it outside the project. CI can use
`CAMPUS_TOKEN`; credentials never go in argv, project archives or output logs.
`campus run` without a command discovers `campus.json`; if absent it reports
entry/data candidates and explains how to create a supported map/reduce project.
It prints its code/data inventory and review, supports `--yes` only with explicit
saved sharing/configuration, `--wait`, output directory selection and safe resume.
Data files are content-addressed, uploaded separately with bounded checksummed
parts, and reused only within the same authenticated account after ownership
validation. Code snapshots exclude data, state, results, credentials and venvs.

## Evidence and limits

Test account isolation, unsafe paths/globs, metadata limits, literal argv,
configuration/LLM mismatch, dependent map splitting, output names, retries,
late completion fencing, duplicate counting, dataset cache misses and mutation,
CLI prompt/CI behavior, password secrecy and deterministic result aggregation.

Physical acceptance requires a single submitted job with useful accepted tasks
from the main machine and RTX laptop, one trained model, complete unique test
coverage, independent standalone agreement and recorded per-device contribution.
Also test an interrupted worker and recovery of only unfinished work. Benchmark
matched standalone and pool workloads with identical correctness/resources;
report throughput, transfer/preparation, utilization and bottlenecks honestly.
Unknown arbitrary scripts are not automatically proven parallelizable. The
supported contract can express other independent batch and map/reduce projects.

## Approaches

Manual manifests alone are predictable but leave discovery and usability gaps.
Fully autonomous rewriting lacks a reliable correctness contract for arbitrary
stateful code. The selected hybrid keeps explicit executable semantics and uses
the LLM to inspect/explain the plan, while trusted code validates and executes it.
Existing HTTP leases, native containers, uploads and DAGs are reused instead of
introducing a second distributed framework or peer-to-peer privileged services.
