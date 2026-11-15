import { useState } from "react";
import { UtilizationChart } from "../components/MeasurementCharts";
import { useResource } from "../lib/hooks";
import { post, errorText } from "../lib/api";
import type { Benchmark, Node } from "../lib/types";
import {
  PageHeader,
  ErrorBox,
  Empty,
  Status,
  Metric,
} from "../components/Common";
import { Button } from "../components/ui/button";
export function Benchmarks() {
  const {
    data: reports,
    error,
    refresh,
  } = useResource<Benchmark[]>("/benchmarks");
  const { data: nodes, error: nodeError } = useResource<Node[]>("/nodes");
  const [selected, setSelected] = useState("");
  const [previewId, setPreviewId] = useState("");
  const [revision, setRevision] = useState(1);
  const [ids, setIds] = useState<string[]>([]);
  const [concurrency, setConcurrency] = useState(1);
  const [repetitions, setRepetitions] = useState(3);
  const [warm, setWarm] = useState(true);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const report = reports?.find((b) => b.id === selected);
  return (
    <>
      <PageHeader title="Benchmarks" />
      <ErrorBox message={error || nodeError || message} />
      <details className="card">
        <summary>New benchmark</summary>
        <p>
          Use a validated plan from your workspace and select at least two
          devices. Import matching standalone runs to compare individual devices
          with the pool.
        </p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              const result = await post<Benchmark>("/benchmarks", {
                preview_id: previewId.trim(),
                plan_revision: revision,
                node_ids: ids,
                concurrency,
                repetitions,
                warm,
                batch_policy: {},
              });
              await refresh();
              setSelected(result.id);
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
              Validated plan ID
              <input
                required
                value={previewId}
                onChange={(e) => setPreviewId(e.target.value)}
              />
            </label>
            <label>
              Plan revision
              <input
                type="number"
                min={1}
                value={revision}
                onChange={(e) => setRevision(+e.target.value)}
                required
              />
            </label>
            <label>
              Concurrent tasks per device
              <input
                type="number"
                min={1}
                max={512}
                required
                value={concurrency}
                onChange={(e) => setConcurrency(+e.target.value)}
              />
            </label>
            <label>
              Repetitions
              <input
                type="number"
                min={3}
                max={20}
                required
                value={repetitions}
                onChange={(e) => setRepetitions(+e.target.value)}
              />
            </label>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={warm}
              onChange={(e) => setWarm(e.target.checked)}
            />
            Warm environment
          </label>
          <fieldset>
            <legend>Devices</legend>
            {!nodes && !nodeError && <p>Loading devices…</p>}
            {nodes?.length === 0 && <p>No devices registered.</p>}
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
          <Button disabled={busy || ids.length < 2}>
            {busy ? "Working…" : "Create benchmark"}
          </Button>
        </form>
      </details>
      <section className="card">
        <h2>Saved benchmarks</h2>
        {!reports && !error && <Empty>Loading benchmarks…</Empty>}
        {reports?.length === 0 && <Empty>No benchmarks yet.</Empty>}
        {reports?.map((b) => (
          <div className="row" key={b.id}>
            <Button variant="ghost" onClick={() => setSelected(b.id)}>
              Benchmark {b.id}
            </Button>
            <Status value={b.status} />
          </div>
        ))}
      </section>
      {report && (
        <section className="card">
          <h2>Benchmark {report.id}</h2>
          <div className="metrics">
            <Metric
              label="Pool efficiency"
              value={
                report.efficiency_percent == null
                  ? "—"
                  : report.efficiency_percent.toFixed(1) + "%"
              }
            />
            <Metric
              label="Speedup"
              value={
                report.speedup == null ? "—" : report.speedup.toFixed(2) + "×"
              }
            />
            <Metric
              label="Pool units / sec"
              value={report.pooled_units_per_second?.toFixed(2) ?? "—"}
            />
            <Metric label="Repetitions" value={report.config.repetitions} />
          </div>
          {report.efficiency_percent != null &&
            report.efficiency_percent > 100 && (
              <p className="notice">
                Efficiency exceeds 100%. Check that environments, warm-up and
                measurements are comparable.
              </p>
            )}
          <h3>Standalone throughput</h3>
          {Object.entries(report.standalone_units_per_second).map(
            ([id, rate]) => (
              <div className="chart-row" key={id}>
                <span>
                  {nodes?.find((n) => n.id === id)?.name || id.slice(0, 8)}
                </span>
                <meter
                  aria-label={
                    "Throughput for " +
                    (nodes?.find((n) => n.id === id)?.name || id)
                  }
                  min={0}
                  max={Math.max(
                    1,
                    ...Object.values(report.standalone_units_per_second),
                    report.pooled_units_per_second || 0,
                  )}
                  value={rate}
                />
                <span>{rate.toFixed(2)} units/s</span>
              </div>
            ),
          )}
          <dl>
            <dt>Concurrency</dt>
            <dd>{report.config.concurrency}</dd>
            <dt>Environment</dt>
            <dd>{report.config.warm ? "Warm" : "Cold start"}</dd>
            <dt>Manifest digest</dt>
            <dd>
              <code>{report.manifest_digest}</code>
            </dd>
            <dt>Configuration digest</dt>
            <dd>
              <code>{report.config_digest}</code>
            </dd>
          </dl>
          <h3>Raw measurements</h3>
          {report.standalone_results.map((r, i) => (
            <details key={i}>
              <summary>
                {r.node_id} · {r.units} units / {r.duration_ms} ms
              </summary>
              <p>
                Compute: {r.duration_ms} ms · verification: {r.verification_ms}{" "}
                ms
              </p>
              {r.utilization.length ? (
                <div>
                  <UtilizationChart samples={r.utilization} />
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Sample</th>
                          <th>Phase</th>
                          <th>CPU</th>
                          <th>GPU</th>
                        </tr>
                      </thead>
                      <tbody>
                        {r.utilization.map((u) => (
                          <tr key={u.sequence}>
                            <td>{u.sequence}</td>
                            <td>{u.phase}</td>
                            <td>
                              {u.cpu_percent == null
                                ? "Not measured"
                                : u.cpu_percent + "%"}
                            </td>
                            <td>
                              {u.gpu_percent == null
                                ? "Not measured"
                                : u.gpu_percent + "%"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <p>No telemetry recorded.</p>
              )}
            </details>
          ))}
          <div className="actions">
            <a
              className="button outline"
              href={"/api/v1/benchmarks/" + report.id + "/export"}
              download
            >
              Download JSON
            </a>
          </div>
          <label>
            Import standalone results (JSON)
            <input
              type="file"
              accept="application/json"
              disabled={busy}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                if (file.size > 10 * 1024 * 1024) {
                  setMessage("Choose a JSON file up to 10 MB.");
                  return;
                }
                setBusy(true);
                try {
                  const payload = JSON.parse(await file.text());
                  await post(
                    "/benchmarks/" + report.id + "/standalone-results",
                    payload,
                  );
                  await refresh();
                  setMessage("");
                } catch (e) {
                  setMessage(errorText(e));
                } finally {
                  setBusy(false);
                }
              }}
            />
          </label>
        </section>
      )}
    </>
  );
}
