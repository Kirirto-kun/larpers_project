import { Link } from "react-router-dom";
import { Monitor, ArrowUpRight } from "lucide-react";
import type { Node, PoolEvent } from "../lib/types";
import { Status, Empty } from "./Common";
export function Topology({
  nodes,
  events,
}: {
  nodes: Node[];
  events: PoolEvent[];
}) {
  return (
    <section className="overview-section">
      <div className="section-heading">
        <h2>
          Devices <span>{nodes.length}</span>
        </h2>
        <Link to="/nodes">
          View devices <ArrowUpRight size={13} />
        </Link>
      </div>

      <div className="topology">
        {nodes.length === 0 && <Empty>No devices registered.</Empty>}
        {nodes.map((n) => (
          <div className="node-tile" key={n.id}>
            <Monitor size={20} aria-hidden="true" />
            <div className="device-identity">
              <h3>{n.name}</h3>
              <small>{n.roles.join(" · ")}</small>
            </div>
            <small className="device-resources">
              {n.cpu_logical} CPU threads · {n.gpus.length} GPUs
            </small>
            <Status value={n.state} />
          </div>
        ))}
      </div>
      <details>
        <summary>Activity · {events.length}</summary>
        {events.map((e) => (
          <div className="row" key={e.event_id}>
            <span>
              {e.type} · {e.entity_id}
            </span>
            <small>{e.occurred_at}</small>
          </div>
        ))}
      </details>
    </section>
  );
}
