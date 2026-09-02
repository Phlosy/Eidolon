import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { resolveEmployeeAnimation } from "./animation-registry";

vi.mock("phaser", () => ({ default: {} }));

describe("resolveEmployeeAnimation", () => {
  it("returns core animations and a stable fallback", () => {
    expect(resolveEmployeeAnimation("employee-2", "walk")).toBe("employee-2.walk");
    expect(resolveEmployeeAnimation("employee-2", "unknown")).toBe("employee-2.idle");
  });
});
