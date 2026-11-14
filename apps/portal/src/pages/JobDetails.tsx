import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useResource } from "../lib/hooks";
import { api, post, errorText } from "../lib/api";
import type { Job } from "../lib/types";
import { JobTimeline } from "../components/JobTimeline";
import { ResultViewer } from "../components/ResultViewer";
import { Button } from "../components/ui/button";
import { PageHeader, Status, ErrorBox, Metric } from "../components/Common";
export function JobDetails() {
  const { id } = useParams();
  const {
    data: job,
    error,
    refresh,
    connected,
  } = useResource<Job>("/jobs/" + id, "/jobs/" + id + "/events");
  const [logs, setLogs] = useState<{
    lines: string[];
    truncated: boolean;
    next_cursor?: string;
  }>();
  const [logError, setLogError] = useState("");
  const [actionError, setError] = useState("");
  const [query, setQuery] = useState("");
  const [searchId, setSearchId] = useState("");
  const [search, setSearch] = useState<Job>();
  const [busy, setBusy] = useState(false);
  async function loadLogs(cursor?: string) {
    try {
      const page = await api<NonNullable<typeof logs>>(
        "/jobs/" +
          id +
          "/logs" +
          (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""),
      );
      setLogs((previous) =>
        cursor && previous
          ? { ...page, lines: [...previous.lines, ...page.lines] }
          : page,
      );
      setLogError("");
    } catch (e) {
      setLogError(errorText(e));
    }
  }
  useEffect(() => {
    void loadLogs();
  }, [id]);
  useEffect(() => {
    if (!searchId) return;
    let active = true;
    const poll = async () => {
      try {
        const result = await api<Job>("/jobs/" + searchId);
        if (active) setSearch(result);
      } catch (e) {
        if (active) setError(errorText(e));
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [searchId]);
  if (!job)
    return (
      <>
        <ErrorBox message={error} />
        <p>Loading task…</p>
      </>
    );
  return (
    <>
      <PageHeader eyebrow="TASK DETAILS" title={job.name}>
        <span>{job.id}</span>
      </PageHeader>
      <Status value={job.state} />
      {!connected && (
        <div className="notice">
          Live connection interrupted. Reconnecting and refreshing saved state…
        </div>
      )}
      <ErrorBox message={error || actionError} />
      {job.state === "waiting_for_capacity" && (
        <div className="notice">
          Waiting for a compatible device. Reconnect the required GPU or ask the
          pool owner for capacity.
        </div>
      )}
      {job.failed_units > 0 && (
        <div className="notice">
          Partial result: {job.failed_units} units failed. Available files do
          not represent a complete successful run.
        </div>
      )}
      <div className="metrics" style={{ marginTop: 24 }}>
        <Metric label="Accepted units" value={job.accepted_units} />
        <Metric label="Total units" value={job.total_units} />
        <Metric label="Failed units" value={job.failed_units} />
        <Metric
          label="Completion"
          value={
            job.total_units
              ? Math.round((job.accepted_units / job.total_units) * 100) + "%"
              : "—"
          }
        />
      </div>
      <div className="grid">
        <JobTimeline job={job} />
        <ResultViewer job={job} />
      </div>
      {job.errors?.map((message, i) => (
        <ErrorBox key={i} message={message} />
      ))}
      {job.index_artifact_id && (
        <section className="card">
          <h2>Search this index</h2>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                const r = await post<{ search_job_id: string }>(
                  "/jobs/" + id + "/search",
                  { query, top_k: 5, index_artifact_id: job.index_artifact_id },
                );
                setSearchId(r.search_job_id);
                setSearch(undefined);
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <label>
              Your question
              <input
                required
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <Button disabled={busy}>Search sources</Button>
          </form>
          {search && (
            <>
              <Status value={search.state} />
              <ResultViewer job={search} />
            </>
          )}
        </section>
      )}
      <section className="card">
        <h2>Logs</h2>
        <ErrorBox message={logError} />
        {logs?.truncated && (
          <div className="notice">Logs were truncated by the server.</div>
        )}
        <pre>{logs?.lines.join("\n") || "No log lines available."}</pre>
        {logs?.next_cursor && (
          <Button
            variant="outline"
            onClick={() => void loadLogs(logs.next_cursor)}
          >
            Load more logs
          </Button>
        )}
      </section>
      <div className="actions">
        <Button
          variant="outline"
          onClick={() => {
            void refresh();
            void loadLogs();
          }}
        >
          Refresh task
        </Button>
        {!["succeeded", "failed", "cancelled"].includes(job.state) && (
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
    </>
  );
}
