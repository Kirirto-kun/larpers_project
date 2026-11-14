import type { ReactNode } from "react";
export const stateNames: Record<string, string> = {
  ready: "Готово",
  preflight: "Проверка компьютера",
  offline: "Не в сети",
  paused: "На паузе",
  draining: "Завершает работу",
  quarantined: "Заблокировано",
  running: "Выполняется",
  queued: "В очереди",
  waiting_for_capacity: "Ожидает устройства",
  succeeded: "Завершено",
  partial: "Частичный результат",
  failed: "Ошибка",
  cancelled: "Отменено",
  inspecting: "Проверяем файлы",
  planning: "Готовим план",
  needs_input: "Нужен ваш ответ",
  validating: "Проверяем план",
  leased: "Назначено",
  verifying: "Проверка результата",
  retry_wait: "Повторная попытка",
  collecting: "Сбор измерений",
  complete: "Завершено",
};
export function Status({ value }: { value: string }) {
  return (
    <span className={"status " + value}>{stateNames[value] || value}</span>
  );
}
export function ErrorBox({ message }: { message: string }) {
  return message ? (
    <div className="notice error" role="alert">
      {message}
    </div>
  ) : null;
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
export function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="metric">
      <p>{label}</p>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </div>
  );
}
export function bytes(n: number) {
  return n >= 1073741824
    ? (n / 1073741824).toFixed(1) + " ГБ"
    : n >= 1048576
      ? (n / 1048576).toFixed(1) + " МБ"
      : n.toLocaleString("ru-RU") + " Б";
}
export function date(value: string | null) {
  return value ? new Date(value).toLocaleString("ru-RU") : "Ещё не подключался";
}
export function PageHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      {children && <p>{children}</p>}
    </header>
  );
}
