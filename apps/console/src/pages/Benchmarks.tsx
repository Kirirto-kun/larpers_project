import { useState } from "react";
import { UtilizationChart } from "../components/MeasurementCharts";
import { useResource } from "../lib/hooks";
import { post, errorText } from "../lib/api";
import type { Benchmark, Node } from "../lib/types";
import {
  PageHeader,
  ErrorBox,
  Empty,
  Status,
  Metric,
} from "../components/Common";
import { Button } from "../components/ui/button";
export function Benchmarks() {
  const {
    data: reports,
    error,
    refresh,
  } = useResource<Benchmark[]>("/benchmarks");
  const { data: nodes } = useResource<Node[]>("/nodes");
  const [selected, setSelected] = useState("");
  const [previewId, setPreviewId] = useState("");
  const [revision, setRevision] = useState(1);
  const [ids, setIds] = useState<string[]>([]);
  const [concurrency, setConcurrency] = useState(1);
  const [repetitions, setRepetitions] = useState(3);
  const [warm, setWarm] = useState(true);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const report = reports?.find((b) => b.id === selected);
  return (
    <>
      <PageHeader eyebrow="ИЗМЕРЕНИЯ" title="Проверяйте пользу общего пула">
        Сравнение одинаковых задач и бюджетов на отдельных компьютерах и в пуле.
        Загрузка устройства не равна эффективности пула.
      </PageHeader>
      <ErrorBox message={error || message} />
      <details className="card">
        <summary>Новое измерение</summary>
        <p>
          Сначала подготовьте и проверьте план в рабочем пространстве. Выберите
          минимум два устройства. Для отдельных запусков нужны соответствующие
          записи клиента.
        </p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              const result = await post<Benchmark>("/benchmarks", {
                preview_id: previewId.trim(),
                plan_revision: revision,
                node_ids: ids,
                concurrency,
                repetitions,
                warm,
                batch_policy: {},
              });
              await refresh();
              setSelected(result.id);
              setMessage("");
            } catch (e) {
              setMessage(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="grid">
            <label>
              ID проверенного плана
              <input
                required
                value={previewId}
                onChange={(e) => setPreviewId(e.target.value)}
              />
            </label>
            <label>
              Версия плана
              <input
                type="number"
                min={1}
                value={revision}
                onChange={(e) => setRevision(+e.target.value)}
                required
              />
            </label>
            <label>
              Параллельных задач на устройство
              <input
                type="number"
                min={1}
                max={512}
                required
                value={concurrency}
                onChange={(e) => setConcurrency(+e.target.value)}
              />
            </label>
            <label>
              Повторений
              <input
                type="number"
                min={3}
                max={20}
                required
                value={repetitions}
                onChange={(e) => setRepetitions(+e.target.value)}
              />
            </label>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={warm}
              onChange={(e) => setWarm(e.target.checked)}
            />
            Прогретое окружение
          </label>
          <fieldset>
            <legend>Устройства</legend>
            {nodes?.map((n) => (
              <label className="check" key={n.id}>
                <input
                  type="checkbox"
                  checked={ids.includes(n.id)}
                  onChange={(e) =>
                    setIds(
                      e.target.checked
                        ? [...ids, n.id]
                        : ids.filter((id) => id !== n.id),
                    )
                  }
                />
                {n.name}
              </label>
            ))}
          </fieldset>
          <Button disabled={busy || ids.length < 2}>Создать измерение</Button>
        </form>
      </details>
      <section className="card">
        <h2>Сохранённые измерения</h2>
        {reports?.length === 0 && <Empty>Измерений пока нет</Empty>}
        {reports?.map((b) => (
          <div className="row" key={b.id}>
            <Button variant="ghost" onClick={() => setSelected(b.id)}>
              Измерение {b.id}
            </Button>
            <Status value={b.status} />
          </div>
        ))}
      </section>
      {report && (
        <section className="card">
          <h2>Измерение {report.id}</h2>
          <div className="metrics">
            <Metric
              label="Эффективность пула"
              value={
                report.efficiency_percent == null
                  ? "—"
                  : report.efficiency_percent.toFixed(1) + "%"
              }
            />
            <Metric
              label="Ускорение"
              value={
                report.speedup == null ? "—" : report.speedup.toFixed(2) + "×"
              }
            />
            <Metric
              label="Пул, ед. / сек."
              value={report.pooled_units_per_second?.toFixed(2) ?? "—"}
            />
            <Metric label="Повторений" value={report.config.repetitions} />
          </div>
          {report.efficiency_percent != null &&
            report.efficiency_percent > 100 && (
              <p className="notice">
                Эффективность выше 100%. Проверьте сопоставимость окружения,
                прогрева и измерений.
              </p>
            )}
          <h3>Производительность отдельных устройств</h3>
          {Object.entries(report.standalone_units_per_second).map(
            ([id, rate]) => (
              <div className="chart-row" key={id}>
                <span>
                  {nodes?.find((n) => n.id === id)?.name || id.slice(0, 8)}
                </span>
                <meter
                  min={0}
                  max={Math.max(
                    1,
                    ...Object.values(report.standalone_units_per_second),
                    report.pooled_units_per_second || 0,
                  )}
                  value={rate}
                />
                <span>{rate.toFixed(2)} ед./с</span>
              </div>
            ),
          )}
          <dl>
            <dt>Параллельность</dt>
            <dd>{report.config.concurrency}</dd>
            <dt>Окружение</dt>
            <dd>{report.config.warm ? "Прогретое" : "Холодный старт"}</dd>
            <dt>Манифест</dt>
            <dd>
              <code>{report.manifest_digest}</code>
            </dd>
            <dt>Конфигурация</dt>
            <dd>
              <code>{report.config_digest}</code>
            </dd>
          </dl>
          <h3>Исходные измерения и телеметрия</h3>
          {report.standalone_results.map((r, i) => (
            <details key={i}>
              <summary>
                {r.node_id} · {r.units} ед. / {r.duration_ms} мс
              </summary>
              <p>
                Вычисления: {r.duration_ms} мс · проверка: {r.verification_ms}{" "}
                мс
              </p>
              {r.utilization.length ? (
                <div>
                  <UtilizationChart samples={r.utilization} />
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Отсчёт</th>
                          <th>Фаза</th>
                          <th>CPU</th>
                          <th>GPU</th>
                        </tr>
                      </thead>
                      <tbody>
                        {r.utilization.map((u) => (
                          <tr key={u.sequence}>
                            <td>{u.sequence}</td>
                            <td>{u.phase}</td>
                            <td>
                              {u.cpu_percent == null
                                ? "Не измерено"
                                : u.cpu_percent + "%"}
                            </td>
                            <td>
                              {u.gpu_percent == null
                                ? "Не измерено"
                                : u.gpu_percent + "%"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <p>Телеметрия не записана.</p>
              )}
            </details>
          ))}
          <div className="actions">
            <a
              className="button outline"
              href={"/api/v1/benchmarks/" + report.id + "/export"}
              download
            >
              Скачать JSON
            </a>
          </div>
          <label>
            Импорт отдельного измерения (JSON)
            <input
              type="file"
              accept="application/json"
              disabled={busy}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                if (file.size > 10 * 1024 * 1024) {
                  setMessage("Выберите JSON до 10 МБ.");
                  return;
                }
                setBusy(true);
                try {
                  const payload = JSON.parse(await file.text());
                  await post(
                    "/benchmarks/" + report.id + "/standalone-results",
                    payload,
                  );
                  await refresh();
                  setMessage("");
                } catch (e) {
                  setMessage(errorText(e));
                } finally {
                  setBusy(false);
                }
              }}
            />
          </label>
        </section>
      )}
    </>
  );
}
