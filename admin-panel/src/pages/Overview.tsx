import { useResource } from "../lib/hooks";
import type { Summary, Node } from "../lib/types";
import { PageHeader, Metric, ErrorBox } from "../components/Common";
import { Topology } from "../components/Topology";
import { CostBreakdown } from "../components/CostBreakdown";
export function Overview() {
  const {
    data: summary,
    error,
    connected,
  } = useResource<Summary>("/pool/summary", "/pool/events");
  const { data: nodes, error: nodeError } = useResource<Node[]>(
    "/nodes",
    "/pool/events",
  );
  return (
    <>
      <section className="overview-intro">
        <PageHeader
          eyebrow="POOL OVERVIEW"
          title="Shared compute. Visible progress."
        >
          Keep an eye on the devices, useful work, and recovery across your
          private pool.
        </PageHeader>
      </section>
      <ErrorBox message={error || nodeError} />
      {!connected && (
        <div className="notice">
          Live updates disconnected. Reconnecting and refreshing the saved
          snapshot.
        </div>
      )}
      <div className="metrics">
        <Metric
          label="Ready devices"
          value={nodes ? nodes.filter((n) => n.state === "ready").length : "—"}
          hint={nodes ? nodes.length + " registered" : "Not measured"}
        />
        <Metric
          label="Accepted units"
          value={summary?.accepted_units ?? "—"}
          hint="Unique, server-accepted work"
        />
        <Metric label="Queued tasks" value={summary?.queued_tasks ?? "—"} />
        <Metric
          label="Recovering tasks"
          value={summary?.recovering_tasks ?? "—"}
        />
      </div>
      {nodes && <Topology nodes={nodes} events={summary?.events || []} />}
      <CostBreakdown />
    </>
  );
}
