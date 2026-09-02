import { describe, expect, it } from "vitest";
import { findGridPath } from "./navigation-system";

describe("findGridPath", () => {
  it("routes around blocked tiles and reaches the target", () => {
    const path = findGridPath(
      { x: 1, y: 1 },
      { x: 4, y: 1 },
      { width: 6, height: 4, blocked: new Set(["2,1", "3,1"]) },
    );
    expect(path.at(0)).toEqual({ x: 1, y: 1 });
    expect(path.at(-1)).toEqual({ x: 4, y: 1 });
    expect(path).not.toContainEqual({ x: 2, y: 1 });
  });

  it("returns an empty path when the target is unreachable", () => {
    expect(
      findGridPath(
        { x: 0, y: 0 },
        { x: 1, y: 1 },
        { width: 2, height: 2, blocked: new Set(["1,0", "0,1"]) },
      ),
    ).toEqual([]);
  });
});
