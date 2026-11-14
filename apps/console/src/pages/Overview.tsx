import { Link } from "react-router-dom";
import { useResource } from "../lib/hooks";
import type { Summary, Node } from "../lib/types";
import {
  PageHeader,
  Metric,
  ErrorBox,
  Status,
  Empty,
  bytes,
  stateNames,
  date,
} from "../components/Common";
export function Overview() {
  const { data: summary, error } = useResource<Summary>("/pool/summary");
  const { data: nodes, error: nodeError } = useResource<Node[]>("/nodes");
  return (
    <>
      <PageHeader eyebrow="ОБЗОР ПУЛА" title="Пул под контролем">
        Устройства, подтверждённая работа и состояние задач. Данные обновляются
        каждые 5 секунд.
      </PageHeader>
      <ErrorBox message={error || nodeError} />
      <div className="metrics">
        <Metric
          label="Готовы к работе"
          value={summary?.ready_nodes ?? "—"}
          hint={summary ? summary.nodes + " устройств в пуле" : undefined}
        />
        <Metric
          label="Принято единиц работы"
          value={summary?.accepted_units ?? "—"}
        />
        <Metric
          label="Выполняются"
          value={summary?.jobs_by_state.running ?? "—"}
        />
        <Metric
          label="С ошибкой"
          value={summary?.jobs_by_state.failed ?? "—"}
        />
      </div>
      <div className="grid">
        <section className="card">
          <h2>Распределение задач</h2>
          {summary && Object.keys(summary.jobs_by_state).length ? (
            Object.entries(summary.jobs_by_state).map(([state, count]) => (
              <div className="chart-row" key={state}>
                <span>
                  {stateNames[state] || state}: {count}
                </span>
                <meter
                  min={0}
                  max={Math.max(1, ...Object.values(summary.jobs_by_state))}
                  value={count}
                />
                <span>{count}</span>
              </div>
            ))
          ) : (
            <Empty>Задач пока нет.</Empty>
          )}
          <Link to="/jobs">Все задачи →</Link>
        </section>
        <section className="card">
          <h2>Доступная вычислительная мощность</h2>
          <p>
            Вклад ограничен разрешениями участников и возможностями их
            устройств.
          </p>
          <dl>
            <dt>CPU предложено</dt>
            <dd>
              {nodes
                ? nodes
                    .filter((n) => n.state === "ready")
                    .reduce(
                      (v, n) =>
                        v + (n.capabilities?.offered_budget.cpu_millis || 0),
                      0,
                    ) / 1000
                : "—"}
            </dd>
            <dt>Память предложена</dt>
            <dd>
              {nodes
                ? bytes(
                    nodes
                      .filter((n) => n.state === "ready")
                      .reduce(
                        (v, n) =>
                          v +
                          (n.capabilities?.offered_budget.memory_bytes || 0),
                        0,
                      ),
                  )
                : "—"}
            </dd>
            <dt>GPU предложено</dt>
            <dd>
              {nodes
                ? nodes
                    .filter((n) => n.state === "ready")
                    .reduce(
                      (v, n) =>
                        v + (n.capabilities?.offered_budget.gpu_slots || 0),
                      0,
                    )
                : "—"}
            </dd>
            <dt>Время и стоимость</dt>
            <dd>Не измерены</dd>
          </dl>
        </section>
      </div>
      <section className="card">
        <h2>Устройства пула</h2>
        {nodes?.length === 0 && <Empty>Устройств пока нет.</Empty>}
        <div className="topology">
          {nodes?.map((n) => (
            <article className="node-tile" key={n.id}>
              <h3>{n.name}</h3>
              <Status value={n.state} />
              <p>
                {n.capabilities
                  ? `${n.capabilities.cpu_logical} CPU`
                  : "Проверка ещё не выполнена"}
              </p>
              <p>
                {n.active_attempts} активных задач · {n.accepted_units} принято
              </p>
              <small>Связь: {date(n.last_seen_at)}</small>
            </article>
          ))}
        </div>
        <div className="actions">
          <Link to="/nodes">Управлять устройствами →</Link>
        </div>
      </section>
    </>
  );
}
