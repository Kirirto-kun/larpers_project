import type { Benchmark } from "../lib/types";

export function RunDistribution({ report }: { report: Benchmark }) {
  const series = [
    { name: "Pool", values: report.pool_rates, color: "#12996a" },
    {
      name: "Single via service",
      values: report.single_service_rates,
      color: "#8d9eae",
    },
  ];
  const valid = (v: number) => Number.isFinite(v) && v >= 0;
  const max =
    Math.max(1, ...series.flatMap((s) => s.values.filter(valid))) * 1.15;
  const count = Math.max(1, ...series.map((s) => s.values.length));
  const x = (i: number) => 48 + (i / Math.max(1, count - 1)) * 660;
  const y = (v: number) => 210 - (v / max) * 180;
  return (
    <section className="run-distribution" aria-label="Benchmark runs">
      <div className="section-heading">
        <h3>Throughput by run</h3>
        <small>{report.unit}/s</small>
      </div>
      <svg
        className="run-line-chart"
        viewBox="0 0 740 255"
        role="img"
        aria-label={series
          .map(
            (s) =>
              s.name +
              ": " +
              (s.values.filter(valid).join(", ") || "not measured"),
          )
          .join("; ")}
      >
        {[0, 1, 2, 3, 4].map((i) => (
          <g key={i}>
            <line
              x1="48"
              x2="708"
              y1={210 - i * 45}
              y2={210 - i * 45}
              stroke="#e7ede7"
              strokeDasharray={i ? "3 5" : undefined}
            />
            <text x="35" y={214 - i * 45} textAnchor="end">
              {((max * i) / 4).toFixed(1)}
            </text>
          </g>
        ))}
        {series.map((s) => {
          const segments: string[] = [];
          let points: string[] = [];
          s.values.forEach((v, i) => {
            if (valid(v)) points.push(`${x(i)},${y(v)}`);
            else {
              if (points.length) segments.push(points.join(" "));
              points = [];
            }
          });
          if (points.length) segments.push(points.join(" "));
          return (
            <g key={s.name}>
              {segments.map((p, i) => (
                <polyline
                  key={i}
                  points={p}
                  fill="none"
                  stroke={s.color}
                  strokeWidth="2.5"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ))}
              {s.values.map(
                (v, i) =>
                  valid(v) && (
                    <circle
                      key={i}
                      cx={x(i)}
                      cy={y(v)}
                      r="4"
                      fill="#fff"
                      stroke={s.color}
                      strokeWidth="2"
                    >
                      <title>
                        {s.name} · run {i + 1}: {v} {report.unit}/s
                      </title>
                    </circle>
                  ),
              )}
            </g>
          );
        })}
        {Array.from({ length: Math.min(count, 8) }, (_, i) =>
          Math.round((i * (count - 1)) / Math.max(1, Math.min(count, 8) - 1)),
        ).map((i) => (
          <text key={i} x={x(i)} y="236" textAnchor="middle">
            Run {i + 1}
          </text>
        ))}
      </svg>
      <div className="chart-legend">
        {series.map((s) => (
          <span key={s.name}>
            <i style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
      </div>
    </section>
  );
}
