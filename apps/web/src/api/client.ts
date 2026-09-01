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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Eidolon API");
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const body: unknown = await response.json();
      if (
        body &&
        typeof body === "object" &&
        "detail" in body &&
        typeof (body as { detail: unknown }).detail === "string"
      ) {
        detail = (body as { detail: string }).detail;
      }
    } catch {
      // keep the generic detail
    }
    throw new ApiError(response.status, detail);
  }

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
