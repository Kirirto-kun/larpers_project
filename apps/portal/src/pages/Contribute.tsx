import { useState } from "react";
import { useResource } from "../lib/hooks";
import { post, errorText } from "../lib/api";
import type { Node } from "../lib/types";
import { Button } from "../components/ui/button";
import {
  PageHeader,
  ErrorBox,
  Status,
  bytes,
  Empty,
} from "../components/Common";
export function Contribute() {
  const {
    data: nodes,
    error,
    refresh,
  } = useResource<Node[]>("/nodes", "/pool/events");
  const [os, setOs] = useState("macos");
  const [budget, setBudget] = useState(50);
  const [memoryGiB, setMemoryGiB] = useState(2);
  const [gpu, setGpu] = useState(false);
  const [consent, setConsent] = useState(false);
  const [command, setCommand] = useState("");
  const [actionError, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function invite() {
    setBusy(true);
    try {
      const r = await post<{ command: string }>("/invitations", {
        os,
        offered_budget: {
          cpu_percent: budget,
          memory_bytes: memoryGiB * 1073741824,
          gpu_allowed: gpu,
        },
        consent,
      });
      setCommand(r.command);
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function control(id: string, action: string) {
    setBusy(true);
    try {
      await post("/nodes/" + id + "/control", { action });
      await refresh();
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader
        eyebrow="CONTRIBUTE"
        title="Give your spare compute a purpose."
      >
        You choose the budget. You can drain or stop your device at any time.
      </PageHeader>
      <div className="grid">
        <section className="card">
          <h2>Connect a laptop</h2>
          <label>
            Operating system
            <select
              disabled={busy}
              value={os}
              onChange={(e) => {
                setOs(e.target.value);
                setCommand("");
              }}
            >
              <option value="macos">macOS</option>
              <option value="linux">Linux</option>
              <option value="windows">Windows / WSL2</option>
            </select>
          </label>
          <label>
            CPU budget · {budget}%
            <input
              disabled={busy}
              type="range"
              min="10"
              max="100"
              step="10"
              value={budget}
              onChange={(e) => {
                setBudget(Number(e.target.value));
                setCommand("");
              }}
            />
          </label>
          <label>
            Memory budget (GiB)
            <input
              disabled={busy}
              type="number"
              min="1"
              step="1"
              value={memoryGiB}
              onChange={(e) => {
                setMemoryGiB(+e.target.value);
                setCommand("");
              }}
            />
          </label>
          <label className="check">
            <input
              disabled={busy}
              type="checkbox"
              checked={gpu}
              onChange={(e) => {
                setGpu(e.target.checked);
                setCommand("");
              }}
            />
            Allow verified NVIDIA GPU use
          </label>
          <small>
            GPU availability is checked on your device. A strict GPU/VRAM
            percentage is not promised.
          </small>
          <label className="check">
            <input
              disabled={busy}
              type="checkbox"
              checked={consent}
              onChange={(e) => {
                setConsent(e.target.checked);
                setCommand("");
              }}
            />
            I own or have permission to contribute this device.
          </label>
          <Button
            disabled={
              !consent ||
              busy ||
              !Number.isSafeInteger(memoryGiB) ||
              memoryGiB < 1
            }
            onClick={invite}
          >
            Generate join command
          </Button>
          {command && (
            <>
              <pre>{command}</pre>
              <small>
                Run this command on the device you want to contribute.
                Invitation expires as specified by the server. Keep it private.
              </small>
            </>
          )}
          <ErrorBox message={actionError} />
        </section>
        <section className="card">
          <h2>Before you connect</h2>
          <div className="step">
            <b>1</b>
            <p>
              Install Docker and start its runtime. On Windows, enable WSL2.
            </p>
          </div>
          <div className="step">
            <b>2</b>
            <p>
              Use your team's reachable coordinator address. Campus Wi-Fi may
              block device-to-device connections.
            </p>
          </div>
          <div className="step">
            <b>3</b>
            <p>
              The client checks CPU, memory, disk, runtime, and CUDA. Review the
              results below.
            </p>
          </div>
          <div className="notice">
            Contribute only to a trusted pool using non-sensitive data. The
            device administrator can access workload data.
          </div>
          <p className="muted">
            Drain finishes current work without taking new tasks. Stop requests
            interruption of current work.
          </p>
        </section>
      </div>
      <section className="card">
        <h2>Devices and preflight</h2>
        <ErrorBox message={error} />
        {nodes?.length === 0 && (
          <Empty>
            No devices connected yet. Generate a command to get started.
          </Empty>
        )}
        {nodes?.map((node) => (
          <article className="card" key={node.id}>
            <div className="row">
              <h3>{node.name}</h3>
              <Status value={node.state} />
            </div>
            <p>
              {node.cpu_logical} logical CPUs ·{" "}
              {bytes(node.memory_available_bytes)} available ·{" "}
              {node.gpus.length} GPU(s)
            </p>
            <p className="muted">
              Offered CPU: {node.offered_budget.cpu_percent}% · GPU:{" "}
              {node.offered_budget.gpu_allowed ? "opted in" : "not offered"}
            </p>
            {node.preflight.map((check) => (
              <p key={check.name}>
                {check.ok ? "✓" : "!"} {check.name}: {check.message}
              </p>
            ))}
            <div className="actions">
              {["drain", "stop", "resume"].map((action) => (
                <Button
                  key={action}
                  disabled={busy}
                  variant="outline"
                  onClick={() => void control(node.id, action)}
                >
                  {action[0].toUpperCase() + action.slice(1)}
                </Button>
              ))}
            </div>
          </article>
        ))}
      </section>
    </>
  );
}
