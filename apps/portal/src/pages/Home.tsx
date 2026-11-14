import { Link } from "react-router-dom";
import { ArrowUpRight, Monitor, Files } from "lucide-react";
import { useResource } from "../lib/hooks";
import type { Job, Stats } from "../lib/types";
import { Button } from "../components/ui/button";
import { Status, ErrorBox, Empty, Metric, date } from "../components/Common";
export function JobList({ jobs }: { jobs: Job[] }) {
  return jobs.length ? (
    jobs.map((j) => (
      <div className="row" key={j.id}>
        <div>
          <Link to={"/jobs/" + j.id}>
            {j.purpose === "benchmark"
              ? "Измерение производительности"
              : "Задача"}{" "}
            · {j.id.slice(0, 8)}
          </Link>
          <p className="muted">
            {date(j.created_at)} · {j.accepted_units} / {j.total_units} принято
          </p>
        </div>
        <Status value={j.state} />
      </div>
    ))
  ) : (
    <Empty>Здесь появятся ваши задачи и результаты.</Empty>
  );
}
export function Home({ ownerId }: { ownerId: string }) {
  const { data: stats, error } = useResource<Stats>("/me/stats");
  const { data: jobs, error: jobsError } = useResource<Job[]>("/jobs");
  return (
    <>
      <section className="hero">
        <p className="eyebrow">ОБЩИЕ РЕСУРСЫ — НОВЫЕ ВОЗМОЖНОСТИ</p>
        <h1>Ваше рабочее пространство</h1>
        <p>
          Запустите вычисления или подключите свой компьютер. Campus распределит
          работу между доступными устройствами.
        </p>
        <div className="actions">
          <Button asChild>
            <Link to="/new">
              Создать задачу <ArrowUpRight aria-hidden="true" size={18} />
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/contribute">Подключить компьютер</Link>
          </Button>
        </div>
      </section>
      <ErrorBox message={error || jobsError} />
      <div className="metrics">
        <Metric
          label="Ваши устройства в сети"
          value={
            stats ? stats.devices_online + " / " + stats.devices_total : "—"
          }
        />
        <Metric
          label="Принято единиц работы"
          value={stats?.accepted_units ?? "—"}
          hint="Только подтверждённый результат"
        />
        <Metric
          label="Ваши задачи"
          value={
            stats
              ? Object.values(stats.jobs_by_state).reduce((a, b) => a + b, 0)
              : "—"
          }
        />
        <Metric
          label="Завершено задач"
          value={stats ? stats.jobs_by_state.succeeded || 0 : "—"}
          hint="Ваши успешно выполненные задачи"
        />
      </div>
      <div className="grid">
        <section className="card">
          <Files aria-hidden="true" />
          <h2>От идеи к результату</h2>
          <p className="muted">
            Опишите задачу, добавьте код или данные. Сначала проверьте план —
            запуск всегда за вами.
          </p>
          <Link to="/new">Подготовить первую задачу →</Link>
        </section>
        <section className="card">
          <Monitor aria-hidden="true" />
          <h2>Ваш вклад под контролем</h2>
          <p className="muted">
            Выберите нагрузку 10%, 50% или 90% и ставьте участие на паузу в
            любой момент.
          </p>
          <Link to="/contribute">Настроить устройства →</Link>
        </section>
      </div>
      <section className="card">
        <h2>Последние задачи</h2>
        {jobs ? (
          <JobList jobs={jobs.filter((job) => job.owner_id === ownerId)} />
        ) : (
          <p role="status">Загружаем задачи…</p>
        )}
      </section>
      <section className="card">
        <h2>История участия</h2>
        {stats?.history.length ? (
          <div
            className="history"
            role="img"
            aria-label="Подтверждённая работа по дням"
          >
            {stats.history.map((h) => (
              <div className="chart-row" key={h.date}>
                <span>{h.date}</span>
                <meter
                  min={0}
                  max={Math.max(
                    1,
                    ...stats.history.map((x) => x.accepted_units),
                  )}
                  value={h.accepted_units}
                />
                <span>{h.accepted_units} ед.</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty>
            Подтверждённая работа появится здесь после выполнения задач на ваших
            устройствах.
          </Empty>
        )}
      </section>
    </>
  );
}
