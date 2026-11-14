# Benchmark driver and report

Standard-library Python tools, with no direct database/scheduler mutations. Tests run with `python3 -m unittest discover -s tools/benchmark/tests -v` or the planned `uv run --project server pytest tools/benchmark/tests -q` after the server branch arrives.

`run.py --base-url URL --config FILE --out DIR` submits `/api/v1/benchmarks`, saves the returned ID and polls until terminal state, then saves the JSON export. Use `CAMPUS_BENCHMARK_TOKEN` for scoped authorization. Remote URLs require HTTPS. A timeout preserves collected snapshots and never silently starts a replacement experiment. Repeat the same command with `--resume` to observe its saved ID. Changed configuration/origin and a nonempty output directory without resume are rejected. HTTP redirects are rejected to avoid forwarding credentials.

`report.py --input raw.json --out report-directory` creates JSON, CSV, Markdown and escaped HTML. The provisional raw shape below is local to these tools; adapt transport at integration when the authoritative foundation schema is available.

```json
{
  "config": {
    "experiment_id": "recorded-id",
    "git_sha": "final-sha",
    "compute_node_ids": ["cpu-1", "gpu-1"],
    "workload_digest": "immutable-digest",
    "input_digest": "immutable-digest",
    "quality_policy": "full-reference-v1",
    "resource_settings": {"cpu_percent": 50},
    "unit": "fragments",
    "input_distribution": "manifest-digest",
    "batch_policy": {"target_units": 16},
    "concurrency": 2,
    "cold": false,
    "measurement_mode": "normal",
    "timing_scope": "end_to_end",
    "model_digest": "immutable-model",
    "image_digest": "immutable-image",
    "adapter_digest": "immutable-adapter",
    "precision": "fp32",
    "result_cache_enabled": false
  },
  "records": [],
  "timings_ms": {},
  "utilization": [],
  "planning": {},
  "costs": {},
  "bottleneck": "Not measured"
}
```

Each record repeats all matching config fields plus `experiment_id`, unique `record_id`, `kind` (`standalone`, `single_service`, `pool`), positive finite `elapsed_ms`, and `results: [{"unit_id":"stable-id", "state":"accepted"}]`. Standalone and single_service records specify `node_id` and local `provenance`; pool records specify exactly the series `compute_node_ids`. Use one accepted result per useful fragment/image ID; duplicate accepted IDs are counted once within a repeat. Failed/rejected results never count. Separate record IDs identify intentional independent repeats.

Modes `normal` and `fault_injection`, and scopes `end_to_end` and `steady_state`, cannot be combined. Model/image/adapter digests and precision must match. Result caches must be disabled. Missing result lists are rejected rather than counted as zero.

Every compute node needs a positive standalone baseline; missing data yields an insufficient-data report. The report preserves efficiency above 100% with a methodology flag. At least three repeats are required for formal results. Signatures prove local provenance, not that local timing is independently trustworthy. The final foundation must provide full validation/provenance and server acceptance evidence.
