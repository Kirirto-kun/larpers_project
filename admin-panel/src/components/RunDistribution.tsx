import type { Benchmark } from "../lib/types";
import { median } from "../lib/benchmark";

export function RunDistribution({ report }: { report: Benchmark }) {
  const series = [
    { name: "Pool", values: report.pool_rates, color: "#12996a" },
    {
      name: "Single via service",
      values: report.single_service_rates,
      color: "#899a90",
    },
  ].map((s) => ({
    ...s,
    values: s.values.filter((v) => Number.isFinite(v) && v >= 0),
  }));
  const max = Math.max(1, ...series.flatMap((s) => s.values)) * 1.1;
  return (
    <section className="run-distribution" aria-label="Run distribution">
      <div className="section-heading">
        <h3>Run distribution</h3>
        <small>{report.unit}/s</small>
      </div>
      {series.map((s) => (
        <div className="distribution-row" key={s.name}>
          <span>{s.name}</span>
          <svg
            viewBox="0 0 500 36"
            role="img"
            aria-label={
              s.name + " samples: " + (s.values.join(", ") || "not measured")
            }
          >
            {[0, 1, 2, 3, 4].map((i) => (
              <line
                key={i}
                x1={10 + i * 120}
                x2={10 + i * 120}
                y1="0"
                y2="36"
                stroke="#e9eee9"
              />
            ))}
            {s.values.length > 0 && (
              <line
                x1={10 + (Math.min(...s.values) / max) * 480}
                x2={10 + (Math.max(...s.values) / max) * 480}
                y1="18"
                y2="18"
                stroke={s.color}
                strokeWidth="2"
                opacity=".35"
              />
            )}
            {s.values.map((v, i) => (
              <circle
                key={i}
                cx={10 + (v / max) * 480}
                cy={18 + ((i % 3) - 1) * 6}
                r="3.5"
                fill={s.color}
              >
                <title>
                  Run {i + 1}: {v} {report.unit}/s
                </title>
              </circle>
            ))}
            {s.values.length > 0 && (
              <path
                d={`M${10 + (median(s.values)! / max) * 480} 5v26`}
                stroke={s.color}
                strokeWidth="2"
              >
                <title>Median: {median(s.values)}</title>
              </path>
            )}
          </svg>
          <strong>{median(s.values)?.toFixed(2) ?? "—"}</strong>
        </div>
      ))}
      <div className="distribution-axis">
        <span>0</span>
        <span>{(max / 2).toFixed(1)}</span>
        <span>{max.toFixed(1)}</span>
      </div>
      <small className="chart-caption">Dots: runs · line: median</small>
    </section>
  );
}
