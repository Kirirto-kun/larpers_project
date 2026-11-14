import { Monitor } from "lucide-react";
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
    <section className="card">
      <h2>Devices</h2>

      <div className="topology">
        {nodes.length === 0 && <Empty>No devices registered.</Empty>}
        {nodes.map((n) => (
          <div className="node-tile" key={n.id}>
            <Monitor size={28} />
            <h3>{n.name}</h3>
            <Status value={n.state} />
            <p>{n.roles.join(" · ")}</p>
            <small>
              {n.cpu_logical} CPU threads · {n.gpus.length} GPUs
            </small>
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
