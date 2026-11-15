import { useState } from "react";
import { useResource } from "../lib/hooks";
import type { Job } from "../lib/types";
import { post, errorText } from "../lib/api";
import {
  PageHeader,
  Status,
  ErrorBox,
  Empty,
  date,
} from "../components/Common";
import { Button } from "../components/ui/button";
export function Jobs() {
  const { data, error, refresh } = useResource<Job[]>("/jobs");
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const job = data?.find((j) => j.id === selected);
  return (
    <>
      <PageHeader title="Tasks" />
      <ErrorBox message={error || message} />
      <section className="card">
        {!data && !error && <Empty>Loading tasks…</Empty>}
        {data?.length === 0 && <Empty>No tasks yet.</Empty>}
        {data?.map((j) => (
          <div className="row" key={j.id}>
            <div>
              <Button variant="ghost" onClick={() => setSelected(j.id)}>
                Task {j.id.slice(0, 8)}
              </Button>
              <p>
                {date(j.created_at)} · {j.accepted_units} / {j.total_units}{" "}
                accepted · {j.failed_units} failed
              </p>
            </div>
            <Status value={j.state} />
          </div>
        ))}
      </section>
      {job && (
        <section className="card">
          <h2>Task {job.id.slice(0, 8)}</h2>
          <p>
            Owner: <code>{job.owner_id}</code>
          </p>
          {job.tasks.map((t) => (
            <div className="row" key={t.id}>
              <div>
                <b>{t.stage_id}</b>
                <p>
                  {t.units} units · attempt {t.generation} ·{" "}
                  {t.node_id || "No device assigned"}
                </p>
                {t.error_code && <p className="error">{t.error_code}</p>}
              </div>
              <Status value={t.state} />
            </div>
          ))}
          <div className="actions">
            <a className="button outline" href={"/jobs/" + job.id}>
              View log and results
            </a>
            {!["succeeded", "partial", "failed", "cancelled"].includes(
              job.state,
            ) && (
              <Button
                disabled={busy}
                variant="outline"
                onClick={async () => {
                  setBusy(true);
                  try {
                    await post("/jobs/" + job.id + "/cancel", {});
                    await refresh();
                    setMessage("");
                  } catch (e) {
                    setMessage(errorText(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Cancel task
              </Button>
            )}
          </div>
        </section>
      )}
    </>
  );
}
