import { useState } from "react";
import { useResource } from "../lib/hooks";
import type { Node } from "../lib/types";
import { post, api, errorText } from "../lib/api";
import {
  PageHeader,
  Status,
  ErrorBox,
  Empty,
  bytes,
  date,
} from "../components/Common";
import { Button } from "../components/ui/button";
export function Nodes() {
  const { data, error, refresh } = useResource<Node[]>("/nodes");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  async function control(n: Node, action: string) {
    setBusy(n.id);
    try {
      await post("/nodes/" + n.id + "/control", { action });
      await refresh();
      setMessage("");
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy("");
    }
  }
  return (
    <>
      <PageHeader eyebrow="УСТРОЙСТВА" title="Вычислительные ресурсы пула">
        Состояние поступает от клиентов. Команда из браузера не запускает
        выключенное приложение.
      </PageHeader>
      <ErrorBox message={error || message} />
      {data?.length === 0 && <Empty>Устройств пока нет.</Empty>}
      {data?.map((n) => (
        <article className="card" key={n.id}>
          <div className="row">
            <h2>{n.name}</h2>
            <Status value={n.state} />
          </div>
          <div className="grid">
            <div>
              <h3>Оборудование и доступность</h3>
              {n.capabilities ? (
                <>
                  <p>
                    {n.capabilities.os} / {n.capabilities.arch} ·{" "}
                    {n.capabilities.cpu_logical} CPU ·{" "}
                    {bytes(n.capabilities.memory_available_bytes)} памяти
                  </p>
                  <p>
                    Docker {n.capabilities.runtime.version} ·{" "}
                    {n.capabilities.runtime.healthy
                      ? "работает"
                      : "требует внимания"}
                  </p>
                  <p>
                    Источник питания:{" "}
                    {n.capabilities.power_state === "ac"
                      ? "сеть"
                      : n.capabilities.power_state === "battery"
                        ? "батарея"
                        : "неизвестен"}
                  </p>
                  {n.capabilities.gpus.map((g) => (
                    <p key={g.id}>
                      <b>{g.vendor}</b> · {bytes(g.vram_bytes)} · CUDA{" "}
                      {g.cuda_verified ? "проверена" : "не подтверждена"}
                    </p>
                  ))}
                  <p>
                    Предоставлено:{" "}
                    {n.capabilities.offered_budget.cpu_millis / 1000} CPU ·{" "}
                    {bytes(n.capabilities.offered_budget.memory_bytes)} ·{" "}
                    {n.capabilities.offered_budget.gpu_slots} GPU
                  </p>
                </>
              ) : (
                <p>Клиент ещё не передал характеристики.</p>
              )}
              <p className="muted">
                Загрузка CPU/GPU: не измерена в этом представлении.
              </p>
            </div>
            <div>
              <h3>Участие</h3>
              <p>
                {n.active_attempts} активных задач · {n.accepted_units} принято
              </p>
              <p>Последняя связь: {date(n.last_seen_at)}</p>
              <p>
                Владелец: <code>{n.owner_id}</code>
              </p>
              {n.supports_web_controls ? (
                <>
                  <p className="notice">
                    {n.applied_revision === n.settings.revision
                      ? "Настройки применены клиентом"
                      : "Ожидает применения на компьютере"}
                  </p>
                  <label>
                    Режим нагрузки
                    <select
                      value={n.settings.mode}
                      disabled={!!busy}
                      onChange={async (e) => {
                        setBusy(n.id);
                        try {
                          await api("/nodes/" + n.id + "/settings", {
                            method: "PATCH",
                            body: JSON.stringify({ mode: e.target.value }),
                          });
                          await refresh();
                          setMessage("");
                        } catch (e) {
                          setMessage(errorText(e));
                        } finally {
                          setBusy("");
                        }
                      }}
                    >
                      <option value="low">Лёгкий · 10%</option>
                      <option value="medium">Средний · 50%</option>
                      <option value="max">Максимальный · 90%</option>
                    </select>
                  </label>
                </>
              ) : (
                <p className="notice">
                  Для удалённых настроек требуется обновление клиента.
                </p>
              )}
              {n.settings_error && <ErrorBox message={n.settings_error} />}
            </div>
          </div>
          <div className="actions">
            {[
              { id: "drain", name: "Завершить текущую работу" },
              { id: "stop", name: "Остановить" },
              { id: "resume", name: "Продолжить" },
              {
                id: n.state === "quarantined" ? "release" : "quarantine",
                name:
                  n.state === "quarantined"
                    ? "Снять блокировку"
                    : "Заблокировать",
              },
            ].map((a) => (
              <Button
                key={a.id}
                variant="outline"
                disabled={!!busy}
                onClick={() => void control(n, a.id)}
              >
                {a.name}
              </Button>
            ))}
          </div>
        </article>
      ))}
    </>
  );
}
