import { Link } from "react-router-dom";
import { ArrowUpRight, Plus, Monitor, Files } from "lucide-react";
import { useResource } from "../lib/hooks";
import type { Job, Stats } from "../lib/types";
import { Button } from "../components/ui/button";
import { Status, ErrorBox, Empty, Metric, date } from "../components/Common";

export function JobList({ jobs }: { jobs: Job[] }) {
  return jobs.length ? (
    jobs.map((job) => (
      <div className="row job-list-row" key={job.id}>
        <span className="list-icon">
          <Files size={18} aria-hidden="true" />
        </span>
        <div className="job-list-name">
          <Link to={"/jobs/" + job.id}>
            {job.purpose === "benchmark" ? "Benchmark" : "Task"}{" "}
            <span className="muted">{job.id.slice(0, 8)}</span>
          </Link>
          <p>
            {date(job.created_at)} · {job.accepted_units} / {job.total_units}{" "}
            completed
          </p>
        </div>
        <Status value={job.state} />
        <Link
          className="row-link"
          to={"/jobs/" + job.id}
          aria-label={"Open task " + job.id.slice(0, 8)}
        >
          <ArrowUpRight size={18} aria-hidden="true" />
        </Link>
      </div>
    ))
  ) : (
    <Empty>No tasks yet. Create a task to get started.</Empty>
  );
}

export function Home({ ownerId }: { ownerId: string }) {
  const { data: stats, error } = useResource<Stats>("/me/stats");
  const { data: jobs, error: jobsError } = useResource<Job[]>("/jobs");
  return (
    <>
      <header className="workspace-heading">
        <div>
          <p className="eyebrow">Overview</p>
          <h1>Your workspace</h1>
          <p className="muted">A place for your next big idea.</p>
        </div>
        <Button asChild>
          <Link to="/new">
            <Plus size={18} aria-hidden="true" /> New task
          </Link>
        </Button>
      </header>
      <ErrorBox message={error || jobsError} />
      <div className="metrics">
        <Metric
          label="Devices online"
          value={
            stats ? stats.devices_online + " / " + stats.devices_total : "—"
          }
        />
        <Metric label="Work completed" value={stats?.accepted_units ?? "—"} />
        <Metric
          label="Total tasks"
          value={
            stats
              ? Object.values(stats.jobs_by_state).reduce((a, b) => a + b, 0)
              : "—"
          }
        />
        <Metric
          label="Finished tasks"
          value={stats ? stats.jobs_by_state.succeeded || 0 : "—"}
        />
      </div>
      <section className="workspace-banner">
        <div>
          <p className="eyebrow">Make room for more</p>
          <h2>Your computer could help.</h2>
          <p>Share spare capacity. Choose your pace. Pause any time.</p>
        </div>
        <Button asChild variant="outline">
          <Link to="/contribute">
            Connect a computer <ArrowUpRight size={18} aria-hidden="true" />
          </Link>
        </Button>
        <Monitor className="banner-icon" size={100} aria-hidden="true" />
      </section>
      <section className="card recent-jobs">
        <div className="section-heading">
          <h2>Recent tasks</h2>
          <Link className="text-link" to="/new">
            Create task <Plus size={16} aria-hidden="true" />
          </Link>
        </div>
        {jobs ? (
          <JobList
            jobs={jobs
              .filter((job) => job.owner_id === ownerId)
              .sort((a, b) => b.created_at.localeCompare(a.created_at))}
          />
        ) : (
          <p role="status">Loading tasks…</p>
        )}
      </section>
      <section className="card">
        <div className="section-heading">
          <h2>Your contribution</h2>
          <Link className="text-link" to="/contribute">
            Manage devices <ArrowUpRight size={16} aria-hidden="true" />
          </Link>
        </div>
        {stats ? (
          stats.history.length ? (
            <div
              className="history"
              role="img"
              aria-label="Completed work by day"
            >
              {stats.history.map((h) => (
                <div className="chart-row" key={h.date}>
                  <span>{h.date}</span>
                  <meter
                    min={0}
                    max={Math.max(
                      1,
                      ...stats.history.map((x) => x.accepted_units),
                    )}
                    value={h.accepted_units}
                  />
                  <span>{h.accepted_units} units</span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>Connect a computer to start contributing to the pool.</Empty>
          )
        ) : (
          <p role="status">Loading contribution…</p>
        )}
      </section>
    </>
  );
}
