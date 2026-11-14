import type { Job } from "../lib/types";
import { Status } from "./Common";
export function JobTimeline({ job }: { job: Job }) {
  return (
    <section className="card">
      <h2>Execution timeline</h2>
      {job.stages.map((stage) => (
        <div className="row" key={stage.stage_id}>
          <span>{stage.stage_id}</span>
          <Status value={stage.state} />
        </div>
      ))}
      {job.attempts.map((a) => (
        <div className="row" key={a.attempt_id}>
          <div>
            <b>{a.node_id}</b>
            <p>{a.message}</p>
            <small>
              {a.occurred_at} · {a.attempt_id}
            </small>
          </div>
          <Status value={a.state} />
        </div>
      ))}
    </section>
  );
}
