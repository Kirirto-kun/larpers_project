import type { Benchmark } from "../lib/types";
import { Empty } from "./Common";

export function TimingChart({ timings }: { timings: Record<string, number> }) {
  const stages = [
    "transfer",
    "queue",
    "start",
    "compute",
    "verify",
    "aggregate",
  ];
  const max = Math.max(
    1,
    ...stages.map((s) => (Number.isFinite(timings[s]) ? timings[s] : 0)),
  );
  return (
    <section aria-label="Timing breakdown chart">
      <h3>Timing breakdown (ms)</h3>
      {stages.map((stage) => {
        const value = timings[stage];
        return (
          <div className="chart-row" key={stage}>
            <span>{stage}</span>
            <div>
              {Number.isFinite(value) && value >= 0 && (
                <div
                  className="bar"
                  style={{ width: (value / max) * 100 + "%" }}
                />
              )}
            </div>
            <span>
              {Number.isFinite(value) && value >= 0
                ? value.toLocaleString() + " ms"
                : "Not measured"}
            </span>
          </div>
        );
      })}
      <small>Stages may overlap.</small>
    </section>
  );
}

export function UtilizationChart({
  records,
}: {
  records: Benchmark["utilization"];
}) {
  return (
    <section aria-label="Resource utilization charts">
      <h3>CPU/GPU utilization</h3>
      <p className="muted">1 s intervals · gaps excluded</p>
      {!records.length && <Empty>No utilization measurements.</Empty>}
      {records.map((record) => {
        const samples = record.samples || [];
        const maxTime = Math.max(1000, record.window_ms);
        const valid = (value: number | null) =>
          value !== null &&
          Number.isFinite(value) &&
          value >= 0 &&
          value <= 100;
        const segments = (key: "cpu_percent" | "gpu_percent") => {
          const paths: string[] = [];
          let points: string[] = [];
          let last = -1000;
          for (const sample of [...samples].sort(
            (a, b) => a.elapsed_ms - b.elapsed_ms,
          )) {
            const value = sample[key];
            if (!valid(value) || sample.elapsed_ms - last > 1500) {
              if (points.length) paths.push(points.join(" "));
              points = [];
            }
            if (
              valid(value) &&
              sample.elapsed_ms >= 0 &&
              sample.elapsed_ms <= maxTime
            )
              points.push(
                `${40 + (sample.elapsed_ms / maxTime) * 520},${120 - (value as number)}`,
              );
            last = sample.elapsed_ms;
          }
          if (points.length) paths.push(points.join(" "));
          return paths;
        };
        return (
          <article className="card" key={record.node_id}>
            <h3>{record.node_id}</h3>
            <p>
              Window {record.window_ms.toLocaleString()} ms · {record.gaps} gaps
              · {samples.length} samples
            </p>
            {samples.length > 0 ? (
              <svg
                viewBox="0 0 600 155"
                role="img"
                aria-label={`${record.node_id} CPU and GPU utilization over time`}
                style={{ width: "100%" }}
              >
                <path d="M40 20V120H560" stroke="#cfd8cb" fill="none" />
                <text x="0" y="25" fontSize="11">
                  100%
                </text>
                <text x="15" y="120" fontSize="11">
                  0%
                </text>
                <text x="40" y="145" fontSize="11">
                  0 ms
                </text>
                <text x="490" y="145" fontSize="11">
                  {maxTime} ms
                </text>
                {(["cpu_percent", "gpu_percent"] as const).flatMap(
                  (key, index) =>
                    segments(key).map((points, i) => (
                      <polyline
                        key={key + i}
                        points={points}
                        fill="none"
                        stroke={index ? "#5a83a0" : "#5b872d"}
                        strokeWidth="2"
                      />
                    )),
                )}
              </svg>
            ) : (
              <Empty>No raw samples. Averages only.</Empty>
            )}
            {(["cpu_percent", "gpu_percent"] as const).map((key) => (
              <div className="chart-row" key={key}>
                <span>{key === "cpu_percent" ? "CPU mean" : "GPU mean"}</span>
                <div>
                  {valid(record[key]) && (
                    <div
                      className="bar"
                      style={{
                        width: record[key] + "%",
                        background:
                          key === "gpu_percent" ? "#5a83a0" : undefined,
                      }}
                    />
                  )}
                </div>
                <span>
                  {valid(record[key]) ? record[key] + "%" : "Not measured"}
                </span>
              </div>
            ))}
            <details>
              <summary>Raw samples</summary>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Elapsed ms</th>
                      <th>CPU %</th>
                      <th>GPU %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {samples.map((s, i) => (
                      <tr key={i}>
                        <td>{s.elapsed_ms}</td>
                        <td>{s.cpu_percent ?? "Gap"}</td>
                        <td>{s.gpu_percent ?? "Gap"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </article>
        );
      })}
    </section>
  );
}
