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
        <div className="snapshot-bars">
          {tasks.map((t) => (
            <div className="snapshot-bar-row" key={t.label}>
              <span>{t.label}</span>
              <div className="visual-track">
                <span
                  style={{
                    width:
                      t.value === undefined
                        ? 0
                        : (t.value /
                            Math.max(1, ...tasks.map((x) => x.value ?? 0))) *
                            100 +
                          "%",
                    background: t.color,
                  }}
                />
              </div>
              <strong>{t.value ?? "—"}</strong>
            </div>
          ))}
        </div>
      </div>
      <div>
        <h2>Device availability</h2>
        <small className="chart-caption">
          {nodes ? nodes.length + " registered" : "Loading…"}
        </small>
        {nodes && nodes.length > 0 ? (
          <>
            <div
              className="availability-bar"
              role="img"
              aria-label={states
                .map((s) => s.label + ": " + s.value)
                .join(", ")}
            >
              {states.map((s) => (
                <span
                  key={s.label}
                  style={{ flex: s.value, background: s.color }}
                  title={s.label + ": " + s.value}
                />
              ))}
            </div>
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
