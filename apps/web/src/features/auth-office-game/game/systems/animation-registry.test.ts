import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { registerEmployeeAnimations, resolveEmployeeAnimation } from "./animation-registry";

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

  it("maps the expanded walk cycles without crossing employee rows", () => {
    const generateFrameNumbers = vi.fn((_texture: string, frames: object) => frames);
    const create = vi.fn();
    const scene = {
      anims: { exists: vi.fn(() => false), generateFrameNumbers, create },
    } as never;

    registerEmployeeAnimations(scene);

    expect(generateFrameNumbers).toHaveBeenCalledWith("employees", { start: 12, end: 21 });
    expect(generateFrameNumbers).toHaveBeenCalledWith("employees", { start: 68, end: 75 });
    expect(generateFrameNumbers).toHaveBeenCalledWith("employees", { start: 168, end: 175 });
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ key: "employee-3.walk.side", frameRate: 10, repeat: -1 }),
    );
  });
});
