export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public requestId?: string,
    public fields?: Record<string, string>,
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
  const res = await fetch("/api/v1" + path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!res.ok) {
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
export function errorText(e: unknown) {
  return e instanceof ApiError
    ? `${e.message} (${e.code}${e.requestId ? "; request " + e.requestId : ""})`
    : e instanceof Error
      ? e.message
      : "Something went wrong. Please retry.";
}
export function artifactUrl(id: string) {
  return "/api/v1/artifacts/" + encodeURIComponent(id) + "/content";
}
