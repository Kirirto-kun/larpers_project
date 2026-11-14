import { Link } from "react-router-dom";
import { ArrowUpRight, Files, Monitor, ArrowRight } from "lucide-react";
import { useResource } from "../lib/hooks";
import type { Job } from "../lib/types";
import { Button } from "../components/ui/button";
import { Status, ErrorBox, Empty } from "../components/Common";
export function Home() {
  const { data: jobs, error } = useResource<Job[]>("/jobs");
  return (
    <>
      <section className="hero">
        <p className="eyebrow">YOUR TEAM'S SHARED COMPUTE</p>
        <h1>
          Big ideas.
          <br />A little help from your pool.
        </h1>
        <p>
          Turn your team's spare CPU and GPU time into useful results. Bring a
          task, bring your code, or lend a laptop.
        </p>
        <div className="actions">
          <Button asChild>
            <Link to="/new">
              Run a task <ArrowUpRight size={17} />
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/contribute">Contribute compute</Link>
          </Button>
        </div>
      </section>
      <div className="grid">
        <section className="card">
          <Files />
          <h2>From documents to answers</h2>
          <p className="muted">
            Build a searchable index across the pool. Find relevant passages
            with their original sources.
          </p>
          <Link to="/new?recipe=embeddings">
            Explore document search <ArrowRight size={14} />
          </Link>
        </section>
        <section className="card">
          <Monitor />
          <h2>Your code, more room to run</h2>
          <p className="muted">
            Upload a project or use a training recipe. Get your files, model
            weights, and reported metrics back.
          </p>
          <Link to="/new?recipe=training">
            Start a training task <ArrowRight size={14} />
          </Link>
        </section>
      </div>
      <section className="card">
        <h2>Recent work</h2>
        <ErrorBox message={error} />
        {!jobs && !error && <p>Loading your tasks…</p>}
        {jobs?.length === 0 && (
          <Empty>Your first result starts with a task.</Empty>
        )}
        {jobs?.map((job) => (
          <div className="row" key={job.id}>
            <div>
              <Link to={"/jobs/" + job.id}>{job.name}</Link>
              <p className="muted">
                {job.accepted_units} / {job.total_units} accepted units
              </p>
            </div>
            <Status value={job.state} />
          </div>
        ))}
      </section>
    </>
  );
}
