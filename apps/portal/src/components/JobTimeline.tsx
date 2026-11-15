import type { Job } from "../lib/types";
import { Status } from "./Common";
export function JobTimeline({ job }: { job: Job }) {
  return (
    <section className="card">
      <h2>Task progress</h2>
      {job.tasks.length === 0 && <p>Preparing tasks…</p>}
      {job.tasks.map((t, i) => (
        <div className="row" key={t.id}>
          <div>
            <b>
              Step {i + 1} · {t.stage_id}
            </b>
            <p>
              {t.units} units · attempt {t.generation}
              {t.node_id ? " · device " + t.node_id.slice(0, 8) : ""}
            </p>
            {t.error_code && <p className="error">{t.error_code}</p>}
            <small>
              {t.verification_level
                ? "Verification: " + t.verification_level
                : "Result not verified yet"}
            </small>
          </div>
          <Status value={t.state} />
        </div>
      ))}
    </section>
  );
}
