import type { Preview } from "../lib/types";
import { Status, bytes, ErrorBox, date } from "./Common";
const knownAssumptions: Record<string, string> = {
  "Небольшой проверочный расчёт в изолированном контейнере без сети.":
    "A small verification task in an isolated container without network access.",
};
export function PlanPreview({ plan: preview }: { plan: Preview }) {
  return (
    <section className="card preview-card">
      <div className="row">
        <h2>Review the plan</h2>
        <Status value={preview.state} />
      </div>
      <ErrorBox message={preview.error_message || ""} />
      {!preview.plan && (
        <p>
          {preview.state === "needs_input"
            ? "Answer the question below to continue."
            : preview.state === "failed"
              ? "Edit your request or try preparing the plan again."
              : "Preparing and validating your plan. Computation has not started."}
        </p>
      )}
      {preview.plan && (
        <>
          <p className="muted">
            Start when you are ready. Your task waits if no suitable devices are
            available.
          </p>
          {preview.plan.stages.map((s, i) => (
            <article className="plan-stage" key={s.stage_id}>
              <h3>
                Step {i + 1} · {s.stage_id}
              </h3>
              <dl>
                <dt>Compute</dt>
                <dd>
                  {[
                    ...new Set(
                      s.variants.map((v) =>
                        v.accelerator === "cuda" ? "NVIDIA CUDA" : "CPU",
                      ),
                    ),
                  ].join(" / ")}
                </dd>
                <dt>Step time limit</dt>
                <dd>{Math.round(s.task_deadline_ms / 1000)} sec</dd>
                <dt>Resources</dt>
                <dd>
                  {s.variants.map((v, k) => (
                    <div key={k}>
                      {(v.resource_requirements.cpu_millis / 1000).toFixed(1)}{" "}
                      CPU · {bytes(v.resource_requirements.memory_bytes)} memory
                      {v.resource_requirements.gpu_slots > 0 ? " · GPU" : ""}
                    </div>
                  ))}
                </dd>
                <dt>Results</dt>
                <dd>{s.outputs.map((o) => o.path).join(", ")}</dd>
                <dt>If interrupted</dt>
                <dd>
                  {s.resume_policy === "checkpoint"
                    ? "Resume from checkpoint"
                    : s.resume_policy === "restart"
                      ? "Restart"
                      : "Resume not supported"}
                </dd>
              </dl>
              <details>
                <summary>Command and environment</summary>
                <code>{s.argv.join(" ")}</code>
                <p>
                  {s.adapter.id} · {s.adapter.version}
                </p>
              </details>
            </article>
          ))}
          <p>Task deadline: {date(preview.plan.job_deadline_at)}</p>
          <details>
            <summary>Assumptions and evidence</summary>
            <ul>
              {preview.plan.assumptions.map((a, i) => (
                <li key={i}>{knownAssumptions[a] || a}</li>
              ))}
              {preview.plan.evidence.map((e, i) => (
                <li key={"e" + i}>
                  {e.source}: {e.detail}
                </li>
              ))}
            </ul>
          </details>
          <small>Plan version: {preview.plan_revision}</small>
          <details>
            <summary>Plan details</summary>
            <p>
              ID: <code>{preview.id}</code>
            </p>
            <p>Version: {preview.plan_revision}</p>
          </details>
        </>
      )}
    </section>
  );
}
