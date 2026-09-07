import { describe, expect, it } from "vitest";
import { FIT_STATUSES, QUALIFICATION_STATUSES, type FitStatus } from "./index";

describe("Position fit enum contract", () => {
  it("fit statuses mirror the backend", () => {
    expect([...FIT_STATUSES]).toEqual([
      "NOT_EVALUABLE",
      "INSUFFICIENT_DATA",
      "EVALUABLE",
      "STRONG_MATCH",
      "PARTIAL_MATCH",
      "WEAK_MATCH",
      "CRITICAL_GAP",
    ]);
  });
  it("qualification statuses mirror the backend", () => {
    expect([...QUALIFICATION_STATUSES]).toEqual([
      "QUALIFIED",
      "QUALIFIED_WITH_GAPS",
      "NOT_QUALIFIED",
      "INSUFFICIENT_DATA",
    ]);
  });
  it("rejects unknown fit status at compile time", () => {
    const value: FitStatus = "STRONG_MATCH";
    expect(value).toBe("STRONG_MATCH");
    // @ts-expect-error — 后端没有 "PERFECT"; 放开会因 unused directive 变红
    const bad: FitStatus = "PERFECT";
    expect(bad).toBeDefined();
  });
});
