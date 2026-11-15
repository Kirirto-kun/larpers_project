import { useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { useResource } from "../lib/hooks";
import { useJobLogs } from "../lib/logs";
import { post, errorText } from "../lib/api";
import type { Job } from "../lib/types";
import {
  PageHeader,
  Status,
  ErrorBox,
  Metric,
  date,
} from "../components/Common";
import { Button } from "../components/ui/button";
import { JobTimeline } from "../components/JobTimeline";
import { ResultViewer } from "../components/ResultViewer";
export function JobDetails() {
  const { id } = useParams();
  const nav = useNavigate();
  const [query, setQuery] = useState("");
  const [indexId, setIndexId] = useState("");
  const {
    data: job,
    error,
    refresh,
    connected,
  } = useResource<Job>("/jobs/" + id, "/jobs/" + id + "/events");
  const { data: logs, error: logError } = useJobLogs(id);
  const [actionError, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Link to="/">← Back to tasks</Link>
      <PageHeader eyebrow="TASK" title={"Task · " + (id || "").slice(0, 8)}>
        Follow progress and download your results.
      </PageHeader>
      <ErrorBox message={error || actionError} />
      {!job && !error && <p role="status">Loading task…</p>}
      {job && (
        <>
          <div className="row">
            <div>
              <Status value={job.state} />
              <p className="muted">Created {date(job.created_at)}</p>
            </div>
            {!["succeeded", "partial", "failed", "cancelled"].includes(
              job.state,
            ) && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await post("/jobs/" + id + "/cancel", {});
                    await refresh();
                  } catch (e) {
                    setError(errorText(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Cancel task
              </Button>
            )}
          </div>
          {!connected && (
            <p className="muted" role="status">
              Reconnecting to live updates. Refreshing every 5 seconds.
            </p>
          )}
          {job.state === "waiting_for_capacity" && (
            <div className="notice">
              Waiting for a suitable device.{" "}
              <Link to="/contribute">Connect a computer</Link>
            </div>
          )}
          {job.failed_units > 0 && (
            <div className="notice">
              Partial result: {job.failed_units} units failed and are not
              counted as accepted.
            </div>
          )}
          <div className="metrics">
            <Metric label="Accepted" value={job.accepted_units} />
            <Metric label="Total" value={job.total_units} />
            <Metric label="Failed" value={job.failed_units} />
            <Metric
              label="Progress"
              value={
                job.total_units
                  ? Math.round((100 * job.accepted_units) / job.total_units) +
                    "%"
                  : "—"
              }
            />
          </div>
          <progress
            className="job-progress"
            aria-label="Verified progress"
            value={job.accepted_units}
            max={Math.max(1, job.total_units)}
          />
          <JobTimeline job={job} />
          <ResultViewer job={job} />
          {job.artifacts.length > 0 && (
            <details className="card">
              <summary>Search results</summary>
              <p>
                Choose the search index created by this task. Searching runs as
                a separate task in the pool.
              </p>
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  setBusy(true);
                  try {
                    const r = await post<{ search_job_id: string }>(
                      "/jobs/" + id + "/search",
                      {
                        query: query.trim(),
                        top_k: 10,
                        index_artifact_id: indexId || job.artifacts[0].id,
                      },
                    );
                    nav("/jobs/" + r.search_job_id);
                    setError("");
                  } catch (e) {
                    setError(errorText(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <label>
                  Index file
                  <select
                    value={indexId || job.artifacts[0].id}
                    onChange={(e) => setIndexId(e.target.value)}
                  >
                    {job.artifacts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Search query
                  <input
                    required
                    maxLength={4000}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                <Button disabled={busy || !query.trim()}>Search index</Button>
              </form>
            </details>
          )}
          <section className="card">
            <h2>Logs</h2>
            <ErrorBox message={logError} />
            {logs?.truncated && <p className="notice">Log truncated</p>}
            {logs?.entries.some((e) => e.gap) && (
              <p className="notice">Some log lines are unavailable.</p>
            )}
            <pre aria-label="Execution log">
              {logs?.entries.map((e) => e.text).join("\n") ||
                "No log entries yet."}
            </pre>
          </section>
        </>
      )}
    </>
  );
}
