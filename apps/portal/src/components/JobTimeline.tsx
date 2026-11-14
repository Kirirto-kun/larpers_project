import type { Job } from "../lib/types";
import { Status } from "./Common";
export function JobTimeline({ job }: { job: Job }) {
  return (
    <section className="card">
      <h2>Ход выполнения</h2>
      {job.tasks.length === 0 && <p>Подготовка задач…</p>}
      {job.tasks.map((t, i) => (
        <div className="row" key={t.id}>
          <div>
            <b>
              Шаг {i + 1} · {t.stage_id}
            </b>
            <p>
              {t.units} ед. · попытка {t.generation}
              {t.node_id ? " · устройство " + t.node_id.slice(0, 8) : ""}
            </p>
            {t.error_code && <p className="error">{t.error_code}</p>}
            <small>
              {t.verification_level
                ? "Проверка: " + t.verification_level
                : "Результат ещё не подтверждён"}
            </small>
          </div>
          <Status value={t.state} />
        </div>
      ))}
    </section>
  );
}
