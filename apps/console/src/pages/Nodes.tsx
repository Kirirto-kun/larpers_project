import { useState } from "react";
import { useResource } from "../lib/hooks";
import type { Node } from "../lib/types";
import { post, errorText } from "../lib/api";
import {
  PageHeader,
  Status,
  ErrorBox,
  Empty,
  bytes,
} from "../components/Common";
import { AttemptTimeline } from "../components/AttemptTimeline";
import { Button } from "../components/ui/button";
export function Nodes({ admin }: { admin: boolean }) {
  const { data, error, refresh } = useResource<Node[]>(
    "/nodes",
    "/pool/events",
  );
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const node = data?.find((n) => n.id === selected);
  return (
    <>
      <PageHeader eyebrow="DEVICES" title="Every laptop has a role.">
        CPU and GPU capacity is offered explicitly by each participant.
      </PageHeader>
      <ErrorBox message={error || message} />
      <section className="card table-wrap">
        <table>
          <thead>
            <tr>
              <th>Device</th>
              <th>Status</th>
              <th>Resources</th>
              <th>Last seen (UTC)</th>
              <th>Activity</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((n) => (
              <tr key={n.id}>
                <td>
                  <button
                    className="button ghost"
                    onClick={() => setSelected(n.id)}
                  >
                    {n.name}
                  </button>
                  <small>{n.roles.join(", ")}</small>
                </td>
                <td>
                  <Status value={n.state} />
                </td>
                <td>
                  {n.cpu_logical} CPU · {bytes(n.memory_available_bytes)}
                  <br />
                  {n.gpus.length} GPU(s)
                </td>
                <td>{n.last_seen_at}</td>
                <td>{n.current_job_id || "Idle"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data?.length === 0 && <Empty>No devices registered.</Empty>}
      </section>
      {node && (
        <section className="card">
          <h2>{node.name}</h2>
          <dl>
            <dt>CPU utilization</dt>
            <dd>
              {node.cpu_utilization === undefined
                ? "Not measured"
                : node.cpu_utilization + "%"}
            </dd>
            <dt>GPU utilization</dt>
            <dd>
              {node.gpu_utilization === undefined
                ? "Not measured"
                : node.gpu_utilization + "%"}
            </dd>
            <dt>Offered CPU</dt>
            <dd>{node.offered_budget.cpu_percent}%</dd>
            <dt>GPU permission</dt>
            <dd>
              {node.offered_budget.gpu_allowed ? "Opted in" : "Not offered"}
            </dd>
          </dl>
          {node.gpus.map((gpu) => (
            <div className="notice" key={gpu.id}>
              {gpu.vendor} GPU · {bytes(gpu.vram_bytes)} VRAM · CUDA{" "}
              {gpu.cuda_verified ? "verified" : "not verified"}
            </div>
          ))}
          {node.preflight.map((c) => (
            <p key={c.name}>
              {c.ok ? "✓" : "!"} {c.name}: {c.message}
            </p>
          ))}
          {admin && (
            <div className="actions">
              {["drain", "stop", "resume", "quarantine", "release"].map(
                (action) => (
                  <Button
                    variant="outline"
                    disabled={busy}
                    key={action}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await post("/nodes/" + node.id + "/control", {
                          action,
                        });
                        await refresh();
                        setMessage("");
                      } catch (e) {
                        setMessage(errorText(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    {action}
                  </Button>
                ),
              )}
            </div>
          )}
          <AttemptTimeline attempts={node.attempts || []} />
        </section>
      )}
    </>
  );
}
