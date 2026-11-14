import type { Benchmark } from "../lib/types";
import { median, comparison } from "../lib/benchmark";
import { Metric } from "./Common";
export function ThroughputChart({ report: b }: { report: Benchmark }) {
  const c = comparison(b);
  const rows = [
    ...[...new Set(b.node_ids)].map((id) => ({
      name: id,
      value: median(b.standalone.find((s) => s.node_id === id)?.rates || []),
    })),
    { name: "Single via service", value: median(b.single_service_rates) },
    { name: "Pool", value: median(b.pool_rates) },
  ];
  const max = Math.max(1, ...rows.map((r) => r.value ?? 0));
  return (
    <>
      <div className="metrics">
        <Metric
          label="Pool throughput"
          value={median(b.pool_rates)?.toFixed(2) ?? "—"}
          hint={b.unit + "/s"}
        />
        <Metric
          label="Pool efficiency"
          value={c ? c.efficiency.toFixed(1) + "%" : "Insufficient data"}
        />
        <Metric
          label="Speedup vs best node"
          value={c ? c.speedup.toFixed(2) + "×" : "—"}
        />
        <Metric
          label="Repeats"
          value={b.pool_rates.length}
          hint={b.cold ? "Cold start" : "Warm model cache"}
        />
      </div>
      {c && c.efficiency > 100 && (
        <div className="notice">
          Efficiency exceeds 100%. Check the baseline and workload settings.
        </div>
      )}
      {rows.map((r) => (
        <div
          className={
            "chart-row comparison-row" +
            (r.name === "Pool" ? " pool-comparison" : "")
          }
          key={r.name}
        >
          <span>{r.name}</span>
          <div className="comparison-track">
            <div
              className="bar"
              style={{
                width: (r.value === null ? 0 : (r.value / max) * 100) + "%",
              }}
            />
          </div>
          <span>
            {r.value === null ? "Missing" : r.value.toFixed(2)} {b.unit}/s
          </span>
        </div>
      ))}
      <details>
        <summary>Raw rates</summary>
        <p className="muted">
          Pool: {b.pool_rates.join(", ") || "No samples"}. Range:{" "}
          {b.pool_rates.length
            ? Math.min(...b.pool_rates) + "–" + Math.max(...b.pool_rates)
            : "Unknown"}
          .
        </p>
      </details>
    </>
  );
}
