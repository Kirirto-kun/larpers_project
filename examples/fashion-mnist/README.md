# Fashion-MNIST: one model trained across devices

This project trains **one nearest-centroid classifier** from all 60,000 official
Fashion-MNIST training images, then evaluates all 10,000 official test images.
It is a complete CPU/CUDA map/reduce training job. The learned parameters are
ten 784-dimensional class centroids. This algorithm is deliberately small and
inspectable; it does not use neural-network gradient synchronization or pool
device memory.

The [official Zalando dataset](https://github.com/zalandoresearch/fashion-mnist)
contains 28 × 28 grayscale images in ten clothing classes. `prepare.py` downloads
the four compressed IDX files from the official repository at the pinned commit
`b2617bb6d3ffa2e429640350f613e3291e10b141`, over HTTPS, and verifies the
maintainer's published MD5 values. It records their SHA256 values and binds each
prepared input shard to its own SHA256. See `DATASET_LICENSE.txt` for the
dataset's MIT license.

## Run the project

Use Python 3.11 and NumPy 2.2.6 for preparation and the CPU reference. The
repository's protected CPU/CUDA images already contain NumPy and PyTorch 2.9.1.
CPU workers need no PyTorch import. CUDA workers require a working CUDA runtime;
selecting CUDA without one fails explicitly.

From this directory:

```text
python prepare.py
campus run
```

`campus run` reads `campus.json`, reviews the declared stages, uploads the code
and 22 immutable input shards, and submits one DAG job. Sign in with `campus
login` when needed. Follow the CLI's plan and sharing review. Dataset bytes are
ordinary job inputs; they are not needed for the LLM's explanation of this plan.

Preparation creates an ignored `dataset/` directory, containing 12 training
shards of 5,000 images and 10 test shards of 1,000 images, plus the cached raw
archives under `dataset/raw/`. The ignored, generated **project-root
`manifest.json` must be included in the code snapshot**. It is small metadata
that binds the shard counts, ranges and checksums. Only `dataset/*.npz` are
declared data inputs. Keep local outputs outside the project, or under the
ignored `results/` directory.

`.campusignore` keeps the entire `dataset/` directory, including cached raw
archives, out of the code ZIP. Explicit `campus.json` data globs still select
the 22 NPZ files for separate uploads. Generated `manifest.json` remains in the
code ZIP; tests and local outputs/caches are excluded.

Preparation is reproducible and cached archives are rechecked on every run.
An invalid cached archive fails instead of being silently reused. When changing
`--train-shard-size` or `--test-shard-size`, use a fresh project dataset directory
or move obsolete `.npz` shards first; stale shards are rejected. A task works on
one immutable shard and never downloads data.

## The four stages

| Stage | Work | Accepted output |
| --- | --- | --- |
| `fit` | Map every training shard to exact int64 class sums and counts | `partial.json` |
| `model` | Sum every unique training partial and divide by global class counts | `model.json` |
| `evaluate` | Map each test shard through the same accepted global model | `predictions.json` |
| `aggregate` | Verify complete unique coverage and merge predictions/metrics | `result.json` |

Merging sufficient statistics is exactly equivalent to training the classifier
on the concatenated training data. No shard trains an independent final model.
The classifier uses squared Euclidean distance on the original uint8 pixels,
with float64 scores and the lower class number winning an exact tie. Integer
training sums are exact on CPU and CUDA. Reductions run on CPU. The maps allow
the scheduler to choose CPU or a verified, consented CUDA offer.

Every partial records the input checksum, count and item IDs. The model retains
all training provenance, exact sufficient statistics and centroids. Every
evaluation binds both the numerical model ID and the accepted model file's
SHA256, and records labels and predictions in source order. The final reduction
rejects missing/duplicate shards, changed input identities, inconsistent counts,
invalid class labels and mixed models. It produces sorted unique item IDs,
predictions, accuracy, a confusion matrix and accelerator contribution counts.

The runner mounts code at `/workspace`, a task's selected input at
`/inputs/dataset/<shard>.npz`, and accepted predecessor outputs under
`/inputs/stages/<stage-id>/<task-id>/<output-name>`. These paths are the only
runtime convention used by `worker.py`. The commands in `campus.json` use literal
argv and the runner's `{input}` and `{accelerator}` substitutions.

Each map requests one CPU, 1 GiB RAM, 256 MiB scratch and 512 MiB VRAM for CUDA.
Each reduction requests one CPU, 512 MiB RAM and 256 MiB scratch. All stages have
a 120-second timeout and a 1 MiB output limit. The prepared shards total about
31 MB. On the verified official partition, `model.json` is about 520 KB and
`result.json` about 492 KB.

## Independent correctness and a standalone baseline

Run all stages serially on one machine, as separate worker processes:

```text
python standalone.py --accelerator cpu --output-dir results/cpu
python reference.py --output results/reference.json --check results/cpu/result.json --model results/cpu/model.json
```

Use `--accelerator cuda` with a CUDA runtime to exercise every map on the GPU.
Choose an empty output directory for each run. `standalone.py` uses the same
stage functions, task sizes, predecessor layout and output validation as the
pool job; `run.json` records per-task wall times and output checksums.

`reference.py` is independent of `worker.py` and `prepare.py`: it reads the
original IDX files, trains with direct NumPy means over complete classes, and
classifies with direct squared differences. `--check` requires exact agreement
for every test prediction, label, item ID and metric. `--model` additionally
checks exact global class counts, pixel sums and centroids. It can check a
downloaded pool `result.json`/`model.json` in exactly the same way.

The official full-dataset CPU run produced **6,768 / 10,000 correct (67.68%)**,
with exact reference agreement. The SHA256 of the 10,000 predicted class bytes,
in original test order, is:

```text
a6a255ce75ad0953eb7264eef89f8500b33634a8a2a89f31941e0f0f2bda1a6b
```

This lightweight workload can spend more time on process/container startup,
transfers and orchestration than on arithmetic. A fair pool comparison uses
the same immutable input manifest, image digest, CPU/memory limits, task sizes,
accelerator policy and correctness reference. Report preparation, transfers,
queueing and container startup separately, along with end-to-end elapsed time.
The standalone report excludes coordinator/data-transfer/container-startup
costs. The independent reference uses a different algorithm implementation and
is a correctness check, not the matched performance baseline.

## Tests

From the repository root:

```text
python -m unittest discover -s examples/fashion-mnist/tests -v
```

Tests use small hand-checked fixtures and run the actual CLI stage chain and
standalone driver. They cover remainder partitions, stable checksums, malformed
IDX data, empty/invalid arrays, exact sums/centroids, global predictions, tie
breaking, changed inputs, missing/duplicate coverage and mixed model outputs.
CPU/CUDA equivalence is exercised when a CUDA device is available, and skipped
explicitly otherwise. CUDA equivalence on the full official dataset must be
verified on the physical GPU runtime before making a pool acceptance claim.
