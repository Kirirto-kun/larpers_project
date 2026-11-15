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
    throw new Error(
      "Cannot reach the server. Check your connection and try again.",
    );
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
  UNAUTHORIZED: "Could not sign in. Check your email and password.",
  FORBIDDEN: "You do not have permission to do this.",
  EMAIL_IN_USE: "This email is already in use. Sign in to your account.",
  ACCOUNT_EXISTS: "This account already has a password. Sign in with it.",
  PLANNER_UNAVAILABLE:
    "The planner is temporarily unavailable. Your files are saved. Try again.",
  RATE_LIMITED: "Too many requests. Try again shortly.",
  STALE_PREVIEW:
    "The plan has changed. Review the updated plan before starting.",
  CAPACITY_UNAVAILABLE:
    "No suitable device is available. Connect a device or try again later.",
  QUOTA_EXCEEDED:
    "Storage limit reached. Reduce the file size or contact the owner.",
};
export function errorText(e: unknown) {
  if (e instanceof ApiError)
    return (
      (messages[e.code] || e.message) +
      (e.requestId ? " · Request ID: " + e.requestId : "")
    );
  return e instanceof Error
    ? e.message
    : "The action could not be completed. Try again.";
}
export function artifactUrl(id: string) {
  return "/api/v1/artifacts/" + encodeURIComponent(id) + "/content";
}
