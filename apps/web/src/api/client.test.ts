import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, authHeaders, describeApiError, get, post } from "./client";

describe("describeApiError", () => {
  it("reads FastAPI string details and pydantic lists", () => {
    expect(describeApiError({ detail: "not signed in" }, 401)).toBe("not signed in");
    expect(
      describeApiError(
        { detail: [{ loc: ["body", "email"], msg: "value is not a valid email" }] },
        422,
      ),
    ).toBe("email: value is not a valid email");
    expect(describeApiError(null, 500)).toBe("Request failed with status 500");
  });
});

describe("request", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "eidolon_csrf=; max-age=0";
  });

  it("prefixes /api/v2, sends cookies and echoes the CSRF cookie on mutations", async () => {
    document.cookie = "eidolon_csrf=tok%2F1";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await post("/auth/logout", undefined, "idem-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v2/auth/logout");
    expect(init.credentials).toBe("include");
    expect(init.headers).toMatchObject({ "X-CSRF-Token": "tok/1", "Idempotency-Key": "idem-1" });
    expect(authHeaders()).toEqual({ "X-CSRF-Token": "tok/1" });
  });

  it("turns error bodies into ApiError and 204 into undefined", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "you are not employed by this company" }), {
        status: 403,
      }),
    );
    await expect(get("/companies/x")).rejects.toMatchObject({
      status: 403,
      detail: "you are not employed by this company",
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(post("/auth/logout")).resolves.toBeUndefined();
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("offline"));
    await expect(get("/auth/me")).rejects.toBeInstanceOf(ApiError);
  });
});
