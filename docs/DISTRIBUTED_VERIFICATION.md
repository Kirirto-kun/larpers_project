# Distributed projects verification — 2026-11-15

**Verified:** one-command project execution, physical multi-device correctness,
nine repeated service jobs, active-worker recovery, and published R8 downloads.

The workflow and supported contract are documented in
[Distributed projects](DISTRIBUTED_PROJECTS.md). The reproducible workload and
independent correctness check are in the
[Fashion-MNIST example](../examples/fashion-mnist/README.md).

## One job on three physical computers

Job **`523021fd-98b6-4698-b26f-d27bbc9ff4e2`** completed **24 of 24 tasks** with
state `succeeded`. It trained one global nearest-centroid model from all
60,000 training images and evaluated all 10,000 test images. The DAG comprised
12 training maps, one model reduction, 10 dependent evaluation maps and one
final reduction.

| Physical computer | Accepted tasks | Observed contribution |
| --- | ---: | --- |
| RTX laptop | 16 | Accepted work, including four tasks with physical CUDA execution |
| Main Windows computer | 7 | Accepted CPU work |
| Mac | 1 | Global model reduction on CPU |
| Total | 24 | One submitted DAG job across three distinct physical computers |

These counts represent accepted tasks, not separate containers presented as
separate computers. The Mac contribution demonstrates ARM64 CPU execution; it
does not establish Metal or CUDA support on macOS. Task counts do not measure
relative utilization or performance.

## Independent result verification

An independent reference read the original Fashion-MNIST IDX files. The
downloaded global model matched its complete class counts, integer pixel sums
and centroids. Every test item ID, label and prediction agreed with the
reference; coverage was complete and unique.

| Result | Verified value |
| --- | --- |
| Training coverage | 60,000 / 60,000 images |
| Test coverage | 10,000 / 10,000 images |
| Correct predictions | 6,768 |
| Accuracy | 67.68% |
| Prediction-byte SHA-256 | `a6a255ce75ad0953eb7264eef89f8500b33634a8a2a89f31941e0f0f2bda1a6b` |
| Numerical model ID | `779002763bd52a9095afa63569525daa99a82d94acd5afb03f421f9347d8e208` |

The prediction hash covers the 10,000 predicted class bytes in original test
order. The model ID is the workload's numerical identity, not a substitute for
the downloaded artifact's own SHA-256 check. Accepted outputs also retain the
coordinator's artifact size/checksum metadata.

This proves one global model assembled from distributed sufficient statistics.
It does not claim synchronized neural-network training or combined device
memory.

## Automated and browser checks

| Check | Observed result |
| --- | --- |
| Complete server suite on isolated PostgreSQL | 227 passed, 65.17 seconds |
| Complete Go runner suite | Passed |
| Go vet and focused app/worker race checks | Passed after the diagnostic fix |
| Workload and Fashion-MNIST tests | 68 passed, one host-CUDA skip, 108.44 seconds |
| SDK tests | 193 passed, 23.61 seconds |
| Production portal and console builds | Passed |
| Contract export/check and isolated generated-Python validation | Passed |
| Real member browser view | All 24 accepted outputs visible; desktop and mobile checks passed |
| Authenticated browser download | Downloaded bytes matched the artifact SHA-256 |

The host-CUDA test skip describes that local test environment. Physical CUDA
execution was separately observed in the successful job above. Browser checks
demonstrate the deployed member flow in addition to API and fixture tests.

All five original final-review findings were closed before this verification
checkpoint. Credentials, private configuration and raw dataset bytes are not
included in this report.

## Load failure and diagnostic correction

The initial CPU-only three-job load window was not a successful benchmark.
One evaluation task failed before the execution grant. An instrumented repeat
retained the exact cause: `PermissionError: [Errno 13]` when the Linux guard
atomically replaced `challenge.tmp` with `challenge.json`. Native Windows Go
readers omitted delete sharing, blocking that replacement through Docker
Desktop's bind mount. All failed windows remain excluded from performance
claims; other submitted jobs were allowed to drain and settings were restored.

The runner now captures bounded, redacted diagnostics before the grant and uses
read-only shared-delete handles for both challenge/state readers. This does not
grant write/delete rights or change the image, nonce protocol, lease fence or
timeout. An opt-in regression held the actual native reader open while the
existing Linux image performed `os.replace`: the old reader reproduced errno
13, and the new reader passed while retaining its immutable old snapshot. Long
Unicode paths over 300 characters, UNC spelling, read-only access and rejection
of final symlinks/nonregular files were checked. The full Go suite, targeted
race checks and vet passed after the fix.

The final quiet repeat used the published R8 binary
`b3001ad53cf57148e718be38ed89d929556a07c4de1963852f13355dd042eca8`.
The owner restarted the physical RTX client with R8 before measurement. All
nine jobs completed: **216/216 accepted tasks, no failed units**, with exact
independent model/prediction agreement. The startup failure did not recur.

## Service performance comparison

The comparator is
[`tools/qa/project_benchmark.py`](../tools/qa/project_benchmark.py). It runs three
job windows with the same immutable project: the main CPU computer alone, the
RTX laptop alone, then those two physical computers together. The Mac's accepted
model reduction belongs to the functional proof above and is not included in
this two-node performance comparison.

These are **single-node service baselines**, not bare-metal baselines. Each
window includes all three CLI planning/submission calls, API/queueing, input
transfers, container startup, computation, verification, result downloads and
independent comparison. Every job must finish with complete accepted work on
the allowed physical nodes and exact reference agreement before a phase counts.

| Phase | Jobs verified | Complete window | Correct predictions/second | Device/accelerator contributions |
| --- | --- | --- | --- | --- |
| CPU computer only | 3 / 3 | 374.984 s | 54.146 | 72 CPU tasks on main computer |
| RTX laptop only | 3 / 3 | 124.719 s | 162.798 | 72 CPU tasks on RTX laptop |
| Two-node pool | 3 / 3 | 125.484 s | 161.805 | Main 20 + RTX laptop 52 CPU tasks |

The report defines throughput as correct predictions divided by the complete
batch window. Pool speedup is pool throughput divided by the faster single-node
service throughput. Pool efficiency is pool throughput divided by the sum of
the two single-node throughputs. Measured speedup was **0.994×** relative to the
faster laptop, and efficiency was **0.746**. This workload showed **no useful
speed gain over the RTX laptop alone**; no statistical significance is claimed
for the 0.6% difference. Task granularity, transfer/startup costs and heterogeneous
device capacity remain relevant optimization work. These timings cannot
separate pure compute from every startup/transfer cost.

The map accelerator policy stayed `auto`. Its accepted throughput observations
selected CPU for this small classifier; these are laptop service measurements,
not GPU-only performance numbers. Physical CUDA capability was separately
verified. Per-task image identities, input/code hashes, limits, task sizes and
correctness checks stayed fixed. Cache temperature and available host resources
were not forcibly normalized; before/after offers were retained. Build/test
activity stopped during the timed windows. All original desired settings were
restored, including the Mac excluded from this two-node comparison.

Job receipts (each completed 24/24):

- CPU: `8d377d91-6220-48cf-abf3-0c5c705e5003`, `ebfa05f9-48c3-4ca5-8b24-e798bc765126`, `a09db855-d843-4306-9752-f418a2c32b60`.
- RTX laptop: `e1ce111b-1637-45b2-8025-670225d8727f`, `882dfa07-d059-4bda-bac4-67d2aa5c203e`, `ebb293af-352e-4cf4-92f9-4754b67827cf`.
- Pool: `defa1cf7-f143-45b7-8e21-7c3f89b5aec2`, `86c8e9fe-6cac-4ea9-aecc-355d51537fc2`, `6f87ce49-9167-4c29-9b6b-3ccff3e7b600`.

There is no public read-telemetry endpoint. A read-only operator export retained
36 real samples across the original proof, nine measured jobs and recovery;
this sparse sampling does not establish whole-window utilization. No utilization
percentage is fabricated from offered capacity or missing samples.

A native standalone diagnostic completed 24 worker processes in **23.698
seconds**. Its execution conditions are unmatched to the service windows, so
that number is not used as a baseline or a speedup denominator.

## Interruption and recovery

Job `172d1a24-79af-4be3-b851-1d05854fe250` completed **24/24** after local
`leave --stop` interrupted the root-owned main worker with two active tasks.
One already accepted task retained its original attempt identity. Unfinished
tasks `ff0394ce-0f1b-476c-aa3f-1445ce907502` and
`f96bb90f-4bc7-4a1f-9354-caa2b618f4b5` moved from generation 1 on the main
computer to generation 2 on the physical RTX laptop. Only unfinished work was
retried; final unique acceptance, model arrays and every prediction matched the
reference. All final accepted work in this recovery job came from RTX. The
test used a real worker stop, not a physical power/network cut. Late-completion
fencing is additionally covered by the PostgreSQL integration tests.

The Mac's original desired settings were restored. The root-owned test worker
was stopped cleanly; user-owned RTX/Mac processes were not terminated.

A separate post-update CUDA job `200fd2bf-be42-468a-be67-ce4087a6c90f` ran on
the RTX laptop through the existing website example pipeline. All **65,536**
matrix values matched independent exact integer CPU dot products, with maximum
absolute error **0** and matrix SHA-256
`c80c02c52733ea80a50a317e411d955f8971866f8be4321590cd893485a1cc94`.

## Release and remaining boundaries

Release **`runner-20261115-r8`** is published on the LAN. Full non-image download
sizes and SHA-256 values, all three image Range responses, starter attachment
headers, private-path 404 responses and the authenticated advertised release
passed strict CA-verified HTTPS checks. The release builder fully validated
the unchanged image archives. Known private credentials were absent from the
changed source and public clients/ZIP contents (107 files, 17 private values
checked). No enrollment invitation is included.

The [developer CLI downloads](DISTRIBUTED_PROJECTS.md#get-the-developer-cli-and-sign-in)
include six native OS/architecture builds and a public CA. A
[source example ZIP](https://10.4.146.76:8443/downloads/runner-20261115-r8/fashion-mnist-project.zip)
contains preparation/worker/reference scripts, without dataset bytes.
The deployed API image identity is
`sha256:c4e17c3b8f05595fbd4101272704ebfebcc2e4264453a47db9d9e958fc9fbf3f`.
The canonical private Compose override preserves TLS, data and all older
download routes; PostgreSQL has no published host port. Use the
[live handoff](../bootstrap/LIVE_SERVER.md) for download identities.

The supported distributed interface remains an explicit file map/reduce DAG,
including dependent maps. Arbitrary `train.py` scripts and DDP programs are not
automatically split. There is no Metal execution, synchronized gradient
exchange, or pooled VRAM. CUDA still requires a verified NVIDIA offer and local
consent. Eligible devices must remain online with enough offered resources.

New CLI project previews currently have a **30-minute job deadline**, including
capacity waiting. The broader schema's task-timeout and absolute deadline caps
do not extend that workflow deadline. Structural artifact acceptance checks
execution/output integrity; the independent application reference is what
establishes the Fashion-MNIST numerical result.
