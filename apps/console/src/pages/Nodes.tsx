import { useState } from "react";
import { useResource } from "../lib/hooks";
import type { Node } from "../lib/types";
import { post, api, errorText } from "../lib/api";
import {
  PageHeader,
  Status,
  ErrorBox,
  Empty,
  bytes,
  date,
} from "../components/Common";
import { Button } from "../components/ui/button";
export function Nodes() {
  const { data, error, refresh } = useResource<Node[]>("/nodes");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  async function control(n: Node, action: string) {
    setBusy(n.id);
    try {
      await post("/nodes/" + n.id + "/control", { action });
      await refresh();
      setMessage("");
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy("");
    }
  }
  return (
    <>
      <PageHeader title="Devices" />
      <ErrorBox message={error || message} />
      {!data && !error && <Empty>Loading devices…</Empty>}
      {data?.length === 0 && <Empty>No devices registered.</Empty>}
      {data?.map((n) => (
        <article className="card" key={n.id}>
          <div className="row">
            <h2>{n.name}</h2>
            <Status value={n.state} />
          </div>
          <div className="grid">
            <div>
              <h3>Hardware</h3>
              {n.capabilities ? (
                <>
                  <p>
                    {n.capabilities.os} / {n.capabilities.arch} ·{" "}
                    {n.capabilities.cpu_logical} CPU ·{" "}
                    {bytes(n.capabilities.memory_available_bytes)} memory
                  </p>
                  <p>
                    Docker {n.capabilities.runtime.version} ·{" "}
                    {n.capabilities.runtime.healthy
                      ? "healthy"
                      : "needs attention"}
                  </p>
                  <p>
                    Power:{" "}
                    {n.capabilities.power_state === "ac"
                      ? "AC power"
                      : n.capabilities.power_state === "battery"
                        ? "battery"
                        : "unknown"}
                  </p>
                  {n.capabilities.gpus.map((g) => (
                    <p key={g.id}>
                      <b>{g.vendor}</b> · {bytes(g.vram_bytes)} · CUDA{" "}
                      {g.cuda_verified ? "verified" : "not verified"}
                    </p>
                  ))}
                  <p>
                    Offered: {n.capabilities.offered_budget.cpu_millis / 1000}{" "}
                    CPU · {bytes(n.capabilities.offered_budget.memory_bytes)} ·{" "}
                    {n.capabilities.offered_budget.gpu_slots} GPU
                  </p>
                </>
              ) : (
                <p>Hardware details not received yet.</p>
              )}
              <p className="muted">
                CPU and GPU utilization not measured here.
              </p>
            </div>
            <div>
              <h3>Activity & controls</h3>
              <p>
                {n.active_attempts} active tasks · {n.accepted_units} accepted
                units
              </p>
              <p>Last seen: {date(n.last_seen_at)}</p>
              <p>
                Owner: <code>{n.owner_id}</code>
              </p>
              {n.supports_web_controls ? (
                <>
                  <p className="notice">
                    {n.applied_revision === n.settings.revision
                      ? "Settings applied"
                      : "Waiting for device to apply settings"}
                  </p>
                  <label>
                    Workload mode
                    <select
                      value={n.settings.mode}
                      disabled={!!busy}
                      onChange={async (e) => {
                        setBusy(n.id);
                        try {
                          await api("/nodes/" + n.id + "/settings", {
                            method: "PATCH",
                            body: JSON.stringify({ mode: e.target.value }),
                          });
                          await refresh();
                          setMessage("");
                        } catch (e) {
                          setMessage(errorText(e));
                        } finally {
                          setBusy("");
                        }
                      }}
                    >
                      <option value="low">Low · 10%</option>
                      <option value="medium">Medium · 50%</option>
                      <option value="max">Maximum · 90%</option>
                    </select>
                  </label>
                </>
              ) : (
                <p className="notice">
                  Update the desktop app to change settings remotely.
                </p>
              )}
              {n.settings_error && <ErrorBox message={n.settings_error} />}
            </div>
          </div>
          <div className="actions">
            {[
              { id: "drain", name: "Finish current work" },
              { id: "stop", name: "Stop" },
              { id: "resume", name: "Resume" },
              {
                id: n.state === "quarantined" ? "release" : "quarantine",
                name: n.state === "quarantined" ? "Release" : "Quarantine",
              },
            ].map((a) => (
              <Button
                key={a.id}
                variant="outline"
                disabled={!!busy}
                onClick={() => void control(n, a.id)}
              >
                {a.name}
              </Button>
            ))}
          </div>
        </article>
      ))}
    </>
  );
}
