import type { Attempt } from "../lib/types";
import { Status, Empty } from "./Common";
export function AttemptTimeline({ attempts }: { attempts: Attempt[] }) {
  return (
    <section>
      <h3>Attempt history</h3>
      {!attempts.length && <Empty>No recorded attempts.</Empty>}
      {attempts.map((a) => (
        <article className="card" key={a.attempt_id}>
          <div className="row">
            <b>{a.node_id}</b>
            <Status value={a.state} />
          </div>
          <p>{a.message}</p>
          <small>
            {a.occurred_at} · {a.attempt_id}
          </small>
          <dl>
            <dt>Input digest</dt>
            <dd>
              <code>{a.input_digest || "Not reported"}</code>
            </dd>
            <dt>Output digest</dt>
            <dd>
              <code>{a.output_digest || "Not reported"}</code>
            </dd>
          </dl>
        </article>
      ))}
    </section>
  );
}
