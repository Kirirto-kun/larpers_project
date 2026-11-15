import { Link } from "react-router-dom";
import { Monitor } from "lucide-react";
import { useResource } from "../lib/hooks";
import type { Summary, Node } from "../lib/types";
import {
  PageHeader,
  Metric,
  ErrorBox,
  Status,
  Empty,
  bytes,
  date,
} from "../components/Common";
import { PoolCharts } from "../components/PoolCharts";

export function Overview() {
  const { data: summary, error } = useResource<Summary>("/pool/summary");
  const { data: nodes, error: nodeError } = useResource<Node[]>("/nodes");
  const readyNodes = nodes?.filter((node) => node.state === "ready");
  const capacityKnown = readyNodes?.every((node) => node.capabilities !== null);
  const offered = capacityKnown
    ? readyNodes!.reduce(
        (total, node) => ({
          cpu: total.cpu + node.capabilities!.offered_budget.cpu_millis,
          memory: total.memory + node.capabilities!.offered_budget.memory_bytes,
          gpu: total.gpu + node.capabilities!.offered_budget.gpu_slots,
        }),
        { cpu: 0, memory: 0, gpu: 0 },
      )
    : undefined;

  return (
    <div className="overview-page">
      <section className="overview-intro">
        <PageHeader title="Overview" />
        <span className="connection-note">Updates every 5 seconds</span>
      </section>
      <ErrorBox message={error || nodeError} />
      <div className="metrics">
        <Metric
          label="Ready devices"
          value={summary?.ready_nodes ?? "—"}
          hint={
            summary
              ? `${summary.nodes} registered`
              : error
                ? "Unavailable"
                : "Loading…"
          }
        />
        <Metric label="Accepted units" value={summary?.accepted_units ?? "—"} />
        <Metric
          label="Running tasks"
          value={summary ? (summary.jobs_by_state.running ?? 0) : "—"}
        />
        <Metric
          label="Failed tasks"
          value={summary ? (summary.jobs_by_state.failed ?? 0) : "—"}
        />
      </div>
      <PoolCharts
        summary={summary}
        nodes={nodes}
        summaryUnavailable={!!error}
        nodesUnavailable={!!nodeError}
      />
      <section className="card overview-section">
        <div className="section-heading">
          <h2>Offered capacity</h2>
          <span className="muted">Ready devices only</span>
        </div>
        <dl>
          <dt>CPU cores</dt>
          <dd>{offered ? offered.cpu / 1000 : "—"}</dd>
          <dt>Memory</dt>
          <dd>{offered ? bytes(offered.memory) : "—"}</dd>
          <dt>GPU slots</dt>
          <dd>{offered ? offered.gpu : "—"}</dd>
        </dl>
        {readyNodes && !capacityKnown && (
          <p className="muted">
            Some device capabilities have not been reported.
          </p>
        )}
      </section>
      <section className="card overview-section">
        <div className="section-heading">
          <h2>Devices{nodes ? ` · ${nodes.length}` : ""}</h2>
          <Link to="/nodes">View devices →</Link>
        </div>
        {nodes?.length === 0 && <Empty>No devices registered.</Empty>}
        {!nodes && (
          <Empty>
            {nodeError ? "Devices unavailable." : "Loading devices…"}
          </Empty>
        )}
        <div className="topology">
          {nodes?.map((node) => (
            <article className="node-tile" key={node.id}>
              <Monitor size={20} aria-hidden="true" />
              <h3>{node.name}</h3>
              <Status value={node.state} />
              <p>
                {node.capabilities
                  ? `${node.capabilities.cpu_logical} CPU threads · ${node.capabilities.gpus.length} ${node.capabilities.gpus.length === 1 ? "GPU" : "GPUs"}`
                  : "Hardware details pending"}
              </p>
              <p>
                {node.active_attempts} active tasks · {node.accepted_units}{" "}
                accepted units
              </p>
              <small>Last seen: {date(node.last_seen_at)}</small>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
