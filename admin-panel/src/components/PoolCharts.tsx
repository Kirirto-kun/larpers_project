import type { Node, Summary } from "../lib/types";

export function UsageBar({ label, value }: { label: string; value?: number }) {
  const measured =
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 100;
  return (
    <div className="usage-row">
      <span>{label}</span>
      <div
        className={"visual-track" + (measured ? "" : " unmeasured")}
        role={measured ? "meter" : undefined}
        aria-label={label}
        aria-valuemin={measured ? 0 : undefined}
        aria-valuemax={measured ? 100 : undefined}
        aria-valuenow={measured ? value : undefined}
      >
        {measured && <span style={{ width: value + "%" }} />}
      </div>
      <small>{measured ? value + "%" : "Not measured"}</small>
    </div>
  );
}

export function PoolCharts({
  summary,
  nodes,
}: {
  summary?: Summary;
  nodes?: Node[];
}) {
  const tasks = [
    { label: "Queued", value: summary?.queued_tasks, color: "#aebbb1" },
    { label: "Running", value: summary?.running_tasks, color: "#12996a" },
    { label: "Recovering", value: summary?.recovering_tasks, color: "#be9960" },
  ];
  const states = nodes
    ? [...new Set(nodes.map((n) => n.state))].map((state) => ({
        label: state.replaceAll("_", " "),
        value: nodes.filter((n) => n.state === state).length,
        color:
          state === "ready"
            ? "#12996a"
            : state === "offline"
              ? "#b88d83"
              : "#aebbb1",
      }))
    : [];
  return (
    <section
      className="overview-section snapshot-charts"
      aria-label="Pool snapshot"
    >
      <div>
        <h2>Workload</h2>
        <small className="chart-caption">Current tasks</small>
        <svg
          className="workload-chart"
          viewBox="0 0 420 210"
          role="img"
          aria-label={tasks
            .map((t) => t.label + ": " + (t.value ?? "not measured"))
            .join(", ")}
        >
          {[0, 1, 2, 3].map((i) => (
            <g key={i}>
              <line
                x1="30"
                x2="405"
                y1={165 - i * 45}
                y2={165 - i * 45}
                stroke="#e7ede7"
                strokeDasharray={i ? "3 5" : undefined}
              />
              <text x="20" y={169 - i * 45} textAnchor="end">
                {Math.ceil(Math.max(1, ...tasks.map((t) => t.value ?? 0)) / 3) *
                  i}
              </text>
            </g>
          ))}
          {tasks.map((t, i) => {
            const height =
              ((t.value ?? 0) /
                (Math.ceil(Math.max(1, ...tasks.map((t) => t.value ?? 0)) / 3) *
                  3)) *
              135;
            return (
              <g key={t.label}>
                <rect
                  x={68 + i * 121}
                  y={165 - height}
                  width="48"
                  height={height}
                  rx="3"
                  fill={t.color}
                >
                  <title>
                    {t.label}: {t.value ?? "not measured"}
                  </title>
                </rect>
                <text
                  className="chart-value"
                  x={92 + i * 121}
                  y={155 - height}
                  textAnchor="middle"
                >
                  {t.value ?? "—"}
                </text>
                <text x={92 + i * 121} y="190" textAnchor="middle">
                  {t.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <div>
        <h2>Device availability</h2>
        <small className="chart-caption">
          {nodes ? nodes.length + " registered" : "Loading…"}
        </small>
        {nodes && nodes.length > 0 ? (
          <>
            <svg
              className="availability-donut"
              viewBox="0 0 220 185"
              role="img"
              aria-label={states
                .map((s) => s.label + ": " + s.value)
                .join(", ")}
            >
              <circle
                cx="110"
                cy="88"
                r="60"
                fill="none"
                stroke="#edf1ec"
                strokeWidth="15"
              />
              {states.map((s, i) => {
                const circumference = 2 * Math.PI * 60;
                const offset =
                  (states
                    .slice(0, i)
                    .reduce((sum, item) => sum + item.value, 0) /
                    nodes.length) *
                  circumference;
                return (
                  <circle
                    key={s.label}
                    cx="110"
                    cy="88"
                    r="60"
                    fill="none"
                    stroke={s.color}
                    strokeWidth="15"
                    strokeDasharray={`${(s.value / nodes.length) * circumference} ${circumference}`}
                    strokeDashoffset={-offset}
                    transform="rotate(-90 110 88)"
                  >
                    <title>
                      {s.label}: {s.value}
                    </title>
                  </circle>
                );
              })}
              <text x="110" y="89" textAnchor="middle" className="donut-value">
                {Math.round(
                  (nodes.filter((n) => n.state === "ready").length /
                    nodes.length) *
                    100,
                )}
                %
              </text>
              <text x="110" y="111" textAnchor="middle">
                ready
              </text>
            </svg>
            <div className="chart-legend">
              {states.map((s) => (
                <span key={s.label}>
                  <i style={{ background: s.color }} />
                  {s.label}
                  <b>{s.value}</b>
                </span>
              ))}
            </div>
          </>
        ) : (
          <p className="muted">{nodes ? "No devices" : "Loading…"}</p>
        )}
      </div>
    </section>
  );
}
