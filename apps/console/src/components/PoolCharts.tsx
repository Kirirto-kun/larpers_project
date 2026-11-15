import { Link } from "react-router-dom";
import type { Node, Summary } from "../lib/types";
import { Empty, stateNames } from "./Common";

const stateColors: Record<string, string> = {
  ready: "var(--primary, #12996a)",
  offline: "#ad6f60",
  quarantined: "#a16643",
  paused: "#9aa99d",
};

// Adapted from the colleague's pool snapshot, using the current server DTOs.
export function PoolCharts({
  summary,
  nodes,
  summaryUnavailable,
  nodesUnavailable,
}: {
  summary?: Summary;
  nodes?: Node[];
  summaryUnavailable: boolean;
  nodesUnavailable: boolean;
}) {
  const jobs = Object.entries(summary?.jobs_by_state ?? {});
  const states = nodes
    ? [...new Set(nodes.map((node) => node.state))].map((state) => ({
        state,
        label: stateNames[state] || state.replaceAll("_", " "),
        count: nodes.filter((node) => node.state === state).length,
        color: stateColors[state] || "#718879",
      }))
    : [];
  const totalJobs = jobs.reduce((sum, [, count]) => sum + count, 0);
  const circumference = 2 * Math.PI * 60;

  return (
    <section className="grid snapshot-charts" aria-label="Pool snapshot">
      <div className="card">
        <div className="section-heading">
          <h2>Workload</h2>
          <Link to="/jobs">View tasks →</Link>
        </div>
        <p className="chart-caption">
          {summary ? `${totalJobs} tasks` : "Current task status"}
        </p>
        {summary ? (
          totalJobs > 0 ? (
            jobs.map(([state, count]) => (
              <div className="chart-row" key={state}>
                <span>{stateNames[state] || state.replaceAll("_", " ")}</span>
                <meter
                  aria-label={stateNames[state] || state}
                  min={0}
                  max={Math.max(1, ...jobs.map(([, value]) => value))}
                  value={count}
                />
                <span>{count}</span>
              </div>
            ))
          ) : (
            <Empty>No tasks yet.</Empty>
          )
        ) : (
          <Empty>
            {summaryUnavailable ? "Workload unavailable." : "Loading workload…"}
          </Empty>
        )}
      </div>
      <div className="card">
        <h2>Device availability</h2>
        <p className="chart-caption">
          {nodes ? `${nodes.length} registered` : "Current device status"}
        </p>
        {nodes?.length ? (
          <>
            <svg
              className="availability-donut"
              viewBox="0 0 220 185"
              role="img"
              aria-label={states
                .map((state) => `${state.label}: ${state.count}`)
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
              {states.map((state, index) => (
                <circle
                  key={state.state}
                  cx="110"
                  cy="88"
                  r="60"
                  fill="none"
                  stroke={state.color}
                  strokeWidth="15"
                  strokeDasharray={`${(state.count / nodes.length) * circumference} ${circumference}`}
                  strokeDashoffset={
                    -(
                      states
                        .slice(0, index)
                        .reduce((sum, item) => sum + item.count, 0) /
                      nodes.length
                    ) * circumference
                  }
                  transform="rotate(-90 110 88)"
                >
                  <title>
                    {state.label}: {state.count}
                  </title>
                </circle>
              ))}
              <text x="110" y="89" textAnchor="middle" className="donut-value">
                {Math.round(
                  (nodes.filter((node) => node.state === "ready").length /
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
              {states.map((state) => (
                <span key={state.state}>
                  <i style={{ background: state.color }} aria-hidden="true" />
                  {state.label}
                  <b>{state.count}</b>
                </span>
              ))}
            </div>
          </>
        ) : (
          <Empty>
            {nodes
              ? "No devices registered."
              : nodesUnavailable
                ? "Device availability unavailable."
                : "Loading devices…"}
          </Empty>
        )}
      </div>
    </section>
  );
}
