import { describe, expect, it } from "vitest";
import { resolveEmployeeDepth, resolveMovementDirection } from "./employee-presentation";

describe("resolveMovementDirection", () => {
  it("uses a stable back view while moving upward", () => {
    expect(resolveMovementDirection({ x: 5, y: 8 }, { x: 5, y: 7 }, "left")).toBe("up");
  });

  it("uses a stable front view while moving downward", () => {
    expect(resolveMovementDirection({ x: 5, y: 7 }, { x: 5, y: 8 }, "right")).toBe("down");
  });

  it("preserves the previous direction when there is no movement", () => {
    expect(resolveMovementDirection({ x: 5, y: 7 }, { x: 5, y: 7 }, "up")).toBe("up");
  });
});

describe("resolveEmployeeDepth", () => {
  it("places a working employee behind the workstation foreground", () => {
    expect(resolveEmployeeDepth(240, -48)).toBe(192);
  });

  it("uses the walking baseline when no interaction depth is supplied", () => {
    expect(resolveEmployeeDepth(240)).toBe(270);
  });
});
