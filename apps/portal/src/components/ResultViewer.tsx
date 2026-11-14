import type { Job } from "../lib/types";
import { artifactUrl } from "../lib/api";
import { bytes, Empty } from "./Common";
export function ResultViewer({ job }: { job: Job }) {
  return (
    <section className="card">
      <h2>Результаты</h2>
      {job.artifacts.length === 0 && (
        <Empty>Файлы появятся после выполнения и проверки результата.</Empty>
      )}
      {job.artifacts.map((a) => (
        <div className="row" key={a.id}>
          <div>
            <a href={artifactUrl(a.id)} download={a.name}>
              {a.name}
            </a>
            <p>{bytes(a.size_bytes)}</p>
            <details>
              <summary>Контрольная сумма SHA-256</summary>
              <code>{a.sha256}</code>
            </details>
          </div>
        </div>
      ))}
      {job.search_result?.matches.map((m) => (
        <article className="card" key={m.item_id}>
          <h3>{m.source}</h3>
          <p>{m.excerpt}</p>
          <small>Совпадение: {m.score.toFixed(3)}</small>
        </article>
      ))}
    </section>
  );
}
