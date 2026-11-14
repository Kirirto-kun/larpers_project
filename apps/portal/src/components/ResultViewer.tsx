import type { Job } from "../lib/types";
import { artifactUrl } from "../lib/api";
import { bytes, Empty } from "./Common";
export function ResultViewer({ job }: { job: Job }) {
  return (
    <section className="card">
      <h2>Results & files</h2>
      <p className="muted">
        Validation:{" "}
        {job.validation_level || "Not reported; quality is not verified."}
      </p>
      {job.artifacts.length === 0 && (
        <Empty>Files will appear after they are published.</Empty>
      )}
      {job.artifacts.map((a) => (
        <div className="row" key={a.id}>
          <div>
            {a.ready ? (
              <a href={artifactUrl(a.id)} download={a.name}>
                {a.name} ↓
              </a>
            ) : (
              <span>{a.name} · Not ready</span>
            )}
            <p className="muted">{bytes(a.size_bytes)}</p>
            {a.digest && <code>{a.digest}</code>}
          </div>
        </div>
      ))}
      {job.metrics && (
        <dl>
          {Object.entries(job.metrics).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {job.matches?.map((m) => (
        <article className="card" key={m.item_id}>
          <h3>{m.source}</h3>
          <p>{m.excerpt}</p>
          <small>Score: {m.score}</small>
        </article>
      ))}
    </section>
  );
}
