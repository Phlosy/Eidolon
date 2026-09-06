/**
 * Typed fetch client for the Eidolon backend.
 * All API access goes through here. By default requests use same-origin
 * relative paths (the dev server / nginx proxy `/api` and `/ws` to the
 * backend — see docs/ports.md). `import.meta.env.VITE_API_BASE_URL` is an
 * optional escape hatch for talking to a different origin; when empty or
 * unset the same-origin default applies.
 */

export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

const API_PREFIX = "/api/v1";

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

/**
 * Turn an error body into a human-readable message. FastAPI returns either
 * `{detail: string}` (our HTTPExceptions) or `{detail: [...]}` (Pydantic
 * validation errors, e.g. 422). Only the first shape used to be handled, so a
 * 422 surfaced as the useless "Request failed with status 422".
 */
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
        // loc[0] is the request location ("body" | "query" | "path" | ...), not a field name.
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

  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }

  return fallback;
}

/**
 * CSRF 头只有一个来源。任何自己调 fetch 的模块（例如 drive.ts 的原始请求）
 * 都必须复用它：漏掉 X-CSRF-Token 的后果是后端直接 403，
 * 用户看到的是"上传失败"，而且只在写操作出现（实测：Drive 上传被教程
 * cloud_docs 这一步撞出来）。
 */
export function authHeaders(): Record<string, string> {
  const csrf = document.cookie
    .split("; ")
    .find((value) => value.startsWith("eidolon_csrf="))
    ?.split("=")[1];
  return csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Eidolon API");
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // non-JSON error body; fall back to the generic message below
    }
    throw new ApiError(response.status, describeApiError(body, response.status));
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function patch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

/**
 * 二进制直传（如头像）：body 就是文件本身，Content-Type 用文件类型，
 * 不引入 multipart。CSRF 头由 request() 统一带上。
 */
export function postFile<T>(path: string, file: Blob, contentType: string): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: file,
    headers: { "Content-Type": contentType || "application/octet-stream" },
  });
}

export function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

/** WebSocket URL for `path`: same-origin (ws/wss from window.location) unless VITE_API_BASE_URL is set. */
export function wsUrl(path: string): string {
  if (API_BASE_URL) {
    return `${API_BASE_URL.replace(/^http/, "ws")}${path}`;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}
