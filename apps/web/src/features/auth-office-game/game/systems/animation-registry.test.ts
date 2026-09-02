import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { resolveEmployeeAnimation } from "./animation-registry";

vi.mock("phaser", () => ({ default: {} }));

describe("resolveEmployeeAnimation", () => {
  it("keeps vertical movement on its matching front or back view", () => {
    expect(resolveEmployeeAnimation("employee-2", "walk", "up")).toBe("employee-2.walk.up");
    expect(resolveEmployeeAnimation("employee-2", "walk", "down")).toBe("employee-2.walk.down");
  });

  it("uses the side view for both horizontal directions and falls back safely", () => {
    expect(resolveEmployeeAnimation("employee-2", "walk", "left")).toBe("employee-2.walk.side");
    expect(resolveEmployeeAnimation("employee-2", "unknown", "unknown")).toBe(
      "employee-2.idle.down",
    );
  });
});
