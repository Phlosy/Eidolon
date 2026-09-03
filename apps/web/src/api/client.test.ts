import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, describeApiError, post } from "./client";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("describeApiError", () => {
  it("keeps plain string details (our HTTPExceptions)", () => {
    expect(describeApiError({ detail: "Email already registered" }, 409)).toBe(
      "Email already registered",
    );
  });

  it("renders FastAPI 422 validation arrays with the offending field", () => {
    const body = {
      detail: [
        {
          type: "string_too_short",
          loc: ["body", "password"],
          msg: "String should have at least 12 characters",
          input: "Test1234!",
          ctx: { min_length: 12 },
        },
      ],
    };
    expect(describeApiError(body, 422)).toBe("password: String should have at least 12 characters");
  });

  it("joins several validation problems and nests field paths", () => {
    const body = {
      detail: [
        { loc: ["body", "email"], msg: "value is not a valid email address" },
        { loc: ["body", "participants", 0, "role"], msg: "field required" },
      ],
    };
    expect(describeApiError(body, 422)).toBe(
      "email: value is not a valid email address; participants.0.role: field required",
    );
  });

  it("falls back to the status line when the body says nothing usable", () => {
    expect(describeApiError(null, 500)).toBe("Request failed with status 500");
    expect(describeApiError({ detail: [] }, 422)).toBe("Request failed with status 422");
    expect(describeApiError({ detail: "   " }, 422)).toBe("Request failed with status 422");
    expect(describeApiError({ other: true }, 400)).toBe("Request failed with status 400");
  });
});

describe("request error surfacing", () => {
  it("throws ApiError carrying the server reason instead of a bare status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            detail: [
              {
                type: "string_too_short",
                loc: ["body", "password"],
                msg: "String should have at least 12 characters",
              },
            ],
          },
          { status: 422 },
        ),
      ),
    );

    await expect(
      post("/auth/register", { email: "a@b.co", password: "Test1234!" }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      message: "password: String should have at least 12 characters",
    } satisfies Partial<ApiError>);
  });

  it("stays readable when the server answers with a non-JSON body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })),
    );

    await expect(post("/auth/register", {})).rejects.toThrowError(
      new ApiError(502, "Request failed with status 502"),
    );
  });
});
