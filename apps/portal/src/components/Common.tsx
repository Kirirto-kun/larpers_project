import type { ReactNode } from "react";
export const stateNames: Record<string, string> = {
  ready: "Ready",
  preflight: "Checking device",
  offline: "Offline",
  paused: "Paused",
  draining: "Finishing work",
  quarantined: "Blocked",
  running: "Running",
  queued: "Queued",
  waiting_for_capacity: "Waiting for a device",
  succeeded: "Completed",
  partial: "Partial result",
  failed: "Failed",
  cancelled: "Cancelled",
  inspecting: "Inspecting files",
  planning: "Planning",
  needs_input: "Needs your answer",
  validating: "Validating plan",
  leased: "Assigned",
  verifying: "Verifying result",
  retry_wait: "Retrying",
  collecting: "Collecting measurements",
  complete: "Completed",
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
    ? (n / 1073741824).toFixed(1) + " GB"
    : n >= 1048576
      ? (n / 1048576).toFixed(1) + " MB"
      : n.toLocaleString("en-US") + " B";
}
export function date(value: string | null) {
  return value ? new Date(value).toLocaleString("en-US") : "Never connected";
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
