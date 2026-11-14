import { useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { useResource } from "../lib/hooks";
import { useJobLogs } from "../lib/logs";
import { post, errorText } from "../lib/api";
import type { Job } from "../lib/types";
import {
  PageHeader,
  Status,
  ErrorBox,
  Metric,
  date,
} from "../components/Common";
import { Button } from "../components/ui/button";
import { JobTimeline } from "../components/JobTimeline";
import { ResultViewer } from "../components/ResultViewer";
export function JobDetails() {
  const { id } = useParams();
  const nav = useNavigate();
  const [query, setQuery] = useState("");
  const [indexId, setIndexId] = useState("");
  const {
    data: job,
    error,
    refresh,
    connected,
  } = useResource<Job>("/jobs/" + id, "/jobs/" + id + "/events");
  const { data: logs, error: logError } = useJobLogs(id);
  const [actionError, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Link to="/">← К задачам</Link>
      <PageHeader eyebrow="ЗАДАЧА" title={"Задача · " + (id || "").slice(0, 8)}>
        Результаты и подтверждённый прогресс вычислений.
      </PageHeader>
      <ErrorBox message={error || actionError} />
      {!job && !error && <p role="status">Загружаем задачу…</p>}
      {job && (
        <>
          <div className="row">
            <div>
              <Status value={job.state} />
              <p className="muted">Создана {date(job.created_at)}</p>
            </div>
            {!["succeeded", "partial", "failed", "cancelled"].includes(
              job.state,
            ) && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await post("/jobs/" + id + "/cancel", {});
                    await refresh();
                  } catch (e) {
                    setError(errorText(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Отменить задачу
              </Button>
            )}
          </div>
          {!connected && (
            <p className="muted" role="status">
              Прямое соединение прервано. Обновляем данные каждые 5 секунд.
            </p>
          )}
          {job.state === "waiting_for_capacity" && (
            <div className="notice">
              Ожидаем подходящее устройство.{" "}
              <Link to="/contribute">Подключить компьютер</Link>
            </div>
          )}
          {job.failed_units > 0 && (
            <div className="notice">
              Частичный результат: {job.failed_units} единиц не выполнено. Они
              не учитываются как принятые.
            </div>
          )}
          <div className="metrics">
            <Metric label="Принято" value={job.accepted_units} />
            <Metric label="Всего" value={job.total_units} />
            <Metric label="С ошибкой" value={job.failed_units} />
            <Metric
              label="Прогресс"
              value={
                job.total_units
                  ? Math.round((100 * job.accepted_units) / job.total_units) +
                    "%"
                  : "—"
              }
            />
          </div>
          <progress
            className="job-progress"
            aria-label="Подтверждённый прогресс"
            value={job.accepted_units}
            max={Math.max(1, job.total_units)}
          />
          <JobTimeline job={job} />
          <ResultViewer job={job} />
          {job.artifacts.length > 0 && (
            <details className="card">
              <summary>Поиск по результату</summary>
              <p>
                Для задачи, которая создала поисковый индекс, выберите файл
                индекса. Поиск выполняется в пуле как отдельная задача.
              </p>
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  setBusy(true);
                  try {
                    const r = await post<{ search_job_id: string }>(
                      "/jobs/" + id + "/search",
                      {
                        query: query.trim(),
                        top_k: 10,
                        index_artifact_id: indexId || job.artifacts[0].id,
                      },
                    );
                    nav("/jobs/" + r.search_job_id);
                    setError("");
                  } catch (e) {
                    setError(errorText(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <label>
                  Файл индекса
                  <select
                    value={indexId || job.artifacts[0].id}
                    onChange={(e) => setIndexId(e.target.value)}
                  >
                    {job.artifacts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Поисковый запрос
                  <input
                    required
                    maxLength={4000}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                <Button disabled={busy || !query.trim()}>
                  Найти в индексе
                </Button>
              </form>
            </details>
          )}
          <section className="card">
            <h2>Журнал</h2>
            <ErrorBox message={logError} />
            {logs?.truncated && <p className="notice">Журнал сокращён</p>}
            {logs?.entries.some((e) => e.gap) && (
              <p className="notice">Часть строк журнала недоступна.</p>
            )}
            <pre aria-label="Журнал выполнения">
              {logs?.entries.map((e) => e.text).join("\n") ||
                "Записей пока нет."}
            </pre>
          </section>
        </>
      )}
    </>
  );
}
