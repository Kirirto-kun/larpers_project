import type { Preview } from "../lib/types";
import { Status } from "./Common";
export function PlanPreview({ plan }: { plan: Preview }) {
  return (
    <section className="card">
      <div className="row">
        <h2>Execution preview</h2>
        <Status value={plan.state} />
      </div>
      <p>{plan.summary}</p>
      <dl>
        <dt>Compute</dt>
        <dd>{plan.accelerator}</dd>
        <dt>Data location</dt>
        <dd>{plan.data_location}</dd>
        <dt>Outputs</dt>
        <dd>{plan.outputs.join(", ") || "Not determined"}</dd>
        <dt>Recovery</dt>
        <dd>{plan.resume_policy}</dd>
        <dt>Duration</dt>
        <dd>
          {plan.duration_ms === null
            ? "Unknown"
            : plan.duration_ms + " ms (estimate)"}
        </dd>
        <dt>Plan revision</dt>
        <dd>{plan.plan_revision}</dd>
      </dl>
      <details>
        <summary>Assumptions and evidence</summary>
        <ul>
          {[...plan.assumptions, ...plan.evidence].map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
