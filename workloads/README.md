# Registered Campus workloads

This is the isolated workload runtime for part 4. It has its own Python environment
and lockfile. The coordinator calls its JSON executable in a prepared, bounded,
no-network runtime. It does not import PyTorch or user validators into the API.

The static registry contains `embeddings.e5@1`, `training.image-classifier@1`, and
`generic.project@1`. No request can select a Python module, remote implementation,
HF custom code, or arbitrary validator. Generic project commands execute through
the runner's workload container; this adapter never executes those commands.

## Run locally

Run the following commands from the repository root after preparing the locked
environment and all inputs. `--offline` keeps uv from contacting its indexes.
The CPU extra selects CPU PyTorch; the CUDA extra selects the pinned CUDA wheel.
The container profiles enforce `network_policy=none` independently of Python.

```powershell
uv run --offline --project workloads --extra cpu pytest workloads/tests -q
uv run --offline --project workloads --extra cpu python -m workloads.embeddings.run --help
uv run --offline --project workloads --extra cpu python -m workloads.training.run --help
```

The training recipe imports `campus.checkpoint` from the local SDK. The prepared
image/environment must install that SDK or include `sdk/python` on `PYTHONPATH`.
There is no network install at execution time.

## Embeddings and source search

Prepare a trusted `intfloat/multilingual-e5-small` snapshot before the offline run.
Use a real immutable 40-character upstream revision, a BERT configuration with
384 hidden dimensions and at least 512 positions, tokenizer files, and
`model.safetensors` (or safe sharded weights). Materialize files rather than HF
cache symlinks. Unsafe weights and Python model files are rejected. This tool
locks the bytes already present; the pool administrator must approve the model
revision/file lock in the prepared profile. A user-provided lock alone does not
authenticate the model's upstream provenance.

```powershell
uv run --offline --project workloads --extra cpu python -m workloads.embeddings.prepare --model C:/models/multilingual-e5-small --revision REAL_40_CHARACTER_COMMIT_SHA --input ./workloads/examples/documents.jsonl --output ./embedding-manifest.json
uv run --offline --project workloads --extra cpu python -m workloads.embeddings.run --manifest ./embedding-manifest.json --output ./index.json --microbatch-size 16
uv run --offline --project workloads --extra cpu python -m workloads.embeddings.run --manifest ./embedding-manifest.json --search ./index.json --query "How are checkpoints saved?" --top-k 3
```

Use `--device cuda` in a prepared CUDA environment. Requesting CUDA without a
usable device is an explicit error. Only the production local transformer encoder
is reachable from these commands. Tests can inject a deterministic encoder through
the Python function argument to exercise memory failures; it is absent from the
production registry and CLI.

TXT files contain one document per nonempty line. JSONL records contain `text`
and an optional string `id`. UTF-8, NFC Unicode normalization, line numbers,
codepoint chunk sizes/overlap, `passage: ` and `query: ` prefixes, float32,
masked mean pooling, L2 normalization, 384 dimensions, and a 512-token cap are
fixed or bound in the manifest. Character chunks can be truncated by the
512-token cap; tune chunk size before a benchmark series. Blank lines are skipped.
Item IDs bind source bytes, display source, line, offsets, text, and optional ID.
The same data and preprocessing produce the same IDs regardless of map batch size.
Source display names must be unique. Each index row includes source, digest,
line/offsets, original text, and optional ID for inspecting search excerpts.
Search reports the structural or full-reference verification performed during
that invocation; it never trusts a stored index's quality label. Supply a trusted
`--reference` on search to recheck every indexed vector against the reference.

`split` returns tasks with stable `item_ids`. A worker executes a task with
`--task task.json`; it must also receive the complete unchanged input manifest
and the immutable input/model files. Task binding is checked before execution.
OOM retries halve the microbatch and restart the same uncompleted slice. Even at
microbatch 1 an OOM is an error. Item IDs and coverage never change on retry.

## Quality and references

The default embedding verdict is `structural_only`, with
`benchmark_eligible=false`. It checks every returned item's ownership, uniqueness,
dimension, finiteness, unit norm, and complete expected coverage. It does not claim
that structurally plausible vectors are correct.

Before a formal series, a trusted reference device prepares the full reference for
the current manifest, keeping it in validator-only storage. The reference is
never mounted into worker containers or returned as the worker's answer. It
records its device, timestamp, and duration separately from execution/validation.

```powershell
uv run --offline --project workloads --extra cpu python -m workloads.embeddings.run --manifest ./embedding-manifest.json --make-reference ./validator-only/reference.json --reference-device-id trusted-cpu-1
```

Local standalone execution can pass `--reference ./validator-only/reference.json`
to validate its computed output before writing the index. Distributed workers must
not receive this option/reference. The JSON `verify` policy is:

```json
{
  "mode": "full_reference",
  "reference_path": "/trusted-reference/reference.json"
}
```

The validator rehashes current files and rejects stale manifest, model, data, or
preprocessing bindings. It compares every assigned vector with absolute tolerance
`0.0005`, cosine similarity at least `0.9999`, and norm tolerance `0.001`.
These are adapter-version constants, written into the reference and unchangeable
through a request. They require calibration on real CPU/CUDA devices before
claiming cross-device benchmark support; they have not been tuned on mismatches.

For per-task verification, the trusted coordinator adds the assignment's
`item_ids` to the policy. The report states `validation_scope=assigned_items`,
the number actually checked, and total manifest items. It never derives this
subset from worker claims. `aggregate` rejects subset policies and requires the
entire manifest exactly once. It revalidates accepted outputs and produces the
searchable index. No sampled validation mode is labeled as full verification.

## Train a real user dataset

The registered recipe trains a small PyTorch CNN with AdamW, StepLR, fixed seed,
deterministic sampling, and fixed RGB resize preprocessing. It uses the user's
images. No images or model downloads occur inside the workload.

The training package configures deterministic cuBLAS before its first Torch/CUDA
import, including the standalone CLI. An unset `CUBLAS_WORKSPACE_CONFIG` defaults
to `:4096:8`; an existing `:4096:8` or `:16:8` is preserved. Other existing values
fail clearly before Torch is imported. Starting after CUDA has already initialized
without a configuration requires a fresh training process. The prepared guard
launches its sanitized environment with `:4096:8`. The actual supported choice
is returned under `runtime.cublas_workspace_config` and included in trainer/model
identity so a changed choice cannot silently reuse a checkpoint or benchmark
identity. Actual GPU training/checkpoint/resume still needs hardware validation.

Copy `examples/training.manifest.json`, set its dataset root, class names,
parameters, and the real prepared image/environment SHA-256 digest. The dataset
directory must contain explicit `train.jsonl` and `validation.jsonl` lists:

```json
{"path":"images/cat-01.png","label":"cat"}
{"path":"images/dog-01.png","label":"dog"}
```

Paths use `/` and must remain under the dataset root. Both lists must contain
images; all classes must appear in training. Duplicate images and overlapping
train/validation contents are rejected, even when filenames differ. Do not use
the example record paths without supplying your own corresponding images.

```powershell
uv run --offline --project workloads --extra cpu python -m workloads.training.run --manifest ./training-manifest.json --output ./training-outputs --checkpoints ./training-checkpoints
```

Container defaults are `/outputs` and `/checkpoints`. `examples/train.py` is a
small entry point for the same registered recipe. Outputs contain
`model.safetensors`, `result.json`, parameters, seed, step, metrics, binding, and
the latest completed checkpoint path. The worker's metrics remain
`pending_independent_verification` until the separate evaluator reconstructs the
registered architecture, safely loads its saved weights, and evaluates the exact
fixed validation split. Pickle is never loaded. Fabricated accuracy/loss or
validation claims are rejected. The policy can set `minimum_accuracy` (default
0) and optional `maximum_loss`; these must be fixed before the series.
Verification proves those validation results, not generalization to unseen data.

Every checkpoint uses `campus.checkpoint.save_checkpoint`: all payloads are
written atomically with content hashes and READY before the immutable directory
is visible. State contains model, optimizer, scheduler, step, sampler order,
position/epoch and generator RNG, Python/NumPy/Torch CPU and CUDA RNG. Tensors use
safetensors and all other state uses tagged JSON. Resume requires exact
code/input/environment/trainer digest compatibility before state restoration.

```powershell
$env:CAMPUS_RESUME_FROM = "C:/checkpoints/step-00000010-xxxxxxxx"
uv run --offline --project workloads --extra cpu python -m workloads.training.run --manifest ./training-manifest.json --output ./resumed-outputs --checkpoints ./resumed-checkpoints
```

`--resume-from` is the explicit equivalent. `--stop-after-steps N` is useful for
testing interrupted/resumed execution; an interrupted result is not benchmark
eligible. New checkpoint names never overwrite a prior attempt's snapshot. The
runner/SDK transport must upload and finalize the complete snapshot before
publishing its attempt-scoped pointer; local creation alone does not publish it.
Custom scripts can use the helper but are not automatically resumable.

## JSON boundary

`python -m workloads.adapter` reads one UTF-8 JSON request from stdin and emits
one JSON response on stdout. Errors are data; stdout contains no logs. Input is
bounded to 16 MiB. Large jobs should split bounded map tasks and use artifact
storage. For `verify`/`aggregate`, supply `output_paths` as an alternative to
inline `outputs`: a list of already prepared local JSON artifact paths (up to
1024 files, 256 MiB per file, 2 GiB total). No artifact URLs are fetched.
All paths refer to already prepared files inside the adapter
container's declared mounts. References and policy are trusted coordinator inputs.

```json
{
  "schema_version": "1",
  "adapter": {"id":"embeddings.e5","version":"1"},
  "operation": "split",
  "payload": {"manifest": {}, "batch_policy":{"items_per_task":128}}
}
```

Replace `manifest` with the actual manifest. Responses always contain
`schema_version`, `result`, and `error`; one of result/error is null. Errors have
`code` and `message`. Registered operations are:

| Operation | Payload | Result |
| --- | --- | --- |
| `validate_input` | `manifest` | valid/input binding and counts |
| `split` | `manifest`, optional `batch_policy` | TaskSpec list |
| `verify` | `manifest`, `outputs`, optional `policy` | quality, coverage, acceptance, validation timing |
| `aggregate` | `manifest`, `outputs` list, optional `policy` | complete bound index/model result |
| `run` E5 | `manifest`, optional `batch_policy`, `device`, `item_ids` | computed vectors, binding, execution timing |
| `run` trainer | `manifest`, optional `output_dir`, `checkpoint_dir`, `resume_from`, `device` | safe weights, metrics, complete checkpoint |

`validate_input` and `verify` responses provide provenance derived by the trusted
registered runtime, never by copying caller-supplied digest labels:

| Field | Derivation |
| --- | --- |
| `manifest_digest` | Current checked input bytes, model/configuration, preprocessing and registered recipe binding |
| `adapter_digest` | SHA-256 of adapter ID/version plus a fixed allowlist of trusted runtime source hashes, with CRLF/CR normalized to LF |
| `adapter_digest_kind` | `trusted_source_lf_sha256` |
| E5 `model_digest` | Canonical validated model ID/revision and complete checked local file lock |
| Trainer `model_digest` | Computed `binding.trainer_digest`: registered recipe, effective validated parameters/classes and actual supported runtime settings |
| Generic `model_digest` | Empty string, with `model_kind=none`; no registered quality model |

E5 declares `model_kind=pretrained_transformer`. Training declares
`model_kind=registered_training_recipe` and returns its binding/recipe. The trainer
model identity describes the recipe rather than its result weights; output
`weights_digest` separately identifies the trained safe file. Training's checkpoint
code binding uses the same portable trusted source digest. Source changes invalidate
old snapshots as required by compatibility checks.

The fixed source list in `provenance.py` covers dispatch, shared helpers, provenance
itself, all registered adapter/backend/state modules and their CLI entry points.
It reads source bytes from the prepared immutable image and imports no user paths
or external ML code for hashing. Changing any registered runtime source changes
the bundle digest for every adapter; line-ending conversion alone does not.
Dependency binaries/SDK are additionally bound by the prepared image digest, not
claimed to be hashed by this source-only digest. These sources must remain immutable
inside the trusted profile for the digest to identify the executing code.

For formal benchmarks, trusted `validate_input` preflight establishes expected
manifest/model/adapter identity before measurement. The coordinator or standalone
runner must compare all three `verify` fields against those frozen expectations
and any supplied assignment labels, rejecting missing/mismatched provenance.
An omitted model label can be populated only from that trusted preflight, then
must match the independent verification result. Invalid inputs/rejected reports
can have empty unknown identities and are never eligible. Generic remains
ineligible even with valid source/manifest provenance. The offline adapter does
not authenticate reference device registration or a caller's metadata labels.

The generic adapter never implements `run`. Its validation/aggregation returns
`quality=unverified`, `benchmark_eligible=false`, and states that files were saved
without quality verification. `generic.adapter.snapshot/collect_outputs` can
capture new/changed writable project files, including a conventional `model.pt`,
without loading them. It excludes common secret files/directories, environment
files, keys, Git/venv content, and simple `.campusignore` globs; rejects symlinks
and quota excess; and does not silently drop oversized models. The runner also
collects declared `/outputs` and publishes file artifacts.

## Evidence and limits

Tests use a real locally generated one-layer random BERT with E5's 384-dimensional
interface to check transformer execution, masks, local-only loading, and unit
normalization. It is a fixture, not a pretrained E5 quality reference. Test-only
encoders force OOM paths. Real small image training and independent evaluation
run on CPU; interrupted/resumed weights match an uninterrupted run byte for byte.
Test images are generated labeled fixtures, not evidence of accuracy on a real
user's dataset. Tests block Python socket connections during real encoder smoke;
container-level offline isolation is a separate guard/profile check.

Real pretrained multilingual quality, actual CUDA execution/tolerance, resource
limits, two-device distribution, checkpoint publication/fencing, Docker builds,
and benchmark timing on enrolled machines still require deployment checks. No
test result here establishes those claims. Hash/model loading and verification
have explicit local costs; coordinator split/aggregate/runtime overhead should
also be measured outside the subprocess in benchmark inventory/timings.
