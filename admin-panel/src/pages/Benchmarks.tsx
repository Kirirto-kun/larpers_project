import { ChevronRight } from "lucide-react";
import { TimingChart, UtilizationChart } from "../components/MeasurementCharts";
import { useState } from "react";
import { useResource } from "../lib/hooks";
import { post, errorText } from "../lib/api";
import type { Benchmark, Node } from "../lib/types";
import { PageHeader, ErrorBox, Empty, Status } from "../components/Common";
import { ThroughputChart } from "../components/ThroughputChart";
import { Button } from "../components/ui/button";
export function Benchmarks({ admin }: { admin: boolean }) {
  const {
    data: reports,
    error,
    refresh,
  } = useResource<Benchmark[]>("/benchmarks", "/pool/events");
  const { data: nodes } = useResource<Node[]>("/nodes");
  const [selected, setSelected] = useState("");
  const [workload, setWorkload] = useState("embeddings");
  const [digest, setDigest] = useState("");
  const [ids, setIds] = useState<string[]>([]);
  const [concurrency, setConcurrency] = useState(2);
  const [repetitions, setRepetitions] = useState(3);
  const [batch, setBatch] = useState(16);
  const [cold, setCold] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const report = reports?.find((b) => b.id === selected);
  return (
    <div className="workspace-page">
      <PageHeader eyebrow="" title="Benchmarks" />
      <ErrorBox message={error || message} />
      {admin && (
        <section className="workspace-section">
          <h2>New experiment</h2>
          <form
            className="experiment-form"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                const b = await post<Benchmark>("/benchmarks", {
                  workload,
                  input_digest: digest,
                  node_ids: ids,
                  concurrency,
                  repetitions,
                  batch_policy: { target_units: batch },
                  cold,
                });
                await refresh();
                setSelected(b.id);
                setMessage("");
              } catch (e) {
                setMessage(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className="grid">
              <label>
                Workload
                <select
                  value={workload}
                  onChange={(e) => setWorkload(e.target.value)}
                >
                  <option value="embeddings">Document embeddings</option>
                  <option value="training">Image classifier training</option>
                </select>
              </label>
              <label>
                Input digest
                <input
                  required
                  value={digest}
                  onChange={(e) => setDigest(e.target.value)}
                  placeholder="sha256:…"
                />
              </label>
              <label>
                Concurrency
                <input
                  type="number"
                  min="1"
                  value={concurrency}
                  onChange={(e) => setConcurrency(+e.target.value)}
                  required
                />
              </label>
              <label>
                Repetitions
                <input
                  type="number"
                  min="3"
                  value={repetitions}
                  onChange={(e) => setRepetitions(+e.target.value)}
                  required
                />
              </label>
              <label>
                Batch size
                <input
                  type="number"
                  min="1"
                  value={batch}
                  onChange={(e) => setBatch(+e.target.value)}
                  required
                />
              </label>
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={cold}
                onChange={(e) => setCold(e.target.checked)}
              />
              Cold start
            </label>
            <fieldset className="form-section">
              <legend>Devices</legend>
              {nodes?.map((n) => (
                <label className="check" key={n.id}>
                  <input
                    type="checkbox"
                    checked={ids.includes(n.id)}
                    onChange={(e) =>
                      setIds(
                        e.target.checked
                          ? [...ids, n.id]
                          : ids.filter((id) => id !== n.id),
                      )
                    }
                  />
                  {n.name}
                </label>
              ))}
            </fieldset>
            <div className="form-footer">
              <Button disabled={busy || !ids.length}>
                {busy ? "Creating experiment…" : "Create experiment"}
              </Button>
            </div>
          </form>
        </section>
      )}
      <section className="workspace-section">
        <h2>Saved experiments</h2>
        {reports?.length === 0 && (
          <Empty>
            No measurements yet. Run an experiment with real devices.
          </Empty>
        )}
        {reports?.map((b) => (
          <div className="row" key={b.id}>
            <button
              className="item-button"
              aria-expanded={selected === b.id}
              onClick={() => setSelected(selected === b.id ? "" : b.id)}
            >
              {b.workload} · {b.experiment_id}
              <ChevronRight aria-hidden="true" />
            </button>
            <Status value={b.state} />
          </div>
        ))}
      </section>
      {report && (
        <section className="workspace-section">
          <h2>
            {report.workload} / {report.experiment_id}
          </h2>
          <ThroughputChart report={report} />
          <dl>
            <dt>Input digest</dt>
            <dd>
              <code>{report.input_digest}</code>
            </dd>
            <dt>Commit</dt>
            <dd>
              <code>{report.git_sha}</code>
            </dd>
            <dt>Concurrency</dt>
            <dd>{report.concurrency}</dd>
            <dt>Bottleneck</dt>
            <dd>{report.bottleneck || "Not identified"}</dd>
          </dl>
          <TimingChart timings={report.timings_ms} />
          <UtilizationChart records={report.utilization} />
          <h3>Standalone records</h3>
          {report.standalone.map((s) => (
            <p key={s.node_id}>
              {s.node_id}: {s.provenance}; raw rates: {s.rates.join(", ")}
            </p>
          ))}
          {report.flags.map((f) => (
            <div className="notice" key={f}>
              {f}
            </div>
          ))}
          <div className="actions">
            <a
              className="button outline"
              href={"/api/v1/benchmarks/" + report.id + "/export?format=json"}
              download
            >
              Export JSON
            </a>
            <a
              className="button outline"
              href={"/api/v1/benchmarks/" + report.id + "/export?format=csv"}
              download
            >
              Export CSV
            </a>
          </div>
          {admin && (
            <label>
              Import standalone records (JSON)
              <input
                type="file"
                accept="application/json"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  if (file.size > 10 * 1024 * 1024) {
                    setMessage(
                      "Standalone import exceeds 10 MiB. Split records into smaller files.",
                    );
                    return;
                  }
                  try {
                    await post(
                      "/benchmarks/" + report.id + "/standalone-results",
                      JSON.parse(await file.text()),
                    );
                    await refresh();
                  } catch (e) {
                    setMessage(errorText(e));
                  }
                }}
              />
            </label>
          )}
        </section>
      )}
    </div>
  );
}
