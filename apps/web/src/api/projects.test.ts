import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { getProjectSpec } from "./projects";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 200 })),
  );
});
afterEach(() => vi.unstubAllGlobals());

it("preserves the default project spec URL", async () => {
  await getProjectSpec(17);
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/v1/projects/17/spec");
});

it("passes the dispatch cursor and page size to the shared project read", async () => {
  await getProjectSpec(17, { dispatchBeforeId: 42, dispatchLimit: 10 });
  const url = new URL(String(vi.mocked(fetch).mock.calls[0][0]), "http://localhost");
  expect(url.searchParams.get("dispatch_before_id")).toBe("42");
  expect(url.searchParams.get("dispatch_limit")).toBe("10");
});
