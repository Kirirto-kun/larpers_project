# Results and measurement evidence

**Status: no physical-pool performance results are available in this branch.** UI fixtures and automated tests use synthetic data solely to verify behavior. They do not prove throughput, CUDA availability, recovery, or multi-device execution.

## Required experiment record

Capture the final integrated git SHA, experiment ID, exact compute-node IDs, immutable input manifest and workload/model/image/adapter digests, tokenizer/pooling/precision/chunk policy, correctness policy, input-length distribution, offered budgets, batch policy, concurrency, cold/warm mode, and measurement window. Disable answer caches for evaluation; disclose immutable model caches. Generate fresh validation reference for changed inputs and record its device/time separately.

Before a pool run, measure each participating compute device standalone in the same runtime with matched limits and quality. Keep raw-local and single-node-through-service results separate; Pi uses standalone medians, never an intentionally worse baseline. Record at least three comparable repeats with median and range. Different settings or units require separate experiments.

Ppool counts unique, correct, accepted useful units divided by elapsed time. Retry execution contributes to elapsed time, not the numerator. End-to-end includes transfer, queue, startup, compute, verification and aggregation. Show steady-state, cold start and LLM planning time/cost separately; the full user journey includes planning.

- Efficiency = Ppool / sum(Pi) × 100% for exactly the participating compute nodes.
- Speedup = Ppool / max(Pi).
- A missing/zero baseline gives insufficient data.
- Above 100% is retained and investigated for batching, baseline and distribution differences.
- CPU/GPU utilization at 1,000 ms intervals is not pooling efficiency; publish raw samples, means, windows and gaps.

Identify at least one measured bottleneck (network, model startup, validator, disk, tail latency, or the single GPU). Keep fault injection and normal throughput in different series. Include the stop/retry timeline and evidence that duplicate/late completion does not inflate accepted units or credits.

## Reproduce a report

`python3 tools/benchmark/run.py --base-url https://COORDINATOR --config experiment.json --out docs/submission/results/RUN_ID`

Set CAMPUS_BENCHMARK_TOKEN in the environment to an authorized scoped credential; do not commit it. The collector saves configuration, server experiment ID, snapshots, and raw export. Its provisional raw-export shape is documented in tools/benchmark/README.md; adapt at integration if needed.

`python3 tools/benchmark/report.py --input docs/submission/results/RUN_ID/raw.json --out docs/submission/results/RUN_ID/report`

Keep raw files alongside report.json, report.md, report.html and rates.csv. Do not replace missing measurements with fixture output. No energy, monetary cost, user demand or speedup measurement is claimed until its evidence is recorded.
