/**
 * Typed fetch client for the Eidolon v2 API (`/api/v2`, see
 * apps/server/eidolon/api). Same-origin by default: the dev server proxies `/api`
 * to the API process. `VITE_API_BASE_URL` is an escape hatch for another origin.
 *
 * Sessions are an HttpOnly cookie; every mutating request must echo the readable
 * `eidolon_csrf` cookie in `X-CSRF-Token` (double-submit), which `request` does.
 */

export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
export const API_PREFIX = "/api/v2";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** FastAPI error bodies: `{detail: string}` or `{detail: [{loc, msg}]}` (422). */
export function describeApiError(body: unknown, status: number): string {
  const fallback = `Request failed with status ${status}`;
  if (!body || typeof body !== "object") return fallback;
  const { detail } = body as { detail?: unknown };
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return null;
        const entry = item as { loc?: unknown; msg?: unknown };
        const parts = Array.isArray(entry.loc) ? [...entry.loc] : [];
        if (
          typeof parts[0] === "string" &&
          ["body", "query", "path", "header", "cookie"].includes(parts[0])
        )
          parts.shift();
        const field = parts.map((part) => String(part)).join(".");
        const message = typeof entry.msg === "string" ? entry.msg : null;
        if (!message) return null;
        return field ? `${field}: ${message}` : message;
      })
      .filter((value): value is string => Boolean(value));
    if (messages.length > 0) return messages.join("; ");
  }
  return fallback;
}

export function csrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const entry = document.cookie.split("; ").find((value) => value.startsWith("eidolon_csrf="));
  return entry ? decodeURIComponent(entry.slice("eidolon_csrf=".length)) : null;
}

export function authHeaders(): Record<string, string> {
  const csrf = csrfToken();
  return csrf ? { "X-CSRF-Token": csrf } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...authHeaders(), ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Eidolon API");
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // non-JSON error body
    }
    throw new ApiError(response.status, describeApiError(body, response.status));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function post<T>(path: string, body?: unknown, idempotencyKey?: string): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
  });
}

export function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

/** Raw bytes (artifact content). */
export async function getBlob(path: string): Promise<{ blob: Blob; type: string }> {
  const response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, { credentials: "include" });
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // ignore
    }
    throw new ApiError(response.status, describeApiError(body, response.status));
  }
  return { blob: await response.blob(), type: response.headers.get("content-type") ?? "" };
}
