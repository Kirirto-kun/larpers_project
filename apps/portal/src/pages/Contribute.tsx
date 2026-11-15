import { useState } from "react";
import { Monitor, Download, Copy } from "lucide-react";
import { useResource } from "../lib/hooks";
import { api, post, errorText } from "../lib/api";
import type {
  Node,
  Mode,
  DesiredState,
  Release,
  Invitation,
} from "../lib/types";
import { Button } from "../components/ui/button";
import {
  PageHeader,
  ErrorBox,
  Status,
  bytes,
  Empty,
  date,
} from "../components/Common";
const modes: { id: Mode; name: string; percent: number; detail: string }[] = [
  {
    id: "low",
    name: "Light",
    percent: 10,
    detail: "While you use your computer",
  },
  {
    id: "medium",
    name: "Balanced",
    percent: 50,
    detail: "Balanced performance",
  },
  {
    id: "max",
    name: "Maximum",
    percent: 90,
    detail: "When idle and plugged in",
  },
];
export function DeviceCard({
  node,
  refresh,
  admin = false,
}: {
  node: Node;
  refresh: () => Promise<void>;
  admin?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const cap = node.capabilities;
  const pending = node.applied_revision !== node.settings.revision;
  async function change(body: { mode?: Mode; desired_state?: DesiredState }) {
    setBusy(true);
    try {
      await api("/nodes/" + node.id + "/settings", {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      await refresh();
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function legacy(action: string) {
    setBusy(true);
    try {
      await post("/nodes/" + node.id + "/control", { action });
      await refresh();
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="card device-card">
      <div className="row">
        <div className="device-title">
          <Monitor aria-hidden="true" />
          <h3>{node.name}</h3>
        </div>
        <Status value={node.state} />
      </div>
      <p className="muted">
        {cap
          ? `${cap.os === "darwin" ? "macOS" : cap.os} · ${cap.cpu_logical} CPU · ${bytes(cap.memory_available_bytes)} available`
          : "Device details appear after connection"}
      </p>
      {cap && (
        <p className="muted">
          Shared: {(cap.offered_budget.cpu_millis / 1000).toFixed(1)} CPU ·{" "}
          {bytes(cap.offered_budget.memory_bytes)} memory · GPU:{" "}
          {cap.offered_budget.gpu_slots > 0 ? "enabled locally" : "not shared"}
        </p>
      )}
      <div className="device-meta">
        <span>{node.active_attempts} active tasks</span>
        <span>{node.accepted_units} accepted</span>
      </div>
      <p className="muted">Last seen: {date(node.last_seen_at)}</p>
      {node.supports_web_controls ? (
        <>
          <p
            className={"notice " + (node.settings_error ? "error" : "")}
            role="status"
          >
            {node.settings_error
              ? "Could not apply settings: " + node.settings_error
              : pending
                ? "Waiting for device to apply settings"
                : "Settings applied"}
            {pending && node.state === "offline"
              ? " — open the app on this device."
              : ""}
          </p>
          <fieldset disabled={busy}>
            <legend>CPU and memory usage</legend>
            <div className="mode-options">
              {modes.map((mode) => (
                <label
                  className={
                    "mode " + (node.settings.mode === mode.id ? "selected" : "")
                  }
                  key={mode.id}
                >
                  <input
                    type="radio"
                    name={"mode-" + node.id}
                    checked={node.settings.mode === mode.id}
                    onChange={() => void change({ mode: mode.id })}
                  />
                  <strong>{mode.percent}%</strong>
                  <span>{mode.name}</span>
                  <small>{mode.detail}</small>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="actions">
            <Button
              disabled={busy || node.state === "quarantined"}
              onClick={() => void change({ desired_state: "running" })}
            >
              Connect / resume
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void change({ desired_state: "paused" })}
            >
              Pause
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void change({ desired_state: "draining" })}
            >
              Finish current work
            </Button>
          </div>
          <p className="muted">
            The app applies changes when it next connects. Keep it open on your
            device.
          </p>
        </>
      ) : (
        <>
          <div className="notice">
            Update the app to manage usage here. Your current connection will
            keep working.
          </div>
          <div className="actions">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void legacy("drain")}
            >
              Finish current work
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void legacy("stop")}
            >
              Stop
            </Button>
          </div>
        </>
      )}
      {admin && (
        <div className="actions">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              void legacy(
                node.state === "quarantined" ? "release" : "quarantine",
              )
            }
          >
            {node.state === "quarantined" ? "Unblock" : "Block"}
          </Button>
        </div>
      )}
      <ErrorBox message={error} />
    </article>
  );
}
export function Contribute() {
  const { data: nodes, error, refresh } = useResource<Node[]>("/nodes");
  const {
    data: release,
    error: releaseError,
    refresh: refreshRelease,
  } = useResource<Release>("/client-release");
  const [name, setName] = useState("My computer");
  const [invitation, setInvitation] = useState<Invitation>();
  const [busy, setBusy] = useState(false);
  const [actionError, setError] = useState("");
  const [copy, setCopy] = useState("");
  const [macCopy, setMacCopy] = useState("");
  return (
    <>
      <PageHeader eyebrow="DEVICES" title="Share your spare capacity">
        Connect a computer, choose its usage limit, and pause whenever you need.
      </PageHeader>
      <div className="grid">
        <section className="card">
          <p className="eyebrow">GET CONNECTED</p>
          <h2>Add a computer</h2>
          <div className="step">
            <b>1</b>
            <div>
              <h3>Download the app</h3>
              <p className="muted">Choose the download for your computer.</p>
              <div className="actions">
                {release?.downloads.map((d) => (
                  <a
                    className="button outline"
                    href={d.url}
                    key={d.os}
                    download
                  >
                    <Download aria-hidden="true" size={16} />
                    {d.label}
                  </a>
                ))}
              </div>
              {releaseError && (
                <>
                  <ErrorBox message={releaseError} />
                  <Button
                    variant="outline"
                    onClick={() => void refreshRelease()}
                  >
                    Retry downloads
                  </Button>
                </>
              )}
            </div>
          </div>
          <div className="step">
            <b>2</b>
            <div>
              <h3>Open the download</h3>
              <p>
                Follow the setup steps. On Windows or macOS, install and open
                Docker Desktop first. Linux needs a running Docker Engine.
              </p>
            </div>
          </div>
          <details>
            <summary>macOS: download will not open?</summary>
            <p>Open Terminal and run:</p>
            <pre>sh ~/Downloads/Connect.command</pre>
            <Button
              variant="outline"
              onClick={async () => {
                try {
                  if (!navigator.clipboard) throw new Error();
                  await navigator.clipboard.writeText(
                    "sh ~/Downloads/Connect.command",
                  );
                  setMacCopy("Copied");
                } catch {
                  setMacCopy("Select the command and copy it manually.");
                }
              }}
            >
              Copy macOS command
            </Button>
            <p role="status">{macCopy}</p>
          </details>
          <div className="step">
            <b>3</b>
            <div>
              <h3>Paste your private invitation</h3>
              <p>Create an invitation below and paste it into the app.</p>
            </div>
          </div>
        </section>
        <section className="card">
          <h2>Create a device invitation</h2>
          <p className="muted">
            This single-use invitation expires in 30 minutes. Share it only with
            your own device.
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setCopy("");
              try {
                setInvitation(
                  await post<Invitation>("/invitations", {
                    kind: "device",
                    name: name.trim(),
                    ttl_seconds: 1800,
                  }),
                );
                setError("");
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <label>
              Computer name
              <input
                value={name}
                maxLength={256}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </label>
            <Button disabled={busy || !name.trim()}>
              {busy ? "Creating…" : "Create invitation"}
            </Button>
          </form>
          {invitation && (
            <div className="invite-result">
              <label>
                Private invitation
                <input
                  readOnly
                  value={invitation.token}
                  onFocus={(e) => e.target.select()}
                />
              </label>
              <Button
                variant="outline"
                onClick={async () => {
                  try {
                    if (!navigator.clipboard) throw new Error();
                    await navigator.clipboard.writeText(invitation.token);
                    setCopy("Copied");
                  } catch {
                    setCopy(
                      "Select the invitation above and copy it manually.",
                    );
                  }
                }}
              >
                <Copy aria-hidden="true" size={16} />
                Copy
              </Button>
              <p role="status">{copy}</p>
              <small>Expires {date(invitation.expires_at)}</small>
            </div>
          )}
          <ErrorBox message={actionError} />
          <div className="notice">
            Enable GPU sharing in the app. Usage limits do not change GPU
            permission or allow battery use.
          </div>
          <p className="muted">
            10 / 50 / 90% applies to the CPU and memory available within your
            Docker and device settings.
          </p>
        </section>
      </div>
      <section>
        <div className="row">
          <h2>Your devices</h2>
          <Button variant="ghost" onClick={() => void refresh()}>
            Refresh
          </Button>
        </div>
        <ErrorBox message={error} />
        {!nodes && !error && <p role="status">Loading devices…</p>}
        {nodes?.length === 0 && (
          <Empty>
            <Monitor aria-hidden="true" />
            <h3>No devices connected yet</h3>
            <p>Follow the steps above to connect your first computer.</p>
          </Empty>
        )}
        {nodes?.map((node) => (
          <DeviceCard key={node.id} node={node} refresh={refresh} />
        ))}
      </section>
    </>
  );
}
