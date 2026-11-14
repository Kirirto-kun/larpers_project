import { useState } from "react";
import { Monitor, Download, Copy } from "lucide-react";
import { useResource } from "../lib/hooks";
import { api, post, errorText } from "../lib/api";
import type {
  Node,
  Mode,
  DesiredState,
  Release,
  Invitation,
} from "../lib/types";
import { Button } from "../components/ui/button";
import {
  PageHeader,
  ErrorBox,
  Status,
  bytes,
  Empty,
  date,
} from "../components/Common";
const modes: { id: Mode; name: string; percent: number; detail: string }[] = [
  {
    id: "low",
    name: "Лёгкий",
    percent: 10,
    detail: "Для работы за компьютером",
  },
  {
    id: "medium",
    name: "Средний",
    percent: 50,
    detail: "Баланс скорости и комфорта",
  },
  {
    id: "max",
    name: "Максимальный",
    percent: 90,
    detail: "Когда компьютер свободен и подключён к питанию",
  },
];
export function DeviceCard({
  node,
  refresh,
  admin = false,
}: {
  node: Node;
  refresh: () => Promise<void>;
  admin?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const cap = node.capabilities;
  const pending = node.applied_revision !== node.settings.revision;
  async function change(body: { mode?: Mode; desired_state?: DesiredState }) {
    setBusy(true);
    try {
      await api("/nodes/" + node.id + "/settings", {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      await refresh();
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function legacy(action: string) {
    setBusy(true);
    try {
      await post("/nodes/" + node.id + "/control", { action });
      await refresh();
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="card device-card">
      <div className="row">
        <div className="device-title">
          <Monitor aria-hidden="true" />
          <h3>{node.name}</h3>
        </div>
        <Status value={node.state} />
      </div>
      <p className="muted">
        {cap
          ? `${cap.os === "darwin" ? "macOS" : cap.os} · ${cap.cpu_logical} CPU · ${bytes(cap.memory_available_bytes)} доступно`
          : "Характеристики появятся после подключения"}
      </p>
      {cap && (
        <p className="muted">
          Выделено: {(cap.offered_budget.cpu_millis / 1000).toFixed(1)} CPU ·{" "}
          {bytes(cap.offered_budget.memory_bytes)} памяти · GPU:{" "}
          {cap.offered_budget.gpu_slots > 0
            ? "разрешён локально"
            : "не предоставлен"}
        </p>
      )}
      <div className="device-meta">
        <span>{node.active_attempts} активных задач</span>
        <span>{node.accepted_units} принято</span>
      </div>
      <p className="muted">Последняя связь: {date(node.last_seen_at)}</p>
      {node.supports_web_controls ? (
        <>
          <p
            className={"notice " + (node.settings_error ? "error" : "")}
            role="status"
          >
            {node.settings_error
              ? "Не удалось применить: " + node.settings_error
              : pending
                ? "Ожидает применения на компьютере"
                : "Настройки применены клиентом"}
            {pending && node.state === "offline"
              ? " — откройте Campus на этом компьютере."
              : ""}
          </p>
          <fieldset disabled={busy}>
            <legend>Нагрузка CPU и памяти</legend>
            <div className="mode-options">
              {modes.map((mode) => (
                <label
                  className={
                    "mode " + (node.settings.mode === mode.id ? "selected" : "")
                  }
                  key={mode.id}
                >
                  <input
                    type="radio"
                    name={"mode-" + node.id}
                    checked={node.settings.mode === mode.id}
                    onChange={() => void change({ mode: mode.id })}
                  />
                  <strong>{mode.percent}%</strong>
                  <span>{mode.name}</span>
                  <small>{mode.detail}</small>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="actions">
            <Button
              disabled={busy || node.state === "quarantined"}
              onClick={() => void change({ desired_state: "running" })}
            >
              Подключить / продолжить
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void change({ desired_state: "paused" })}
            >
              Пауза
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void change({ desired_state: "draining" })}
            >
              Завершить текущую работу
            </Button>
          </div>
          <p className="muted">
            Команда выполнится, когда запущенный клиент свяжется с сервером.
            Браузер не может открыть приложение на компьютере.
          </p>
        </>
      ) : (
        <>
          <div className="notice">
            Обновите клиент для настройки нагрузки и управления из браузера.
            Существующее подключение продолжает работать.
          </div>
          <div className="actions">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void legacy("drain")}
            >
              Завершить текущую работу
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void legacy("stop")}
            >
              Остановить
            </Button>
          </div>
        </>
      )}
      {admin && (
        <div className="actions">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              void legacy(
                node.state === "quarantined" ? "release" : "quarantine",
              )
            }
          >
            {node.state === "quarantined"
              ? "Снять блокировку"
              : "Заблокировать"}
          </Button>
        </div>
      )}
      <ErrorBox message={error} />
    </article>
  );
}
export function Contribute() {
  const { data: nodes, error, refresh } = useResource<Node[]>("/nodes");
  const {
    data: release,
    error: releaseError,
    refresh: refreshRelease,
  } = useResource<Release>("/client-release");
  const [name, setName] = useState("Мой компьютер");
  const [invitation, setInvitation] = useState<Invitation>();
  const [busy, setBusy] = useState(false);
  const [actionError, setError] = useState("");
  const [copy, setCopy] = useState("");
  const [macCopy, setMacCopy] = useState("");
  return (
    <>
      <PageHeader eyebrow="УСТРОЙСТВА" title="Подключите свободные ресурсы">
        Участие можно приостановить в любой момент. Вы управляете нагрузкой, а
        Campus распределяет задачи.
      </PageHeader>
      <div className="grid">
        <section className="card">
          <p className="eyebrow">ПОДКЛЮЧЕНИЕ ЗА ТРИ ШАГА</p>
          <h2>Добавить компьютер</h2>
          <div className="step">
            <b>1</b>
            <div>
              <h3>Скачайте Campus</h3>
              <p className="muted">
                Небольшой файл запуска для вашего компьютера.
              </p>
              <div className="actions">
                {release?.downloads.map((d) => (
                  <a
                    className="button outline"
                    href={d.url}
                    key={d.os}
                    download
                  >
                    <Download aria-hidden="true" size={16} />
                    {d.label}
                  </a>
                ))}
              </div>
              {releaseError && (
                <>
                  <ErrorBox message={releaseError} />
                  <Button
                    variant="outline"
                    onClick={() => void refreshRelease()}
                  >
                    Повторить загрузку ссылок
                  </Button>
                </>
              )}
            </div>
          </div>
          <div className="step">
            <b>2</b>
            <div>
              <h3>Откройте скачанный файл</h3>
              <p>
                Следуйте подсказкам приложения. На Windows и macOS сначала
                установите и запустите Docker Desktop вручную. На Linux нужен
                работающий Docker Engine.
              </p>
            </div>
          </div>
          <details>
            <summary>macOS: если файл не открывается</summary>
            <p>
              Откройте приложение «Терминал» и вставьте команду для скачанного
              файла:
            </p>
            <pre>sh ~/Downloads/Connect.command</pre>
            <Button
              variant="outline"
              onClick={async () => {
                try {
                  if (!navigator.clipboard) throw new Error();
                  await navigator.clipboard.writeText(
                    "sh ~/Downloads/Connect.command",
                  );
                  setMacCopy("Скопировано");
                } catch {
                  setMacCopy("Выделите команду и скопируйте вручную.");
                }
              }}
            >
              Скопировать команду macOS
            </Button>
            <p role="status">{macCopy}</p>
          </details>
          <div className="step">
            <b>3</b>
            <div>
              <h3>Вставьте приватное приглашение</h3>
              <p>Создайте его справа и вставьте в открытый клиент.</p>
            </div>
          </div>
        </section>
        <section className="card">
          <h2>Приглашение для вашего компьютера</h2>
          <p className="muted">
            Одноразовое приглашение действует 30 минут. Передавайте его только
            своему устройству.
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setCopy("");
              try {
                setInvitation(
                  await post<Invitation>("/invitations", {
                    kind: "device",
                    name: name.trim(),
                    ttl_seconds: 1800,
                  }),
                );
                setError("");
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <label>
              Название компьютера
              <input
                value={name}
                maxLength={256}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </label>
            <Button disabled={busy || !name.trim()}>
              {busy ? "Создаём…" : "Создать приглашение"}
            </Button>
          </form>
          {invitation && (
            <div className="invite-result">
              <label>
                Приватное приглашение
                <input
                  readOnly
                  value={invitation.token}
                  onFocus={(e) => e.target.select()}
                />
              </label>
              <Button
                variant="outline"
                onClick={async () => {
                  try {
                    if (!navigator.clipboard) throw new Error();
                    await navigator.clipboard.writeText(invitation.token);
                    setCopy("Скопировано");
                  } catch {
                    setCopy("Выделите приглашение выше и скопируйте вручную.");
                  }
                }}
              >
                <Copy aria-hidden="true" size={16} />
                Скопировать
              </Button>
              <p role="status">{copy}</p>
              <small>Действует до {date(invitation.expires_at)}</small>
            </div>
          )}
          <ErrorBox message={actionError} />
          <div className="notice">
            GPU включается только с вашего разрешения в клиенте. Режим нагрузки
            не меняет это разрешение и не разрешает работу от батареи.
          </div>
          <p className="muted">
            10 / 50 / 90% — доля локально доступных CPU и памяти, ограниченная
            возможностями Docker и настройками владельца.
          </p>
        </section>
      </div>
      <section>
        <div className="row">
          <h2>Подключённые устройства</h2>
          <Button variant="ghost" onClick={() => void refresh()}>
            Обновить
          </Button>
        </div>
        <ErrorBox message={error} />
        {!nodes && !error && <p role="status">Загружаем устройства…</p>}
        {nodes?.length === 0 && (
          <Empty>
            <Monitor aria-hidden="true" />
            <h3>Пока нет подключённых устройств</h3>
            <p>
              Скачайте Campus и добавьте первый компьютер по инструкции выше.
            </p>
          </Empty>
        )}
        {nodes?.map((node) => (
          <DeviceCard key={node.id} node={node} refresh={refresh} />
        ))}
      </section>
    </>
  );
}
