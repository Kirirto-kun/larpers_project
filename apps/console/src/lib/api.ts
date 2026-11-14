export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public requestId?: string,
    public fields?: unknown,
  ) {
    super(message);
  }
}
let csrf = "";
export function setCsrf(token: string) {
  csrf = token;
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof Blob))
    headers.set("Content-Type", "application/json");
  if (init.method && !["GET", "HEAD"].includes(init.method) && csrf)
    headers.set("X-CSRF-Token", csrf);
  let res: Response;
  try {
    res = await fetch("/api/v1" + path, {
      ...init,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new Error("Нет связи с сервером. Проверьте подключение и повторите.");
  }
  if (!res.ok) {
    if (res.status === 401)
      window.dispatchEvent(new Event("campus:unauthorized"));
    const e = await res.json().catch(() => ({}));
    throw new ApiError(
      e.code || String(res.status),
      e.message || res.statusText,
      e.request_id,
      e.field_errors,
    );
  }
  return res.status === 204 ? (undefined as T) : res.json();
}
export function post<T>(path: string, body: unknown, headers?: HeadersInit) {
  return api<T>(path, { method: "POST", body: JSON.stringify(body), headers });
}
const messages: Record<string, string> = {
  UNAUTHORIZED: "Не удалось войти. Проверьте почту и пароль.",
  FORBIDDEN: "Недостаточно прав для этого действия.",
  EMAIL_IN_USE: "Эта почта уже используется. Войдите в аккаунт.",
  ACCOUNT_EXISTS: "Пароль для аккаунта уже задан. Войдите с ним.",
  PLANNER_UNAVAILABLE:
    "Планировщик временно недоступен. Файлы сохранены: попробуйте снова.",
  RATE_LIMITED: "Слишком много запросов. Подождите немного.",
  STALE_PREVIEW: "План изменился. Проверьте обновлённый план перед запуском.",
  CAPACITY_UNAVAILABLE:
    "Пока нет подходящего устройства. Подключите компьютер или подождите.",
  QUOTA_EXCEEDED:
    "Превышен доступный объём. Уменьшите файлы или обратитесь к владельцу.",
};
export function errorText(e: unknown) {
  if (e instanceof ApiError)
    return (
      (messages[e.code] || e.message) +
      (e.requestId ? " · Код обращения: " + e.requestId : "")
    );
  return e instanceof Error
    ? e.message
    : "Не удалось выполнить действие. Повторите попытку.";
}
export function artifactUrl(id: string) {
  return "/api/v1/artifacts/" + encodeURIComponent(id) + "/content";
}
