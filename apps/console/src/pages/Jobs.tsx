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
      <PageHeader eyebrow="ЗАДАЧИ" title="Вся работа пула">
        Принятые единицы учитываются после проверки. Неудачные попытки не
        увеличивают полезный результат.
      </PageHeader>
      <ErrorBox message={error || message} />
      <section className="card">
        {data?.length === 0 && <Empty>Задач пока нет.</Empty>}
        {data?.map((j) => (
          <div className="row" key={j.id}>
            <div>
              <Button variant="ghost" onClick={() => setSelected(j.id)}>
                Задача {j.id.slice(0, 8)}
              </Button>
              <p>
                {date(j.created_at)} · {j.accepted_units} / {j.total_units}{" "}
                принято · {j.failed_units} с ошибкой
              </p>
            </div>
            <Status value={j.state} />
          </div>
        ))}
      </section>
      {job && (
        <section className="card">
          <h2>Задача {job.id.slice(0, 8)}</h2>
          <p>
            Владелец: <code>{job.owner_id}</code>
          </p>
          {job.tasks.map((t) => (
            <div className="row" key={t.id}>
              <div>
                <b>{t.stage_id}</b>
                <p>
                  {t.units} ед. · попытка {t.generation} ·{" "}
                  {t.node_id || "Устройство не назначено"}
                </p>
                {t.error_code && <p className="error">{t.error_code}</p>}
              </div>
              <Status value={t.state} />
            </div>
          ))}
          <div className="actions">
            <a className="button outline" href={"/jobs/" + job.id}>
              Открыть журнал и результаты
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
                Отменить задачу
              </Button>
            )}
          </div>
        </section>
      )}
    </>
  );
}
